"""SCH-01: 生成実行の共通部品。

Desktop (`core/job_controller.py`) / Discord (`core/discord_bot_controller.py`) /
Remote Room (`remote_room/controller.py`) の3経路に逐語コピペされていた
「history取得リトライ」「テンプレ選択fallback」ロジックをここへ集約する。

PR2 では純粋なヘルパー関数のみを提供していた (挙動は既存3箇所と完全に同一)。
PR3 で `GenerationExecutor` 本体 (3経路共通の「接続確認→アップロード→
workflow patch→queue投入→進捗監視→出力解決→job_dirへコピー」という生成フロー
自体の統合) を追加した。3経路 (Desktop/Discord/Remote Room) の各コントローラは
この `GenerationExecutor.run()` を呼ぶだけになり、経路固有の差分
(job_id採番規則・アップロードファイル名・進捗表示への変換・ベンチ計測等) だけを
各コントローラ側に残す。

PR4 で `submit()` (gate取得→run()→release の一括化) と `request_cancel()`
(実行中レジストリとの owner/job_id 照合による cancel) を追加する。従来、各経路の
cancel系メソッドは ComfyUI の `POST /interrupt` を無条件・グローバルに発行していた
ため、別経路のジョブ実行中に自分のcancelを押すと他人のジョブを殺してしまう
(誤爆)問題があった。`request_cancel` は「自分のジョブが実際に実行中の時だけ」
interruptを発行することでこれを解消する。

RET-01 (監査対応) で `run()` の出力解決後 (手順9) に、ComfyUI本体の
output/inputディレクトリに残る元ファイル (job_dirへコピー済みの元mp4、
アップロード済みの入力画像) を削除する処理を追加した。3経路すべてがこの
`run()` を共有するため、この対応だけで Desktop/Discord/Remote Room 全経路の
`comfyui_output_dir`/`comfyui_input_dir` の無限累積が解消される。
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, TYPE_CHECKING

import httpx

from ..comfy.output_resolver import resolve_output_mp4
from ..comfy.workflow_patcher import (
    DevModeOverrides,
    WorkflowPatchContext,
    apply_dev_overrides,
    make_output_prefix,
    patch_workflow,
)
from ..domain.errors import JobCancelledError, OutputNotFoundError

if TYPE_CHECKING:
    from ..comfy.api_client import ComfyApiClient
    from .generation_gate import GenerationGate
    from .paths import AppPaths

logger = logging.getLogger(__name__)


async def resolve_output_with_retry(
    client: "ComfyApiClient",
    prompt_id: str,
    comfyui_output_dir: Path,
    job_id: str,
    attempts: int = 3,
    delay_sec: float = 0.3,
) -> tuple[Path, dict]:
    """historyからMP4を解決する。未反映競合に備え最大attempts回リトライする。

    prompt_idに紐づく動画がhistoryへ未反映な稀な競合に備えたリトライで、
    成功しているのに失敗扱いにしないための安全弁。history取得自体は毎回やり直す。

    戻り値は (mp4パス, 最後に取得したhistory)。
    全滅時は最後のOutputNotFoundErrorを送出する。呼び出し側が失敗時にも
    historyを保存できるよう、送出前に例外へ `history` 属性として添付する。

    resolve_output_mp4 を必ず経由する(VHS_VideoCombineの"gifs"キー対応を維持)。
    """
    history: dict = {}
    last_error: OutputNotFoundError | None = None
    for attempt in range(attempts):
        history = await client.get_history(prompt_id)
        try:
            output_mp4 = resolve_output_mp4(history, prompt_id, comfyui_output_dir, job_id)
            return output_mp4, history
        except OutputNotFoundError as e:
            last_error = e
            if attempt < attempts - 1:
                await asyncio.sleep(delay_sec)
    assert last_error is not None
    last_error.history = history  # type: ignore[attr-defined]  # 呼び出し側での保存用
    raise last_error


def load_template_for_workflow(paths: "AppPaths", workflow: str | None) -> dict:
    """workflow指定に応じてワークフローテンプレートを読み込む。

    workflow指定があれば、そのワークフロー用にサニタイズ済みのテンプレート
    (build_workflow_templates() で `<runtime_root>/remote_room/templates/<wf>.json`
    へ生成済み) を優先する。無ければ (または未生成なら) アプリでアクティブな
    runtime_template_json へフォールバックする。

    Discord Bot / Remote Room の両経路にあった逐語重複ロジックの共通化。
    (Desktopはワークフロー指定という概念自体が無く、常にアクティブテンプレートを
    使うためこの関数の対象外)
    """
    template_path = paths.runtime_template_json()
    if workflow:
        wf_path = paths.runtime_root / "remote_room" / "templates" / f"{workflow}.json"
        if wf_path.exists():
            template_path = wf_path
            logger.info("ワークフロー指定: %s (%s)", workflow, wf_path.name)
        else:
            logger.warning(
                "ワークフローテンプレート未生成: %s → 既定にフォールバック", workflow
            )
    if not template_path.exists():
        raise FileNotFoundError(f"ワークフローテンプレートが見つかりません: {template_path}")
    with template_path.open(encoding="utf-8") as f:
        return json.load(f)


# ── PR3: GenerationExecutor 本体 ───────────────────────────────────────────

# on_stage に渡ってくる段階名。呼び出し側はこの文字列で分岐し、経路ごとの
# 進捗通知 (JobProgress / Discord embed / RemoteRoom pct) へ変換する。
# "connecting"→wait_until_ready / "uploading"→画像コピー+upload_image /
# "queueing"→queue_prompt / "generating"→watch_progressループ /
# "resolving"→resolve_output_with_retry
StageCallback = Callable[[str], None]

# watch_progress が yield する ComfyProgressEvent をそのまま素通しするコールバック。
# generation_executor は comfy.progress_tracker に依存しないため、型は緩く object とする。
EventCallback = Callable[[object], None]

# submit() が即時取得に失敗した (=他の生成が実行中で待たされる) 場合にのみ
# 1回だけ呼ばれるコールバック。呼び出し元はここで「他の生成の完了を待っています...」
# 等の待機通知を表示する。
WaitCallback = Callable[[], None]


@dataclass
class GenerationRequest:
    """`GenerationExecutor.run()` への入力。

    経路 (Desktop/Discord/Remote Room) 固有の決定事項 (job_id採番規則・
    アップロードファイル名・シード決定ロジック・プリセット選択) はすべて
    呼び出し元が確定させた値をここへ詰める。GenerationExecutor 自身は
    これらの「どう決めるか」には一切関与しない。
    """
    owner: str                    # "desktop"|"batch"|"discord"|"remote"|"bridge"
    job_id: str                   # 採番は各入口の既存規則のまま (統一しない)
    input_image: Path              # コピー元の入力画像 (呼び出し元が保持する元ファイル)
    job_dir: Path                  # 呼び出し元がmkdir済み。output.mp4 はここへ配置する
    template: dict                 # 解決済みworkflowテンプレート (選択は呼び出し元)
    seed: int | None
    unet_high_name: str
    unet_low_name: str
    sage_attention_mode: str = "disabled"
    # ComfyUIへアップロードする際に job_dir 直下へコピーするファイル名。
    # 各経路の既存命名をそのまま再現すること
    # (Desktop: f"makeaifactory_{job_id}{suffix}" /
    #  Discord: f"discord_{job_id}{suffix}" / Remote: f"remote_{job_id}.png")。
    upload_basename: str = ""
    dev_overrides: DevModeOverrides | None = None
    save_workflow_json: bool = False       # DesktopのみTrue (patch直後・queue前にjob_dirへ保存)
    ready_timeout_sec: int | None = None   # Noneならwait_until_readyの既定値(120秒)を使う


@dataclass
class GenerationResult:
    """`GenerationExecutor.run()` の戻り値。"""
    output_path: Path   # job_dir/output.mp4 (コピー済み)
    prompt_id: str
    history: dict
    uploaded_image_name: str


@dataclass
class _ActiveRun:
    """PR4: 実行中レジストリの1エントリ (owner をキーに `_active` へ登録される)。

    `request_cancel()` の owner/job_id 照合対象。`cancelled` は run() の
    cancel_check とORで合流させる threading.Event で、request_cancel から
    立てられる (run()が非同期タスク側、request_cancelがどのスレッドからでも
    呼ばれる側、という関係になるためthreading.Eventで橋渡しする)。
    """
    job_id: str
    base_url: str
    cancelled: threading.Event = field(default_factory=threading.Event)


class GenerationExecutor:
    """3経路 (Desktop/Discord/Remote Room) 共通の生成実行本体。

    従来 `JobController.run_job` / `DiscordBotController._generate_video` /
    `RemoteRoomController._generate_video` に逐語コピペされていた
    「wait_until_ready→入力画像コピー+upload_image→WorkflowPatchContext組立+
    patch_workflow→queue_prompt→watch_progressループ→
    resolve_output_with_retry→job_dir/output.mp4コピー」という一連の流れを
    ここへ集約する。各経路固有の振る舞い (進捗通知・ベンチ計測・キャンセル判定・
    ファイル命名) は `on_stage`/`on_event`/`cancel_check` 及び
    `GenerationRequest` の各フィールド経由で呼び出し元に委ねる。

    例外は一切変換せず伝播させる (OutputNotFoundError含む。
    resolve_output_with_retry の「例外にhistory属性を添付する」挙動もそのまま維持)。

    PR4: `gate` を渡すと `submit()` (gate取得→run→release一括) と
    `request_cancel()` (owner/job_id照合でのcancel) が使えるようになる。
    `run()` 単体の利用 (gate不要) には従来どおり影響しない。
    """

    def __init__(self, paths: "AppPaths", gate: "GenerationGate | None" = None) -> None:
        self._paths = paths
        self._gate = gate
        # 実行中レジストリ: owner→_ActiveRun。run()開始時に登録しfinallyで解除する。
        # request_cancelはこの辞書とowner(+job_id)を照合してから初めてinterruptを
        # 発行する(誤爆防止の要)。どのスレッドからも呼べるようlockで保護する。
        self._registry_lock = threading.Lock()
        self._active: dict[str, _ActiveRun] = {}

    async def submit(
        self,
        req: GenerationRequest,
        client: "ComfyApiClient",
        *,
        on_stage: StageCallback | None = None,
        on_event: EventCallback | None = None,
        cancel_check: Callable[[], bool] | None = None,
        on_wait: WaitCallback | None = None,
    ) -> GenerationResult:
        """`gate.wait_acquire(req.owner)` → `run()` → release を一括で行う。

        即時 `try_acquire` に失敗した場合 (=他の生成が実行中) のみ `on_wait()` を
        1回呼んでから `wait_acquire` で待つ。取得できた場合は `on_wait` を呼ばない。
        取得待ち中に `cancel_check()` が True になった場合は `JobCancelledError`
        を送出する (この場合gateは未取得なのでreleaseは行わない)。
        `run()` が成功・例外いずれの場合も、取得したleaseは必ずreleaseする。
        """
        if self._gate is None:
            raise RuntimeError(
                "GenerationExecutor.submit() を使うには、コンストラクタへ gate を渡してください。"
            )
        lease = self._gate.try_acquire(req.owner)
        if lease is None:
            if on_wait is not None:
                on_wait()
            lease = await self._gate.wait_acquire(req.owner, cancel_check=cancel_check)
            if lease is None:
                raise JobCancelledError("生成待機中にキャンセルされました")
        try:
            return await self.run(
                req, client, on_stage=on_stage, on_event=on_event, cancel_check=cancel_check,
            )
        finally:
            self._gate.release(lease)

    def request_cancel(self, owner: str, job_id: str | None = None) -> bool:
        """実行中レジストリと owner (+job_id指定時はjob_idも) を照合し、一致した
        場合のみ (a)当該実行のcancelledフラグを立て、(b)/interrupt をfire-and-forget
        発行する。一致しない・実行中でない場合は False を返し interrupt しない
        (これが「別経路のジョブを誤って止めない」ための本体)。
        どのスレッドからでも呼べる (threading.Lockで保護)。
        """
        with self._registry_lock:
            active = self._active.get(owner)
            if active is None:
                return False
            if job_id is not None and active.job_id != job_id:
                return False
            active.cancelled.set()
            base_url = active.base_url

        if base_url:
            self._fire_interrupt(base_url, owner)
        else:
            logger.warning("request_cancel: base_url不明のためinterrupt発行をスキップ (owner=%s)", owner)
        return True

    def _fire_interrupt(self, base_url: str, owner: str) -> None:
        """`/interrupt` をfire-and-forgetで発行する。daemonスレッド内で実行し、
        呼び出し元のイベントループ/UIスレッドを一切塞がない。例外は握りつぶし
        ログのみ残す (通信不能な状況でrequest_cancel自体が失敗しないように)。
        """
        def _do() -> None:
            try:
                httpx.post(f"{base_url}/interrupt", timeout=10)
                logger.info("interrupt発行完了 (owner=%s)", owner)
            except Exception:
                logger.warning("interrupt発行に失敗しました (owner=%s)", owner, exc_info=True)

        threading.Thread(target=_do, daemon=True, name=f"GenerationExecutor-interrupt-{owner}").start()

    async def run(
        self,
        req: GenerationRequest,
        client: "ComfyApiClient",
        *,
        on_stage: StageCallback | None = None,
        on_event: EventCallback | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> GenerationResult:
        def _stage(name: str) -> None:
            if on_stage is not None:
                on_stage(name)

        # 実行中レジストリへ登録 (owner をキーとする。同一owner内で同時に複数の
        # runが走ることは通常gateにより起きないが、runはgate非経由でも呼べる
        # ため、そのケースでは後勝ちで上書きされる)。base_urlはclientから取得する
        # (ComfyApiClient.base_url。フェイク等で無い場合は空文字扱いとしinterrupt
        # 発行のみスキップする)。
        active_run = _ActiveRun(job_id=req.job_id, base_url=getattr(client, "base_url", "") or "")
        with self._registry_lock:
            self._active[req.owner] = active_run

        try:
            return await self._run_impl(
                req, client, on_stage=_stage, on_event=on_event,
                cancel_check=cancel_check, cancelled_event=active_run.cancelled,
            )
        finally:
            with self._registry_lock:
                if self._active.get(req.owner) is active_run:
                    del self._active[req.owner]

    async def _run_impl(
        self,
        req: GenerationRequest,
        client: "ComfyApiClient",
        *,
        on_stage: Callable[[str], None],
        on_event: EventCallback | None,
        cancel_check: Callable[[], bool] | None,
        cancelled_event: threading.Event,
    ) -> GenerationResult:
        _stage = on_stage

        # 1. ComfyUI起動確認
        _stage("connecting")
        if req.ready_timeout_sec is not None:
            await client.wait_until_ready(timeout_sec=req.ready_timeout_sec)
        else:
            await client.wait_until_ready()

        # 2. 入力画像を job_dir へコピー (既存の経路別命名を再現) → アップロード
        _stage("uploading")
        upload_basename = req.upload_basename or req.input_image.name
        upload_copy = req.job_dir / upload_basename
        shutil.copy2(req.input_image, upload_copy)
        uploaded_name = await client.upload_image(upload_copy)
        logger.info("画像アップロード完了: job=%s → %s", req.job_id, uploaded_name)

        # 3. workflow patch (+ 開発モードのオーバーライド)
        ctx = WorkflowPatchContext(
            job_id=req.job_id,
            uploaded_image_name=uploaded_name,
            output_prefix=make_output_prefix(req.job_id),
            seed=req.seed,
            unet_high_name=req.unet_high_name,
            unet_low_name=req.unet_low_name,
            sage_attention_mode=req.sage_attention_mode,
        )
        patched = patch_workflow(req.template, ctx)
        if req.dev_overrides is not None:
            patched = apply_dev_overrides(patched, req.dev_overrides)

        # 4. workflow.json 保存 (Desktopのみ。queue投入前、既存の保存順序を踏襲)
        if req.save_workflow_json:
            with (req.job_dir / "workflow.json").open("w", encoding="utf-8") as f:
                json.dump(patched, f, ensure_ascii=False, indent=2)

        # 5. prompt投入
        _stage("queueing")
        prompt_id = await client.queue_prompt(patched)
        logger.info("生成キュー投入完了: job=%s prompt=%s", req.job_id, prompt_id)

        # 6. 進捗監視 (ComfyProgressEventをon_eventへ素通し)
        _stage("generating")
        async for event in client.watch_progress(prompt_id):
            if on_event is not None:
                on_event(event)

        # 7. キャンセル判定 (既存Desktopの `job.status == CANCELLED` 判定に相当する位置。
        # watch_progress完了後・出力解決前でチェックする)。呼び出し元の cancel_check と、
        # request_cancel() が立てる registry の cancelled Event を OR で合流させる
        # (どちらか一方が True ならキャンセル扱いにする)。
        if (cancel_check is not None and cancel_check()) or cancelled_event.is_set():
            raise JobCancelledError("生成がキャンセルされました")

        # 8. history からのMP4解決 (未反映競合に備えたリトライ込み。PR2ヘルパー)
        _stage("resolving")
        output_mp4, history = await resolve_output_with_retry(
            client, prompt_id, self._paths.comfyui_output_dir, req.job_id,
        )

        # 9. job_dir/output.mp4 へコピーして結果を返す
        final_output = req.job_dir / "output.mp4"
        shutil.copy2(output_mp4, final_output)

        # RET-01: ComfyUI本体のoutputディレクトリ (comfyui_output_dir) 配下の元mp4は
        # job_dirへコピー済みなら不要。削除しないと際限なく累積するため、コピー先の
        # サイズが元ファイルと一致することを確認してから元ファイルを削除する
        # (Desktop/Discord/Remote Roomの3経路がこのrun()を共有するため、ここ一箇所の
        # 対応で全経路に効く)。サイズ不一致(コピー未完了等の疑い)の場合は消さず
        # warningのみに留める。失敗しても生成自体は成功扱いにする(ログのみ)。
        try:
            if final_output.stat().st_size == output_mp4.stat().st_size:
                output_mp4.unlink(missing_ok=True)
                # 出力subfolder (例: makeAiFactory/<job_id>/) が空になったら親dirも
                # 削除する。他ジョブの出力がまだ残っていれば何もしない。
                parent_dir = output_mp4.parent
                try:
                    if parent_dir.exists() and not any(parent_dir.iterdir()):
                        parent_dir.rmdir()
                except OSError:
                    pass
            else:
                logger.warning(
                    "RET-01: コピー後のサイズ不一致のため元ファイルを削除しませんでした: %s",
                    output_mp4,
                )
        except Exception:
            logger.warning("RET-01: ComfyUI元出力の削除に失敗しました: %s", output_mp4, exc_info=True)

        # RET-01: ComfyUI/input へアップロードした画像 (uploaded_name はComfyUI側の
        # 実ファイル名) も生成完了後は不要なため削除する。テスト用のフェイクpaths等
        # comfyui_input_dir属性を持たない場合も含め、失敗してもログのみで続行する。
        try:
            (self._paths.comfyui_input_dir / uploaded_name).unlink(missing_ok=True)
        except Exception:
            logger.warning("RET-01: ComfyUI入力残渣の削除に失敗しました: %s", uploaded_name, exc_info=True)

        return GenerationResult(
            output_path=final_output,
            prompt_id=prompt_id,
            history=history,
            uploaded_image_name=uploaded_name,
        )
