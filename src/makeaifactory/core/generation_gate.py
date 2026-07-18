"""GenerationGate — プロセス内 admission guard + bot_state 単一書き手化。

生成の入口は Desktop単発 / バッチ / Discord / Remote Room / Local Bridge の
5系統あるが、従来はプロセス内の排他制御が一切なく、`bot_state.json`
(core.bot_state の write_bot_state/read_bot_state) が advisory な目印として
各所からバラバラに書かれているだけだった。このため次のような競合が起き得た。

- "single" 同士が同時に投入され、ComfyUI へ二重にジョブを積んでしまう。
- 先に完了した経路の "idle" 書き込みが、まだ実行中の別経路の状態を
  上書き（clobber）してしまう。
- Discord の keep-alive (read-modify-write) が、メインスレッドが書いた
  最新の状態を古い値で上書きしてしまう。
- デキュー時の状態確認 (read) と実際の queue_prompt 呼び出しの間に
  TOCTOU (Time-Of-Check to Time-Of-Use) の隙がある。
- バッチ中に Discord 割り込みが "single" を書く隙に、Remote Room が
  すり抜けて同時実行してしまう。
- 修復ガード (can_start_repair) も read → 判定の間に TOCTOU がある。

GenerationGate はこれらをプロセス内で共有する単一の排他ロックとして解消する。
生成パイプライン本体 (JobController 等) には一切手を入れず、各入口の
「開始前に取得し、終了時に必ず解放する」という利用側の責務にとどめる。

bot_state.json への書き込みは、runtime_root が指定されている場合に限り
GenerationGate が単独の書き手として行う（PR1 完了後は各所の
write_bot_state 直呼びを全廃する）。これにより (2)(3) の clobber 系の
競合も解消される。
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from .bot_state import write_bot_state

logger = logging.getLogger(__name__)

# 生成の入口として想定するowner。GateLease.owner / try_acquire等の引数に使う。
Owner = Literal["desktop", "batch", "discord", "remote", "bridge"]

# batch_active 中でも取得を許可するowner (バッチのアイテム間割り込み用)。
# これ以外 (remote/bridge/desktop) はバッチ終了までポーリング待ちを継続する。
_BATCH_ALLOWED_OWNERS = ("batch", "discord")

# wait_acquire が長時間 (30分) 取得できない場合に一度だけ警告ログを出す閾値。
_LONG_WAIT_WARN_SEC = 1800.0


@dataclass(frozen=True)
class GateLease:
    """try_acquire/wait_acquire が返す「取得券」。

    release() 時に token を照合することで、誤って他owner (あるいは
    既に無効になった過去のlease) がロックを解放してしまう事故を防ぐ。
    """
    owner: Owner
    token: str  # uuid4().hex。release照合用


class GenerationGate:
    """プロセス内で唯一の生成admissionロック + bot_state単一書き手。

    すべてのメソッドを `threading.Lock` で保護しているため、Qtメインスレッド・
    Discord/Remote Room専用スレッドの各asyncioループ・QThreadPoolのワーカー
    スレッドなど、どこから呼んでも安全に動作する。
    `wait_acquire` だけはポーリング型で、ロックは try_acquire の呼び出し中
    (一瞬) のみ保持し、`await asyncio.sleep(poll_sec)` はロック外で行う
    (既存のバッチ割り込み待機 app.py の流儀に合わせている)。
    """

    def __init__(self, runtime_root: Path | None = None) -> None:
        self._lock = threading.Lock()
        self._runtime_root = runtime_root
        self._port = 0
        self._holder: Owner | None = None
        self._holder_token: str | None = None
        self._batch_active = False

    # ── ComfyUIポート ────────────────────────────────────────────────────

    def set_comfy_port(self, port: int) -> None:
        """起動時 (setup完了時) に1回呼ぶ。bot_state.json を "idle"+port で書く。

        以後の acquire/release/begin_batch/end_batch/refresh_bot_state は
        すべてここで保持したポートを明示的に write_bot_state へ渡す
        (write_bot_state の port==0 引き継ぎ機構には依存しない)。
        """
        with self._lock:
            self._port = port
            self._write_mirror_locked("idle")
        logger.info("GenerationGate: comfy_port設定 port=%d", port)

    @property
    def comfy_port(self) -> int:
        """設定済みのComfyUIポート。set_comfy_port前は0。"""
        with self._lock:
            return self._port

    # ── admission (取得/解放) ────────────────────────────────────────────

    def _can_acquire_locked(self, owner: Owner) -> bool:
        """ロック保持中に呼ぶ内部判定。admissionポリシー本体。

        - holder有 (既に誰かが生成中) → 取得不可。
        - batch_active中は owner が "batch"/"discord" のみ取得可。
          "remote"/"bridge"/"desktop" は batch_active が解けるまで
          ポーリング待ちを継続させる (バッチ実行中の割り込みは
          Discordのみを想定した既存仕様を踏襲)。
        """
        if self._holder is not None:
            return False
        if self._batch_active and owner not in _BATCH_ALLOWED_OWNERS:
            return False
        return True

    def try_acquire(self, owner: Owner) -> GateLease | None:
        """即座に取得を試みる。取得できなければ None (待たない)。"""
        with self._lock:
            if not self._can_acquire_locked(owner):
                return None
            token = uuid.uuid4().hex
            self._holder = owner
            self._holder_token = token
            state = "batch" if self._batch_active else "single"
            self._write_mirror_locked(state)
        logger.info("GenerationGate: acquire owner=%s token=%s state=%s", owner, token[:8], state)
        return GateLease(owner=owner, token=token)

    async def wait_acquire(
        self,
        owner: Owner,
        cancel_check: Callable[[], bool] | None = None,
        poll_sec: float = 0.25,
    ) -> GateLease | None:
        """取得できるまでポーリングして待つ。cancel_check() が True になったら None を返す。

        ロックは try_acquire 呼び出し中だけ保持し、待機中の sleep はロック外で
        行うため、他owner の acquire/release をブロックしない。
        """
        start = time.monotonic()
        warned = False
        while True:
            if cancel_check is not None and cancel_check():
                return None
            lease = self.try_acquire(owner)
            if lease is not None:
                return lease
            if not warned and (time.monotonic() - start) > _LONG_WAIT_WARN_SEC:
                logger.warning(
                    "GenerationGate: owner=%s の取得待ちが30分を超えています (holder=%s)",
                    owner, self._holder,
                )
                warned = True
            await asyncio.sleep(poll_sec)

    def release(self, lease: GateLease) -> None:
        """leaseを解放する。token不一致時は警告ログのみで無視 (無害化)。

        既に解放済み (holderがNone) の場合は何もしない冪等動作とする。
        """
        with self._lock:
            if self._holder is None:
                return
            if self._holder_token != lease.token:
                logger.warning(
                    "GenerationGate: token不一致のためreleaseを無視 (owner=%s, holder=%s)",
                    lease.owner, self._holder,
                )
                return
            prev_owner = self._holder
            self._holder = None
            self._holder_token = None
            state = "batch" if self._batch_active else "idle"
            self._write_mirror_locked(state)
        logger.info("GenerationGate: release owner=%s state=%s", prev_owner, state)

    # ── バッチ全体の開始/終了 ─────────────────────────────────────────────

    def begin_batch(self) -> None:
        """バッチ処理全体の開始を通知する。以後 batch_active が True になる。"""
        with self._lock:
            self._batch_active = True
            self._write_mirror_locked("batch")
        logger.info("GenerationGate: begin_batch")

    def end_batch(self) -> None:
        """バッチ処理全体の終了を通知する。batch_active を False に戻す。

        holder が残っている (通常は起きないが防御的に) 場合は bot_state を
        上書きしない。holder が無ければ "idle" にミラーする。
        """
        with self._lock:
            self._batch_active = False
            if self._holder is None:
                self._write_mirror_locked("idle")
        logger.info("GenerationGate: end_batch")

    @property
    def holder(self) -> Owner | None:
        with self._lock:
            return self._holder

    @property
    def batch_active(self) -> bool:
        with self._lock:
            return self._batch_active

    # ── bot_state.json ミラー ────────────────────────────────────────────

    def refresh_bot_state(self) -> None:
        """現在の内部状態 (holder/batch_active) をそのまま書き直すだけ。

        Discordの keep-alive (定期的なタイムスタンプ更新) 用。
        read-modify-write を行わないため、他経路が書いた最新状態を
        古い値で上書きしてしまう心配がない。
        """
        with self._lock:
            if self._holder is not None:
                state = "batch" if self._batch_active else "single"
            elif self._batch_active:
                state = "batch"
            else:
                state = "idle"
            self._write_mirror_locked(state)

    def _write_mirror_locked(self, state: str) -> None:
        """ロック保持中に呼ぶ内部ヘルパー。runtime_root指定時のみ実書き込みする。"""
        if self._runtime_root is not None:
            write_bot_state(self._runtime_root, state, self._port)
