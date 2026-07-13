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


def is_model_valid(path: Path, entry: ModelEntry, level: str = "exists") -> bool:
    """モデルファイルの妥当性を判定する。

    「存在する」ことと「正常(hash)である」ことを区別するための判定関数。
    存在チェックのみだとサイズ0や途中DLの破損ファイルを導入済みとみなしてしまうため、
    通常起動時も size_bytes 一致まで確認する軽量判定を既定とする。

    level="exists": 存在し、size_bytes が一致すること(0や途中DLを弾く軽量判定。通常起動向け)。
        - entry.size_bytes が 0/未設定なら存在のみで可とする。
    level="hash":    さらに SHA-256 が一致すること(DL完了直後・修復時の厳密判定)。
        - entry.sha256 が未設定なら size 一致のみで可とする(verify_sha256 側の仕様に準拠)。
    """
    if not path.exists():
        return False
    if entry.size_bytes and path.stat().st_size != entry.size_bytes:
        return False
    if level == "hash":
        try:
            verify_sha256(path, entry.sha256)
        except Exception:
            return False
    return True


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
        if not m.required_manual and not is_model_valid(runtime_root / m.target, m, "exists")
    )
    needed_gb = missing_bytes / (1024 ** 3) + buffer_gb
    import shutil
    stat = shutil.disk_usage(runtime_root)
    free_gb = stat.free / (1024 ** 3)
    logger.info("ディスク空き: %.1f GB / 必要: %.1f GB", free_gb, needed_gb)
    if free_gb < needed_gb:
        raise DiskSpaceError(required_gb=needed_gb, available_gb=free_gb)


def get_missing_models(
    runtime_root: Path,
    manifest: ModelManifest,
    presets: list[str] | None = None,
) -> list[ModelEntry]:
    if presets is None:
        presets = ["normal"]
    missing = []
    for model in _needed_models(manifest, presets):
        if not is_model_valid(runtime_root / model.target, model, "exists"):
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
        if not m.required_manual and not is_model_valid(runtime_root / m.target, m, "exists")
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
        if target.exists():
            if is_model_valid(target, model, "hash"):
                logger.info("モデル確認OK: %s", model.name)
                continue
            logger.warning("SHA256不一致。再DLします: %s", model.name)

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
            progress_cb=_cb,
        )


def workflow_models(manifest: ModelManifest, workflow_id: str) -> list[ModelEntry]:
    """指定ワークフロー専用 (workflows に workflow_id を含む) のモデルを返す。"""
    return [m for m in manifest.models if workflow_id in m.workflows]


def get_missing_workflow_models(
    runtime_root: Path,
    manifest: ModelManifest,
    workflow_id: str,
) -> list[ModelEntry]:
    """指定ワークフローに必要だが未配置のモデルを返す。"""
    missing = []
    for m in workflow_models(manifest, workflow_id):
        if not m.required or m.required_manual:
            continue
        if not is_model_valid(runtime_root / m.target, m, "exists"):
            missing.append(m)
    return missing


async def install_specific_models(
    runtime_root: Path,
    models: list[ModelEntry],
    progress_cb: ProgressCallback | None = None,
) -> None:
    """指定したモデル群をDLする (オンデマンドDL用)。既に正しく存在する物はスキップ。"""
    to_download: list[ModelEntry] = []
    for model in models:
        if not model.required:
            continue
        target = runtime_root / model.target
        if target.exists():
            if is_model_valid(target, model, "hash"):
                logger.info("モデル確認OK: %s", model.name)
                continue
            logger.warning("SHA256不一致。再DLします: %s", model.name)
        if model.required_manual:
            logger.warning("手動配置が必要なモデル: %s → %s", model.name, model.target)
            continue
        if not model.is_downloadable:
            logger.warning("DL不可モデル (source_url未設定): %s", model.name)
            continue
        to_download.append(model)

    total_files = len(to_download)
    for idx, model in enumerate(to_download, start=1):
        logger.info("モデルDL開始 (オンデマンド): %s", model.name)

        def _cb(downloaded: int, total: int, name: str = model.name, idx: int = idx, total_files: int = total_files) -> None:
            if progress_cb:
                progress_cb(name, downloaded, total, idx, total_files)

        await download_file(
            model.source_url,
            runtime_root / model.target,
            sha256=model.sha256,
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
        if not is_model_valid(runtime_root / model.target, model, "exists"):
            missing_names.append(model.name)
    if missing_names:
        raise MissingModelError(missing_names)
