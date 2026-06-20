from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
import zipfile
from pathlib import Path

from ..domain.errors import SetupError
from ..i18n import tr
from .downloader import download_file

logger = logging.getLogger(__name__)

_UV_DEFAULT_URL = (
    "https://github.com/astral-sh/uv/releases/download/0.4.18/uv-x86_64-pc-windows-msvc.zip"
)


def _no_window_flags() -> dict:
    if sys.platform == "win32":
        return {
            "creationflags": subprocess.CREATE_NO_WINDOW,
            "startupinfo": _make_startupinfo(),
        }
    return {}


def _make_startupinfo():
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return si


def _run_uv(
    uv_exe: Path,
    args: list[str],
    cwd: Path | None = None,
    extra_env: dict[str, str] | None = None,
) -> None:
    import os
    cmd = [str(uv_exe)] + args
    logger.debug("uv実行: %s", " ".join(cmd))
    env = os.environ.copy()
    # 日本語 Windows (cp932) でビルド時 setup.py の読み取りが UnicodeDecodeError になるのを防ぐ
    env["PYTHONUTF8"] = "1"
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        **_no_window_flags(),
    )
    if result.returncode != 0:
        logger.error("uv失敗 stderr: %s", result.stderr[-2000:])
        raise SetupError(tr("uv実行失敗: {cmd}\n{stderr}").format(
            cmd=" ".join(args[:3]), stderr=result.stderr[-500:]))
    logger.debug("uv stdout: %s", result.stdout[-1000:])
    if result.stderr:
        logger.debug("uv stderr: %s", result.stderr[-500:])


class UvManager:
    def __init__(self, uv_exe: Path, uv_dir: Path):
        self._uv = uv_exe
        self._uv_dir = uv_dir

    @classmethod
    async def ensure(
        cls,
        uv_dir: Path,
        url: str = _UV_DEFAULT_URL,
        sha256: str = "",
    ) -> "UvManager":
        uv_exe = uv_dir / "uv.exe"
        if not uv_exe.exists():
            logger.info("uvをDLします: %s", url)
            zip_path = uv_dir / "uv.zip"
            await download_file(url, zip_path, sha256=sha256)
            with zipfile.ZipFile(zip_path, "r") as zf:
                for member in zf.namelist():
                    if member.endswith("uv.exe"):
                        zf.extract(member, uv_dir)
                        extracted = uv_dir / member
                        if extracted != uv_exe:
                            extracted.rename(uv_exe)
                        break
            zip_path.unlink(missing_ok=True)
        logger.info("uv準備完了: %s", uv_exe)
        return cls(uv_exe, uv_dir)

    def create_venv(self, venv_path: Path, python_version: str = "3.13") -> None:
        logger.info("venv作成: %s", venv_path)
        _run_uv(
            self._uv,
            ["venv", str(venv_path), f"--python={python_version}"],
            extra_env={"UV_PYTHON_DOWNLOADS": "automatic"},
        )

    def pip_install(self, venv_path: Path, packages: list[str], index_url: str = "") -> None:
        # VIRTUAL_ENV 環境変数でインストール先を指定(-p <python> は uv バージョンによって不安定)
        args = ["pip", "install"] + packages
        if index_url:
            args += ["--index-url", index_url]
        _run_uv(self._uv, args, extra_env={"VIRTUAL_ENV": str(venv_path)})

    def pip_install_requirements(self, venv_path: Path, req_file: Path) -> None:
        import os
        import tempfile
        # scikit-image==0.20.0 等 Python 3.13 非対応の古い版がピンされていてもビルドを回避する
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write("scikit-image>=0.25.0\n")
            # opencv-python 4.8.x 以前は numpy 1.x 専用ビルドで numpy 2.x に非対応
            f.write("opencv-python>=4.10.0\n")
            override_path = f.name
        try:
            _run_uv(
                self._uv,
                ["pip", "install", "-r", str(req_file), "--override", override_path],
                extra_env={"VIRTUAL_ENV": str(venv_path)},
            )
        finally:
            os.unlink(override_path)
