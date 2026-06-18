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

ProgressCallback = Callable[[str, int, int, int, int], None]  # name, downloaded, total, file_index, total_files


def _needed_models(manifest: ModelManifest, presets: list[str]) -> list[ModelEntry]:
    """指定プリセット群で必要なモデルエントリを返す（重複なし）。"""
    result: list[ModelEntry] = []
    seen: set[str] = set()
    for m in manifest.models:
        if m.name in seen:
            continue
        # presets が空 → 共有モデル (常に必要)
        # presets に値 → 指定プリセットのどれかに含まれるなら必要
        if m.is_shared or any(p in presets for p in m.presets):
            result.append(m)
            seen.add(m.name)
    return result


def check_disk_space(
    runtime_root: Path,
    manifest: ModelManifest,
    presets: list[str] | None = None,
    buffer_gb: float = DISK_BUFFER_GB,
) -> None:
    if presets is None:
        presets = ["normal"]
    models = _needed_models(manifest, presets)
    missing_bytes = sum(
        m.size_bytes for m in models
        if not m.required_manual and not _is_valid_model(runtime_root, m)
    )
    needed_gb = missing_bytes / (1024 ** 3) + buffer_gb
    import shutil
    stat = shutil.disk_usage(runtime_root)
    free_gb = stat.free / (1024 ** 3)
    logger.info("ディスク空き: %.1f GB / 必要: %.1f GB", free_gb, needed_gb)
    if free_gb < needed_gb:
        raise DiskSpaceError(required_gb=needed_gb, available_gb=free_gb)


def _is_valid_model(runtime_root: Path, model: ModelEntry) -> bool:
    """モデルファイルが存在し、期待サイズと一致するか確認する。"""
    target = runtime_root / model.target
    if not target.exists():
        return False
    if model.size_bytes > 0:
        actual = target.stat().st_size
        expected = model.size_bytes
        tolerance = max(expected * 0.01, 1024)
        if abs(actual - expected) > tolerance:
            logger.warning(
                "サイズ不一致: %s (actual=%d, expected=%d)",
                model.name, actual, expected,
            )
            return False
    return True


def get_missing_models(
    runtime_root: Path,
    manifest: ModelManifest,
    presets: list[str] | None = None,
) -> list[ModelEntry]:
    if presets is None:
        presets = ["normal"]
    missing = []
    for model in _needed_models(manifest, presets):
        if not _is_valid_model(runtime_root, model):
            missing.append(model)
    return missing


def estimate_download_bytes(
    runtime_root: Path,
    manifest: ModelManifest,
    presets: list[str],
) -> int:
    """指定プリセット追加インストール時の未ダウンロード合計バイト数。"""
    return sum(
        m.size_bytes
        for m in _needed_models(manifest, presets)
        if not m.required_manual and not _is_valid_model(runtime_root, m)
    )


async def install_models(
    runtime_root: Path,
    manifest: ModelManifest,
    presets: list[str] | None = None,
    progress_cb: ProgressCallback | None = None,
) -> None:
    if presets is None:
        presets = ["normal"]

    to_download: list[ModelEntry] = []
    for model in _needed_models(manifest, presets):
        if not model.required:
            continue
        target = runtime_root / model.target

        if _is_valid_model(runtime_root, model):
            if model.sha256:
                try:
                    verify_sha256(target, model.sha256)
                    logger.info("モデル確認OK: %s", model.name)
                    continue
                except Exception:
                    logger.warning("SHA256不一致。再DLします: %s", model.name)
            else:
                logger.info("モデル確認OK: %s", model.name)
                continue
        else:
            if target.exists():
                logger.warning("サイズ不正のため再DLします: %s", model.name)
                target.unlink()

        if model.required_manual:
            logger.warning("手動配置が必要なモデル: %s → %s", model.name, model.target)
            continue

        if not model.is_downloadable:
            logger.warning("DL不可モデル (source_url未設定): %s", model.name)
            continue

        to_download.append(model)

    total_files = len(to_download)
    for idx, model in enumerate(to_download, start=1):
        logger.info("モデルDL開始: %s", model.name)

        def _cb(downloaded: int, total: int, name: str = model.name, idx: int = idx, total_files: int = total_files) -> None:
            if progress_cb:
                progress_cb(name, downloaded, total, idx, total_files)

        await download_file(
            model.source_url,
            runtime_root / model.target,
            sha256=model.sha256,
            expected_size=model.size_bytes,
            progress_cb=_cb,
        )


def check_required_models_present(
    runtime_root: Path,
    manifest: ModelManifest,
    presets: list[str] | None = None,
) -> None:
    if presets is None:
        presets = ["normal"]
    missing_names = []
    for model in _needed_models(manifest, presets):
        if not model.required or model.required_manual:
            continue
        if not _is_valid_model(runtime_root, model):
            missing_names.append(model.name)
    if missing_names:
        raise MissingModelError(missing_names)
