"""cloudflared.exe の起動・監視・停止を管理する。"""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

TRYCLOUDFLARE_URL_RE = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")
TUNNEL_STARTUP_TIMEOUT = 60  # seconds


def find_cloudflared() -> Path | None:
    """cloudflared.exe のパスを探す。同梱 → PATH の順に検索する。"""
    # 1. 同梱バイナリを優先
    base = getattr(sys, "_MEIPASS", None)
    if base:
        bundled = Path(base) / "makeaifactory" / "resources" / "cloudflared" / "windows-amd64" / "cloudflared.exe"
    else:
        # 開発モード: このファイルの 3 階層上 = src/makeaifactory
        bundled = Path(__file__).resolve().parents[1] / "resources" / "cloudflared" / "windows-amd64" / "cloudflared.exe"

    if bundled.exists():
        logger.info("同梱 cloudflared を使用: %s", bundled)
        return bundled

    # 2. PATH から検索
    which = shutil.which("cloudflared") or shutil.which("cloudflared.exe")
    if which:
        logger.info("PATH 上の cloudflared を使用: %s", which)
        return Path(which)

    return None


class TunnelManager:
    """cloudflared Quick Tunnel を起動し、発行された URL を取得する。"""

    def __init__(self) -> None:
        self._process: asyncio.subprocess.Process | None = None  # type: ignore[name-defined]
        self._public_url: str | None = None

    @property
    def public_url(self) -> str | None:
        return self._public_url

    async def start(self, local_port: int, exe_path: Path | None = None) -> str:
        """
        cloudflared を起動して trycloudflare.com URL を返す。
        exe_path を指定するとそのバイナリを使用する (cloudflared_installer から渡される)。
        TUNNEL_STARTUP_TIMEOUT 秒以内に URL が取得できない場合は RuntimeError を送出する。

        RLC-01 (2): URL取得(TimeoutError/プロセス異常終了によるRuntimeError等)に
        失敗した場合は、起動済みの cloudflared プロセスを残さないよう自ら停止
        (self-clean) してから元の例外を再送出する。呼び出し側が stop() を
        呼び忘れてもプロセスが残存しないようにするための保険。
        """
        cloudflared = exe_path or find_cloudflared()
        if cloudflared is None:
            raise RuntimeError(
                "cloudflared が見つかりません。\n"
                "アプリに同梱されているか、PATH に cloudflared をインストールしてください。\n"
                "公式: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
            )

        cmd = [
            str(cloudflared),
            "tunnel",
            "--url",
            f"http://127.0.0.1:{local_port}",
        ]
        logger.info("cloudflared 起動: %s", " ".join(cmd))

        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            creationflags=0x08000000 if sys.platform == "win32" else 0,  # CREATE_NO_WINDOW
        )

        try:
            url = await asyncio.wait_for(self._read_url(), timeout=TUNNEL_STARTUP_TIMEOUT)
        except Exception:
            logger.warning("Tunnel URL 取得に失敗したため cloudflared を停止します", exc_info=True)
            await self.stop()
            raise
        self._public_url = url
        logger.info("Tunnel URL 取得: %s", url)

        # バックグラウンドで stdout を読み続ける（プロセスが詰まらないように）
        asyncio.ensure_future(self._drain_output())
        return url

    async def _read_url(self) -> str:
        assert self._process and self._process.stdout
        while True:
            line_bytes = await self._process.stdout.readline()
            if not line_bytes:
                raise RuntimeError("cloudflared が予期せず終了しました")
            line = line_bytes.decode("utf-8", errors="replace").strip()
            logger.debug("cloudflared: %s", line)
            m = TRYCLOUDFLARE_URL_RE.search(line)
            if m:
                return m.group(0)

    async def _drain_output(self) -> None:
        """プロセスが stdout バッファで詰まらないように読み捨てる。"""
        try:
            assert self._process and self._process.stdout
            while True:
                line_bytes = await self._process.stdout.readline()
                if not line_bytes:
                    break
                logger.debug("cloudflared: %s", line_bytes.decode("utf-8", errors="replace").strip())
        except Exception:
            pass

    async def stop(self) -> None:
        if self._process is None:
            return
        try:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._process.kill()
                await self._process.wait()
            logger.info("cloudflared 停止完了")
        except Exception as e:
            logger.warning("cloudflared 停止中にエラー: %s", e)
        finally:
            self._process = None
            self._public_url = None

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.returncode is None
