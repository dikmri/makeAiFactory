from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Callable

from ..comfy.server_controller import ComfyServerController
from ..comfy.workflow_sanitizer import load_and_sanitize
from ..core.job_controller import JobController
from ..core.log_manager import get_setup_logger, setup_logging
from ..core.paths import AppPaths
from ..core.settings_store import SettingsStore
from ..constants import COMFY_STARTUP_TIMEOUT
from ..domain.errors import (
    ComfyStartError,
    DiskSpaceError,
    MissingModelError,
    MissingNodeError,
    SetupError,
    SystemUnsupportedError,
)
from ..domain.manifest import (
    CustomNodesManifest,
    ModelManifest,
    RuntimeManifest,
)
from ..domain.progress import (
    JobProgress,
    SetupProgress,
    SetupState,
)
from ..runtime.comfy_installer import install_comfyui, install_torch
from ..runtime.custom_node_installer import install_custom_nodes, verify_required_classes
from ..runtime.model_installer import (
    check_required_models_present,
    install_models,
)
from ..runtime.runtime_state import RuntimeState
from ..runtime.system_probe import SystemInfo, probe_system, validate_system
from ..runtime.uv_manager import UvManager

logger = logging.getLogger(__name__)

SetupCallback = Callable[[SetupProgress], None]
JobProgressCallback = Callable[[JobProgress], None]


class AppController:
    def __init__(self, paths: AppPaths, settings: SettingsStore):
        self._paths = paths
        self._settings = settings
        self._state = RuntimeState(paths.runtime_root)
        self._server: ComfyServerController | None = None
        self._job_ctrl: JobController | None = None
        self._system_info: SystemInfo | None = None

    def _load_runtime_manifest(self) -> RuntimeManifest:
        with self._paths.runtime_manifest_json().open("r", encoding="utf-8") as f:
            return RuntimeManifest.from_dict(json.load(f))

    def _load_model_manifest(self) -> ModelManifest:
        with self._paths.model_manifest_json().open("r", encoding="utf-8") as f:
            return ModelManifest.from_dict(json.load(f))

    def _load_custom_nodes_manifest(self) -> CustomNodesManifest:
        with self._paths.custom_nodes_manifest_json().open("r", encoding="utf-8") as f:
            return CustomNodesManifest.from_dict(json.load(f))

    def _load_workflow_template(self) -> dict:
        tpl_path = self._paths.runtime_template_json()
        if not tpl_path.exists():
            raise SetupError("workflow templateが見つかりません。セットアップを再実行してください。")
        with tpl_path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def get_system_info_text(self) -> str:
        if not self._system_info:
            return "システム情報未取得"
        info = self._system_info
        gpu = info.primary_gpu
        lines = [
            f"OS: {info.os_name} {info.os_version}",
            f"CPU: {info.cpu}",
            f"RAM: {info.ram_gb:.1f} GB",
            f"GPU: {gpu.name if gpu else 'なし'}",
            f"VRAM: {gpu.vram_gb:.1f} GB" if gpu else "VRAM: -",
            f"nvidia-smi: {'利用可能' if info.nvidia_smi_available else '利用不可'}",
            f"Disk空き: {info.disk_free_gb:.1f} GB",
            f"RuntimeRoot: {self._paths.runtime_root}",
            f"RuntimeState: {self._state.setup_state.value}",
        ]
        return "\n".join(lines)

    async def setup(self, on_progress: SetupCallback | None = None) -> None:
        paths = self._paths
        paths.ensure_dirs()
        setup_log = get_setup_logger(paths.logs_dir)

        def _progress(state: SetupState, msg: str, detail: str = "", pct: float = 0.0) -> None:
            self._state.set_setup_state(state)
            p = SetupProgress(state=state, message=msg, detail=detail, percent=pct)
            setup_log.info("[%s] %s", state.value, msg)
            if on_progress:
                on_progress(p)

        _progress(SetupState.CHECKING_SYSTEM, "システムを確認しています...")
        self._system_info = probe_system(paths.runtime_root)
        try:
            validate_system(self._system_info)
        except SystemUnsupportedError:
            self._state.set_setup_state(SetupState.FAILED)
            raise

        rm = self._load_runtime_manifest()

        _progress(SetupState.PREPARING_RUNTIME_DIR, "runtimeディレクトリを準備しています...")
        paths.uv_dir.mkdir(parents=True, exist_ok=True)

        _progress(SetupState.INSTALLING_UV, "uvをセットアップしています...")
        uv = await UvManager.ensure(paths.uv_dir, rm.uv_windows_url, rm.uv_sha256)

        _progress(SetupState.INSTALLING_PYTHON, "Python環境を準備しています...")
        _progress(SetupState.CREATING_VENV, "仮想環境を作成しています...")
        if not paths.venv_dir.exists():
            uv.create_venv(paths.venv_dir, rm.python_version)

        _progress(SetupState.INSTALLING_TORCH, "PyTorchをインストールしています（時間がかかります）...")
        await install_torch(
            paths.venv_dir, uv,
            rm.torch_version, rm.torchvision_version, rm.torchaudio_version,
            rm.torch_cuda_variant, rm.torch_index_url,
        )

        _progress(SetupState.INSTALLING_COMFYUI, "ComfyUIをセットアップしています...")
        await install_comfyui(
            paths.comfyui_dir, rm.comfyui_zip_url, rm.comfyui_commit,
            paths.venv_dir, uv, paths.downloads_dir,
        )

        cn_manifest = self._load_custom_nodes_manifest()
        _progress(SetupState.INSTALLING_CUSTOM_NODES, "custom nodesをインストールしています...")
        await install_custom_nodes(paths.custom_nodes_dir, cn_manifest, paths.venv_dir, uv, paths.downloads_dir)

        model_manifest = self._load_model_manifest()
        installed = self._settings.installed_presets
        from ..runtime.model_installer import check_disk_space
        try:
            check_disk_space(paths.runtime_root, model_manifest, presets=installed)
        except DiskSpaceError:
            self._state.set_setup_state(SetupState.FAILED)
            raise
        _progress(SetupState.DOWNLOADING_MODELS, "モデルをダウンロードしています（時間がかかります）...")

        def _model_progress(name: str, done: int, total: int, file_idx: int = 0, total_files: int = 0) -> None:
            pct = done / total * 100 if total > 0 else 0
            if on_progress:
                msg = f"モデルDL中 ({file_idx}/{total_files}): {name}" if total_files else f"モデルDL中: {name}"
                on_progress(SetupProgress(
                    state=SetupState.DOWNLOADING_MODELS,
                    message=msg,
                    percent=pct,
                ))

        await install_models(paths.runtime_root, model_manifest, presets=installed, progress_cb=_model_progress)

        _progress(SetupState.VERIFYING_FILES, "ファイルを検証しています...")
        check_required_models_present(paths.runtime_root, model_manifest, presets=installed)

        _progress(SetupState.BUILDING_WORKFLOW_TEMPLATE, "workflowを準備しています...")
        load_and_sanitize(
            paths.api_source_json(),
            paths.runtime_template_json(),
            paths.workflow_dir / "workflow_analysis_report.md",
        )

        _progress(SetupState.VALIDATING_COMFYUI, "ComfyUIを検証しています...")
        self._server = ComfyServerController(paths.python_exe, paths.comfyui_dir, paths.comfyui_log)
        self._server.start(extra_flags=self._vram_flags())
        from ..comfy.api_client import ComfyApiClient
        client = ComfyApiClient(self._server.base_url)
        await client.wait_until_ready(timeout_sec=COMFY_STARTUP_TIMEOUT)

        object_info = await client.get_object_info()
        missing = await verify_required_classes(object_info, cn_manifest)
        if missing:
            self._server.stop()
            raise MissingNodeError(missing)

        _progress(SetupState.READY, "セットアップ完了！", pct=100.0)

    def _vram_flags(self) -> list[str]:
        from ..constants import VRAM_MODE_FLAGS
        return VRAM_MODE_FLAGS.get(self._settings.vram_mode, [])

    async def ensure_ready(self, on_progress: SetupCallback | None = None) -> None:
        if not self._state.is_ready:
            await self.setup(on_progress)
        else:
            # setup() を経由しないパス (再起動後など) でも system_info を確保する
            if self._system_info is None:
                self._system_info = probe_system(self._paths.runtime_root)
                try:
                    validate_system(self._system_info)
                except SystemUnsupportedError:
                    self._state.set_setup_state(SetupState.FAILED)
                    raise
            if self._server is None or not self._server.is_running:
                paths = self._paths
                self._server = ComfyServerController(paths.python_exe, paths.comfyui_dir, paths.comfyui_log)
                self._server.start(extra_flags=self._vram_flags())
                from ..comfy.api_client import ComfyApiClient
                client = ComfyApiClient(self._server.base_url)
                await client.wait_until_ready(timeout_sec=COMFY_STARTUP_TIMEOUT)

    def get_job_controller(self) -> JobController:
        if self._job_ctrl is None:
            template = self._load_workflow_template()
            assert self._server is not None
            gpu_info    = self._system_info.primary_gpu if self._system_info else None
            ram_total   = self._system_info.ram_gb      if self._system_info else 0.0
            self._job_ctrl = JobController(
                self._paths, self._server, self._settings, template,
                gpu_info=gpu_info, ram_total_gb=ram_total,
            )
        return self._job_ctrl

    async def install_presets(
        self,
        presets: list[str],
        on_progress: SetupCallback | None = None,
    ) -> None:
        """インストール済み後に追加プリセット群のモデルをまとめてDLする。

        複数プリセットを同時に渡すと共有モデルは重複DLされず、
        全体進捗 (overall_percent) はそれらを合算した総ファイル数を基準に算出される。
        """
        from ..constants import _VALID_PRESETS
        for preset in presets:
            if preset not in _VALID_PRESETS:
                raise ValueError(f"不正なプリセット: {preset}")

        paths = self._paths
        model_manifest = self._load_model_manifest()

        from ..runtime.model_installer import check_disk_space, install_models, check_required_models_present
        check_disk_space(paths.runtime_root, model_manifest, presets=presets)

        def _model_progress(name: str, done: int, total: int, file_idx: int, total_files: int) -> None:
            if not on_progress:
                return
            file_pct = done / total * 100 if total > 0 else 0
            overall_pct = (
                ((file_idx - 1) + done / total) / total_files * 100
                if total_files > 0 and total > 0 else 0
            )
            on_progress(SetupProgress(
                state=SetupState.DOWNLOADING_MODELS,
                message=f"モデルDL中 ({file_idx}/{total_files}): {name}",
                percent=file_pct,
                overall_percent=overall_pct,
            ))

        await install_models(paths.runtime_root, model_manifest, presets=presets, progress_cb=_model_progress)
        check_required_models_present(paths.runtime_root, model_manifest, presets=presets)
        for preset in presets:
            self._settings.add_installed_preset(preset)

    def stop_server(self) -> None:
        if self._server:
            self._server.stop()
