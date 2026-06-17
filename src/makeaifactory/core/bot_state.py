from __future__ import annotations

import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_STATE_FILENAME = "bot_state.json"


def write_bot_state(runtime_root: Path, state: str, port: int = 0) -> None:
    """Bot連携用状態ファイルを更新する。

    state: "idle" | "single" | "batch"
    port: ComfyUI のポート番号。0 の場合は既存ファイルの値を引き継ぐ。
    """
    try:
        path = runtime_root / _STATE_FILENAME
        if port == 0 and path.exists():
            try:
                port = json.loads(path.read_text(encoding="utf-8")).get("port", 0)
            except Exception:
                pass
        path.write_text(
            json.dumps({"state": state, "port": port, "updated_at": time.time()}),
            encoding="utf-8",
        )
    except Exception as e:
        logger.debug("bot_state.json 書き込み失敗: %s", e)
