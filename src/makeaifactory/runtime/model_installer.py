from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from ..constants import DISK_BUFFER_GB
from ..domain.errors import DiskSpaceError, MissingModelError
from ..domain.manifest import ModelEntry, ModelManifest
from .downloader import download_file
from .hash_verifier import verify_sha256

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str, int, int], None]


def check_disk_space(runtime_root: Path, manifest: ModelManifest, buffer_gb: float = DISK_BUFFER_GB) -> None:
    import shutil
    # 未ダウンロードのモデルのみを集計（既存ファイルは除外）
    missing_bytes = sum(
        m.size_bytes for m in manifest.models
        if m.required and not m.required_manual and not (runtime_root / m.target).exists()
    )
    needed_gb = missing_bytes / (1024 ** 3) + buffer_gb
    stat = shutil.disk_usage(runtime_root)
    free_gb = stat.free / (1024 ** 3)
    logger.info("ディスク空き: %.1f GB / 必要: %.1f GB", free_gb, needed_gb)
    if free_gb < needed_gb:
        raise DiskSpaceError(required_gb=needed_gb, available_gb=free_gb)


def get_missing_models(runtime_root: Path, manifest: ModelManifest) -> list[ModelEntry]:
    missing = []
    for model in manifest.models:
        target = runtime_root / model.target
        if not target.exists():
            missing.append(model)
    return missing


async def install_models(
    runtime_root: Path,
    manifest: ModelManifest,
    progress_cb: ProgressCallback | None = None,
) -> None:
    for model in manifest.models:
        if not model.required:
            continue
        target = runtime_root / model.target
        if target.exists():
            try:
                verify_sha256(target, model.sha256)
                logger.info("モデル確認OK: %s", model.name)
                continue
            except Exception:
                logger.warning("SHA256不一致。再DLします: %s", model.name)

        if model.required_manual:
            logger.warning("手動配置が必要なモデル: %s → %s", model.name, model.target)
            continue

        if not model.is_downloadable:
            logger.warning("DL不可モデル (source_url未設定): %s", model.name)
            continue

        logger.info("モデルDL開始: %s", model.name)

        def _cb(downloaded: int, total: int, name: str = model.name) -> None:
            if progress_cb:
                progress_cb(name, downloaded, total)

        await download_file(
            model.source_url,
            runtime_root / model.target,
            sha256=model.sha256,
            progress_cb=_cb,
        )


def check_required_models_present(runtime_root: Path, manifest: ModelManifest) -> None:
    missing_names = []
    for model in manifest.models:
        if not model.required or model.required_manual:
            continue
        target = runtime_root / model.target
        if not target.exists():
            missing_names.append(model.name)
    if missing_names:
        raise MissingModelError(missing_names)
