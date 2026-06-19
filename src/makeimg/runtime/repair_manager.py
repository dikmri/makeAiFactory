from __future__ import annotations

import logging
import shutil
from pathlib import Path

from ..domain.progress import SetupState
from .runtime_state import RuntimeState

logger = logging.getLogger(__name__)


class RepairManager:
    def __init__(self, runtime_root: Path, state: RuntimeState):
        self._root = runtime_root
        self._state = state

    def reset_to_state(self, target_state: SetupState) -> None:
        logger.info("runtimeを %s 状態にリセットします", target_state.value)
        self._state.set_setup_state(target_state)

    def reset_comfyui(self) -> None:
        comfyui_dir = self._root / "ComfyUI"
        if comfyui_dir.exists():
            logger.info("ComfyUIを削除します: %s", comfyui_dir)
            shutil.rmtree(comfyui_dir, ignore_errors=True)
        self._state.set_setup_state(SetupState.INSTALLING_COMFYUI)

    def reset_custom_nodes(self) -> None:
        node_dir = self._root / "ComfyUI" / "custom_nodes"
        if node_dir.exists():
            for child in node_dir.iterdir():
                if child.is_dir():
                    logger.info("custom_node削除: %s", child.name)
                    shutil.rmtree(child, ignore_errors=True)
        self._state.set_setup_state(SetupState.INSTALLING_CUSTOM_NODES)

    def full_reset(self) -> None:
        logger.warning("runtimeを完全リセットします")
        for child in self._root.iterdir():
            if child.name != "runtime_state.json":
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
        self._state.set_setup_state(SetupState.NOT_INSTALLED)
