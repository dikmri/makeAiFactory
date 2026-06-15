from __future__ import annotations

import logging
import socket
import subprocess
import sys
from pathlib import Path

from ..constants import COMFY_HOST, COMFY_PORT_RANGE, COMFY_STARTUP_TIMEOUT
from ..domain.errors import ComfyStartError

logger = logging.getLogger(__name__)


def _find_free_port(host: str, start: int, end: int) -> int:
    for port in range(start, end):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex((host, port)) != 0:
                return port
    raise ComfyStartError("空きポートが見つかりませんでした")


def _no_window_popen_kwargs() -> dict:
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        kwargs["startupinfo"] = si
    return kwargs


class ComfyServerController:
    def __init__(
        self,
        python_exe: Path,
        comfyui_dir: Path,
        comfyui_log: Path,
    ):
        self._python = python_exe
        self._comfyui_dir = comfyui_dir
        self._comfyui_log = comfyui_log
        self._process: subprocess.Popen | None = None
        self._port: int = 0
        self._log_file = None

    @property
    def port(self) -> int:
        return self._port

    @property
    def base_url(self) -> str:
        return f"http://{COMFY_HOST}:{self._port}"

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self, extra_flags: list[str] | None = None) -> None:
        if self.is_running:
            return

        self._port = _find_free_port(COMFY_HOST, *COMFY_PORT_RANGE)
        self._comfyui_log.parent.mkdir(parents=True, exist_ok=True)
        self._log_file = open(self._comfyui_log, "a", encoding="utf-8", errors="replace", buffering=1)

        cmd = [
            str(self._python),
            str(self._comfyui_dir / "main.py"),
            "--listen", COMFY_HOST,
            "--port", str(self._port),
            "--disable-auto-launch",
            *(extra_flags or []),
        ]

        import os
        env = os.environ.copy()
        # 日本語Windows (cp932) で絵文字等の出力が UnicodeEncodeError を起こしてクラッシュするのを防ぐ
        env["PYTHONUTF8"] = "1"

        logger.info("ComfyUI起動: port=%d", self._port)
        self._process = subprocess.Popen(
            cmd,
            cwd=str(self._comfyui_dir),
            stdout=self._log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            **_no_window_popen_kwargs(),
        )
        logger.info("ComfyUI PID=%d", self._process.pid)

    def stop(self) -> None:
        if self._process and self._process.poll() is None:
            logger.info("ComfyUI停止 (PID=%d)", self._process.pid)
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.kill()
        self._process = None
        if self._log_file:
            self._log_file.close()
            self._log_file = None

    def read_log_tail(self, lines: int = 50) -> str:
        if not self._comfyui_log.exists():
            return ""
        with self._comfyui_log.open("r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        return "".join(all_lines[-lines:])
