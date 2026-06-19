from __future__ import annotations

import logging
import re
import zipfile
from pathlib import Path

from ..domain.errors import SetupError
from ..domain.manifest import CustomNodeEntry, CustomNodesManifest
from .downloader import download_file
from .uv_manager import UvManager

logger = logging.getLogger(__name__)

# Python 3.13 / numpy 2.x で非互換なパッケージの exact pin を修正するマッピング
_REQ_OVERRIDES: list[tuple[re.Pattern, str]] = [
    # scikit-image==0.20.x 等の古い exact pin → >=0.25.0 (wheel あり)
    (re.compile(r"scikit[-_]image\s*==\s*0\.(1\d|2[0-4])\.\d+", re.IGNORECASE), "scikit-image>=0.25.0"),
    # opencv-python 4.8.x 以前は numpy 1.x 専用ビルドのため numpy 2.x で動かない
    (re.compile(r"opencv[-_]python\s*[<>=!].*", re.IGNORECASE), "opencv-python>=4.10.0"),
]


def _patch_requirements_file(req_file: Path) -> None:
    """requirements.txt 内の Python 3.13 非互換な exact pin を安全なバージョンに書き換える。"""
    content = req_file.read_text(encoding="utf-8", errors="replace")
    patched = content
    for pattern, replacement in _REQ_OVERRIDES:
        patched = pattern.sub(replacement, patched)
    if patched != content:
        req_file.write_text(patched, encoding="utf-8")
        logger.info("requirements.txt パッチ適用: %s", req_file)


def _patch_all_requirements(node_dir: Path) -> None:
    for req_file in node_dir.rglob("requirements.txt"):
        try:
            _patch_requirements_file(req_file)
        except Exception as e:
            logger.warning("requirements.txt パッチ失敗 (%s): %s", req_file, e)


async def install_custom_nodes(
    custom_nodes_dir: Path,
    manifest: CustomNodesManifest,
    venv_path: Path,
    uv: UvManager,
    downloads_dir: Path,
) -> None:
    for node in manifest.custom_nodes:
        if node.zip_url in ("", "TO_BE_CONFIRMED"):
            logger.warning("custom node '%s' のURL未設定。スキップします。", node.name)
            continue
        if node.commit in ("", "PIN_COMMIT_HERE"):
            logger.warning("custom node '%s' のcommit未固定。スキップします。", node.name)
            continue
        await _install_one_node(node, custom_nodes_dir, venv_path, uv, downloads_dir)


async def _install_one_node(
    node: CustomNodeEntry,
    custom_nodes_dir: Path,
    venv_path: Path,
    uv: UvManager,
    downloads_dir: Path,
) -> None:
    import shutil

    node_dir = custom_nodes_dir / node.name
    if node_dir.exists():
        logger.info("custom node '%s' は既に存在します。スキップ。", node.name)
        return

    logger.info("custom node '%s' をインストールします", node.name)
    zip_path = downloads_dir / f"{node.name}-{node.commit[:8]}.zip"
    if not zip_path.exists():
        await download_file(node.zip_url, zip_path)

    node_dir.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            members = zf.namelist()
            prefix = members[0].split("/")[0] + "/"
            for member in members:
                target = node_dir / member[len(prefix):]
                if member.endswith("/"):
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(member) as src, target.open("wb") as dst:
                        dst.write(src.read())

        # Python 3.13 非互換な exact pin を全 requirements.txt から除去
        _patch_all_requirements(node_dir)

        if node.has_requirements:
            # トップレベルとサブディレクトリ1段目のrequirements.txtを両方試みる
            req_files = [node_dir / "requirements.txt"] + list(node_dir.glob("*/requirements.txt"))
            for req_file in req_files:
                if req_file.exists():
                    logger.info("  requirements インストール: %s (%s)", node.name, req_file.relative_to(node_dir))
                    uv.pip_install_requirements(venv_path, req_file)

    except Exception:
        # インストール失敗時はディレクトリを削除して次回の再試行を可能にする
        logger.warning("custom node '%s' インストール失敗。ディレクトリを削除します: %s", node.name, node_dir)
        shutil.rmtree(node_dir, ignore_errors=True)
        raise

    logger.info("custom node '%s' インストール完了", node.name)


async def verify_required_classes(
    object_info: dict,
    manifest: CustomNodesManifest,
) -> list[str]:
    """ComfyUIの/object_infoレスポンスと照合し、不足class_typeを返す。"""
    missing = []
    for node in manifest.custom_nodes:
        for cls in node.required_classes:
            if cls not in object_info:
                missing.append(cls)
    return missing
