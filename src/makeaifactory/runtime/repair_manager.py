from __future__ import annotations

import logging
import shutil
from pathlib import Path

from ..domain.progress import SetupState
from .runtime_state import RuntimeState

logger = logging.getLogger(__name__)


def can_start_repair(bot_state: str) -> bool:
    """修復を開始してよいかを bot_state (single/batch/idle/offline等) から判定する。

    生成ジョブ実行中 (single/batch) は、GUIスレッド操作やComfyUI停止が
    ジョブと競合するため修復を開始しない。それ以外 (idle/offline等) はOK。

    非推奨 (SCH-01/PR1): app.py の修復トリガーは GenerationGate.try_acquire("desktop")
    に置き換わった (read → 判定のTOCTOUを無くし、修復中は他経路からの生成も
    ブロックできるようにするため)。この関数自体は純ロジックとして他に実害が
    無いため削除はしていないが、新規の呼び出し追加は避けること。
    """
    return bot_state not in ("single", "batch")


class RepairManager:
    def __init__(self, runtime_root: Path, state: RuntimeState):
        self._root = runtime_root
        self._state = state

    def _assert_within_root(self, path: Path) -> None:
        """削除対象が runtime_root 配下であることを保証するガード。

        シンボリックリンクや将来の呼び出し追加で誤って runtime_root 外を
        指してしまった場合に、意図しない削除を未然に防ぐための安全弁。
        正当な対象 (常に self._root 配下に構成される) では通過するだけで
        挙動は変わらない。
        """
        root = self._root.resolve()
        target = path.resolve()
        if target != root and root not in target.parents:
            raise ValueError(f"runtime_root配下ではないパスは削除できません: {target}")

    def reset_to_state(self, target_state: SetupState) -> None:
        logger.info("runtimeを %s 状態にリセットします", target_state.value)
        self._state.set_setup_state(target_state)

    def reset_comfyui(self) -> None:
        comfyui_dir = self._root / "ComfyUI"
        if comfyui_dir.exists():
            self._assert_within_root(comfyui_dir)
            logger.info("ComfyUIを削除します: %s", comfyui_dir)
            shutil.rmtree(comfyui_dir, ignore_errors=True)
        self._state.set_setup_state(SetupState.INSTALLING_COMFYUI)

    def reset_custom_nodes(self) -> None:
        node_dir = self._root / "ComfyUI" / "custom_nodes"
        if node_dir.exists():
            self._assert_within_root(node_dir)
            for child in node_dir.iterdir():
                if child.is_dir():
                    self._assert_within_root(child)
                    logger.info("custom_node削除: %s", child.name)
                    shutil.rmtree(child, ignore_errors=True)
        self._state.set_setup_state(SetupState.INSTALLING_CUSTOM_NODES)

    def full_reset(self) -> None:
        logger.warning("runtimeを完全リセットします")
        for child in self._root.iterdir():
            if child.name != "runtime_state.json":
                self._assert_within_root(child)
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
        self._state.set_setup_state(SetupState.NOT_INSTALLED)
