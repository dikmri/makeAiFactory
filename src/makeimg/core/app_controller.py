from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Callable

from ..comfy.server_controller import ComfyServerController
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
    SetupProgress,
    SetupState,
)
from ..runtime.comfy_installer import install_comfyui, install_sage_attention, install_torch
from ..runtime.custom_node_installer import install_custom_nodes, verify_required_classes
from ..runtime.model_installer import (
    check_required_models_present,
    get_missing_models,
    install_models,
)
from ..runtime.runtime_state import RuntimeState
from ..runtime.system_probe import SystemInfo, probe_system, validate_system
from ..runtime.uv_manager import UvManager

logger = logging.getLogger(__name__)

SetupCallback = Callable[[SetupProgress], None]


class AppController:
    def __init__(self, paths: AppPaths, settings: SettingsStore):
        self._paths = paths
        self._settings = settings
        self._state = RuntimeState(paths.runtime_root)
        self._server: ComfyServerController | None = None
        self._job_ctrl: JobController | None = None
        self._system_info: SystemInfo | None = None
        self._workflow_cache: dict[str, dict] = {}

    def _load_runtime_manifest(self) -> RuntimeManifest:
        with self._paths.runtime_manifest_json().open("r", encoding="utf-8") as f:
            return RuntimeManifest.from_dict(json.load(f))

    def _load_model_manifest(self) -> ModelManifest:
        with self._paths.model_manifest_json().open("r", encoding="utf-8") as f:
            return ModelManifest.from_dict(json.load(f))

    def _load_custom_nodes_manifest(self) -> CustomNodesManifest:
        with self._paths.custom_nodes_manifest_json().open("r", encoding="utf-8") as f:
            return CustomNodesManifest.from_dict(json.load(f))

    def _load_workflow_template(self, workflow_name: str) -> dict:
        if workflow_name in self._workflow_cache:
            return self._workflow_cache[workflow_name]

        tpl_path = self._paths.runtime_template_json(workflow_name)
        if not tpl_path.exists():
            self._prepare_workflow(workflow_name)
        if not tpl_path.exists():
            raise SetupError(f"workflow templateが見つかりません: {workflow_name}")
        with tpl_path.open("r", encoding="utf-8") as f:
            wf = json.load(f)
        self._workflow_cache[workflow_name] = wf
        return wf

    def _prepare_workflow(self, workflow_name: str) -> None:
        source_path = self._paths.devs_dir / workflow_name
        if not source_path.exists():
            logger.warning("ワークフローファイルが見つかりません: %s", source_path)
            return
        dest_path = self._paths.runtime_template_json(workflow_name)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with source_path.open("r", encoding="utf-8") as f:
            wf = json.load(f)
        with dest_path.open("w", encoding="utf-8") as f:
            json.dump(wf, f, ensure_ascii=False, indent=2)
        logger.info("ワークフロー準備完了: %s → %s", source_path, dest_path)

    def list_workflows(self) -> list[str]:
        workflows = []
        devs_dir = self._paths.devs_dir
        if devs_dir.exists():
            for f in devs_dir.iterdir():
                if f.suffix == ".json":
                    workflows.append(f.name)
        return workflows

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

    @property
    def sage_attention_available(self) -> bool:
        return self._state.sage_attention_available and self._load_runtime_manifest().sageattn_enabled

    async def _ensure_sage_attention_installed(self, on_progress: SetupCallback | None = None) -> None:
        paths = self._paths
        rm = self._load_runtime_manifest()
        if not rm.sageattn_enabled:
            self._state.set_sage_attention_checked(True)
            self._state.set_sage_attention_available(False)
            return
        if on_progress:
            on_progress(SetupProgress(
                state=SetupState.INSTALLING_SAGEATTENTION,
                message="高速化モジュール (SageAttention) を確認しています...",
            ))
        paths.uv_dir.mkdir(parents=True, exist_ok=True)
        uv = await UvManager.ensure(paths.uv_dir, rm.uv_windows_url, rm.uv_sha256)
        sage_ok = await install_sage_attention(
            paths.venv_dir, uv,
            rm.sageattn_triton_version, rm.sageattn_wheel_url, rm.sageattn_wheel_sha256,
            paths.downloads_dir,
        )
        self._state.set_sage_attention_checked(True)
        self._state.set_sage_attention_available(sage_ok)
        logger.info("SageAttention利用可否: %s", sage_ok)

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

        _progress(SetupState.INSTALLING_TORCH, "PyTorchをインストールしています...")
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

        await self._ensure_sage_attention_installed(on_progress)

        model_manifest = self._load_model_manifest()
        installed = self._settings.installed_presets
        from ..runtime.model_installer import check_disk_space
        try:
            check_disk_space(paths.runtime_root, model_manifest, presets=installed)
        except DiskSpaceError:
            self._state.set_setup_state(SetupState.FAILED)
            raise
        _progress(SetupState.DOWNLOADING_MODELS, "モデルをダウンロードしています...")

        from ..runtime.direct_downloader import download_file
        from ..runtime.model_installer import get_missing_models

        async def _download_models() -> None:
            while True:
                missing = get_missing_models(paths.runtime_root, model_manifest, presets=installed)
                auto_models = [m for m in missing if not m.required_manual]
                if not auto_models:
                    break

                model = auto_models[0]
                target = paths.runtime_root / model.target

                def _cb(downloaded: int, total: int, name: str = model.name) -> None:
                    if total > 0:
                        pct = downloaded / total * 100
                        if on_progress:
                            on_progress(SetupProgress(
                                state=SetupState.DOWNLOADING_MODELS,
                                message=f"{name} をダウンロード中... {pct:.0f}%",
                                percent=pct,
                            ))
                    else:
                        mb = downloaded / (1024 * 1024)
                        if on_progress:
                            on_progress(SetupProgress(
                                state=SetupState.DOWNLOADING_MODELS,
                                message=f"{name} をダウンロード中... ({mb:.0f} MB)",
                                percent=-1,
                            ))

                await download_file(model.source_url, target, progress_cb=_cb, expected_size=model.size_bytes)
                logger.info("モデルDL完了: %s", model.name)

        await _download_models()

        _progress(SetupState.VERIFYING_FILES, "ファイルを検証しています...")
        check_required_models_present(paths.runtime_root, model_manifest, presets=installed)

        _progress(SetupState.BUILDING_WORKFLOW_TEMPLATE, "workflowを準備しています...")
        for wf_name in self.list_workflows():
            self._prepare_workflow(wf_name)

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
            return

        if self._system_info is None:
            self._system_info = probe_system(self._paths.runtime_root)
            try:
                validate_system(self._system_info)
            except SystemUnsupportedError:
                self._state.set_setup_state(SetupState.FAILED)
                raise

        if not self._state.sage_attention_checked:
            await self._ensure_sage_attention_installed(on_progress)

        model_manifest = self._load_model_manifest()
        missing = get_missing_models(self._paths.runtime_root, model_manifest, presets=self._settings.installed_presets)
        auto_missing = [m for m in missing if not m.required_manual and m.is_downloadable]
        if auto_missing:
            logger.info("不足モデル %d個を再ダウンロードします", len(auto_missing))
            from ..runtime.direct_downloader import download_file
            for model in auto_missing:
                target = self._paths.runtime_root / model.target
                if target.exists():
                    logger.warning("サイズ不正のため削除: %s", target)
                    target.unlink(missing_ok=True)

                def _cb(downloaded: int, total: int, name: str = model.name) -> None:
                    if on_progress and total > 0:
                        pct = downloaded / total * 100
                        on_progress(SetupProgress(
                            state=SetupState.DOWNLOADING_MODELS,
                            message=f"{name} をダウンロード中... {pct:.0f}%",
                            percent=pct,
                        ))

                await download_file(model.source_url, target, progress_cb=_cb, expected_size=model.size_bytes)
                logger.info("モデルDL完了: %s", model.name)
            check_required_models_present(self._paths.runtime_root, model_manifest, presets=self._settings.installed_presets)

        if self._server is None or not self._server.is_running:
            paths = self._paths
            self._server = ComfyServerController(paths.python_exe, paths.comfyui_dir, paths.comfyui_log)
            self._server.start(extra_flags=self._vram_flags())
            from ..comfy.api_client import ComfyApiClient
            client = ComfyApiClient(self._server.base_url)
            await client.wait_until_ready(timeout_sec=COMFY_STARTUP_TIMEOUT)

    def get_job_controller(self) -> JobController:
        template = self._load_workflow_template(self._settings.active_workflow)
        if self._server is None or not self._server.is_running:
            raise SetupError("ComfyUIサーバーが起動していません")
        gpu_info = self._system_info.primary_gpu if self._system_info else None
        ram_total = self._system_info.ram_gb if self._system_info else 0.0
        rm = self._load_runtime_manifest()
        self._job_ctrl = JobController(
            self._paths, self._server, self._settings, template,
            gpu_info=gpu_info, ram_total_gb=ram_total,
            sage_attention_mode=rm.sageattn_mode,
            sage_attention_available=self._state.sage_attention_available and rm.sageattn_enabled,
        )
        return self._job_ctrl

    async def install_presets(
        self,
        presets: list[str],
        on_progress: SetupCallback | None = None,
    ) -> None:
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

    def get_manual_models(self) -> list[dict]:
        """手動配置が必要なモデルのリストを返す。"""
        from ..runtime.model_installer import get_missing_models
        model_manifest = self._load_model_manifest()
        missing = get_missing_models(self._paths.runtime_root, model_manifest, presets=self._settings.installed_presets)
        manual_models = []
        for m in missing:
            if m.required_manual:
                manual_models.append({
                    "name": m.name,
                    "target": str(self._paths.runtime_root / m.target),
                    "note": m.note or "手動で配置してください",
                })
        return manual_models

    def get_missing_manual_models(self) -> list[dict]:
        """まだ配置されていない手動配置モデルのリストを返す。"""
        from ..runtime.model_installer import get_missing_models
        model_manifest = self._load_model_manifest()
        missing = get_missing_models(self._paths.runtime_root, model_manifest, presets=self._settings.installed_presets)
        result = []
        for m in missing:
            if m.required_manual:
                result.append({
                    "name": m.name,
                    "target": str(self._paths.runtime_root / m.target),
                    "note": m.note or "",
                })
        return result
