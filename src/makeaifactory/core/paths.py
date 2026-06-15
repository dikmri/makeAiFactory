from __future__ import annotations

import os
import sys
from pathlib import Path

from ..constants import APP_NAME


def _app_root() -> Path:
    """バンドルリソース(manifest/workflow)の基底ディレクトリ。
    PyInstaller onedir では --add-data ファイルが _MEIPASS (_internal/) に格納される。"""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).parent.parent.parent.parent


def _exe_dir() -> Path:
    """exe と同階層のディレクトリ(runtime 書き込み候補)。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent.parent.parent.parent


def _is_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        test = path / ".write_test"
        test.touch()
        test.unlink()
        return True
    except OSError:
        return False


def _is_ascii_path(path: Path) -> bool:
    try:
        str(path).encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def get_runtime_root() -> Path:
    """
    書き込み可能なruntime rootを返す。
    パスに非ASCII文字（日本語等）が含まれる場合や書き込み不可の場合は
    %LOCALAPPDATA%へfallback。uvが非ASCIIパスを正しく扱えないため。
    """
    candidate = _exe_dir() / "runtime"
    if _is_ascii_path(candidate) and _is_writable(candidate):
        return candidate

    localappdata = os.environ.get("LOCALAPPDATA", "")
    if localappdata:
        fallback = Path(localappdata) / APP_NAME / "runtime"
        if _is_writable(fallback):
            return fallback

    raise RuntimeError("書き込み可能なruntime領域が見つかりません。")


class AppPaths:
    def __init__(self, runtime_root: Path | None = None):
        self._root = runtime_root or get_runtime_root()

    @property
    def runtime_root(self) -> Path:
        return self._root

    @property
    def app_root(self) -> Path:
        return _app_root()

    @property
    def app_dir(self) -> Path:
        return self.app_root / "app"

    @property
    def workflow_dir(self) -> Path:
        return self.app_dir / "workflow"

    @property
    def manifest_dir(self) -> Path:
        return self.app_dir / "manifest"

    @property
    def uv_dir(self) -> Path:
        return self._root / "uv"

    @property
    def uv_exe(self) -> Path:
        return self.uv_dir / "uv.exe"

    @property
    def venv_dir(self) -> Path:
        return self._root / ".venv"

    @property
    def python_exe(self) -> Path:
        return self.venv_dir / "Scripts" / "python.exe"

    @property
    def comfyui_dir(self) -> Path:
        return self._root / "ComfyUI"

    @property
    def comfyui_main(self) -> Path:
        return self.comfyui_dir / "main.py"

    @property
    def models_dir(self) -> Path:
        return self.comfyui_dir / "models"

    @property
    def custom_nodes_dir(self) -> Path:
        return self.comfyui_dir / "custom_nodes"

    @property
    def comfyui_output_dir(self) -> Path:
        return self.comfyui_dir / "output"

    @property
    def comfyui_input_dir(self) -> Path:
        return self.comfyui_dir / "input"

    @property
    def downloads_dir(self) -> Path:
        return self._root / "downloads"

    @property
    def cache_dir(self) -> Path:
        return self._root / "cache"

    @property
    def logs_dir(self) -> Path:
        return self._root / "logs"

    @property
    def app_log(self) -> Path:
        return self.logs_dir / "app.log"

    @property
    def setup_log(self) -> Path:
        return self.logs_dir / "setup.log"

    @property
    def comfyui_log(self) -> Path:
        return self.logs_dir / "comfyui.log"

    @property
    def jobs_log_dir(self) -> Path:
        return self.logs_dir / "jobs"

    @property
    def outputs_dir(self) -> Path:
        return self._root / "outputs"

    def job_dir(self, job_id: str, date_str: str) -> Path:
        return self.outputs_dir / date_str / job_id

    def api_source_json(self) -> Path:
        return self.workflow_dir / "makeAiFactory_api_source.json"

    def runtime_template_json(self) -> Path:
        return self.workflow_dir / "makeAiFactory_runtime_template.json"

    def patch_rules_json(self) -> Path:
        return self.workflow_dir / "workflow_patch_rules.json"

    def runtime_manifest_json(self) -> Path:
        return self.manifest_dir / "runtime_manifest.json"

    def model_manifest_json(self) -> Path:
        return self.manifest_dir / "model_manifest.json"

    def custom_nodes_manifest_json(self) -> Path:
        return self.manifest_dir / "custom_nodes_manifest.json"

    def ensure_dirs(self) -> None:
        for d in [
            self.logs_dir,
            self.jobs_log_dir,
            self.downloads_dir,
            self.cache_dir,
            self.outputs_dir,
            self.uv_dir,
        ]:
            d.mkdir(parents=True, exist_ok=True)
