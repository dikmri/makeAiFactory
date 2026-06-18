from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_CONFIG_FILENAME = "makeImg_install.json"


def get_config_path(exe_dir: Path) -> Path:
    return exe_dir / _CONFIG_FILENAME


def load_runtime_config(exe_dir: Path) -> Path | None:
    """保存済みのruntime_rootを返す。未設定または読み込み失敗時はNone。"""
    path = get_config_path(exe_dir)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        rt = data.get("runtime_root")
        if rt:
            return Path(rt)
    except Exception as e:
        logger.warning("install config 読み込み失敗: %s", e)
    return None


def save_runtime_config(exe_dir: Path, runtime_root: Path) -> None:
    """runtime_rootをEXE横の設定ファイルに保存する。"""
    path = get_config_path(exe_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump({"runtime_root": str(runtime_root)}, f, indent=2)
        logger.info("install config 保存: %s", runtime_root)
    except Exception as e:
        logger.warning("install config 保存失敗: %s", e)
