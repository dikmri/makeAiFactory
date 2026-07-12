from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from .atomic_json import write_json_atomic

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
        # 90秒間隔で頻繁に呼ばれるファイルのため、毎回 ".bak" を作ると
        # 書き込み回数・IOが倍増するだけで得られる価値が薄い。
        # 直近の状態を失っても discord_bot_controller 側は次の更新で
        # 追いつけるため、ここでは make_backup=False とする。
        write_json_atomic(
            path,
            {"state": state, "port": port, "updated_at": time.time()},
            make_backup=False,
        )
    except Exception as e:
        logger.debug("bot_state.json 書き込み失敗: %s", e)


def read_bot_state(runtime_root: Path) -> tuple[str, int]:
    """bot_state.json から (state, comfy_port) を読む。
    5分以上更新されていない場合は "offline"。
    """
    path = runtime_root / _STATE_FILENAME
    if not path.exists():
        return "offline", 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - data.get("updated_at", 0) > 300:
            return "offline", data.get("port", 0)
        return data.get("state", "offline"), data.get("port", 0)
    except Exception:
        return "offline", 0
