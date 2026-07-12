from __future__ import annotations

import json
import logging
from pathlib import Path

from ..core.atomic_json import read_json_or_default, write_json_atomic
from ..domain.progress import SetupState

logger = logging.getLogger(__name__)

_STATE_FILE = "runtime_state.json"


class RuntimeState:
    def __init__(self, runtime_root: Path):
        self._path = runtime_root / _STATE_FILE
        self._data: dict = {}
        self._load()

    def _load(self) -> None:
        # 壊れたJSONは read_json_or_default 内で ".corrupt" へ隔離される
        self._data = read_json_or_default(self._path, {})

    def _save(self) -> None:
        write_json_atomic(self._path, self._data)

    @property
    def setup_state(self) -> SetupState:
        raw = self._data.get("setup_state", SetupState.NOT_INSTALLED.value)
        try:
            return SetupState(raw)
        except ValueError:
            return SetupState.NOT_INSTALLED

    def set_setup_state(self, state: SetupState) -> None:
        self._data["setup_state"] = state.value
        self._save()
        logger.debug("RuntimeState: %s", state.value)

    @property
    def is_ready(self) -> bool:
        return self.setup_state == SetupState.READY

    @property
    def sage_attention_available(self) -> bool:
        return bool(self._data.get("sage_attention_available", False))

    def set_sage_attention_available(self, available: bool) -> None:
        self._data["sage_attention_available"] = available
        self._save()
        logger.info("RuntimeState: sage_attention_available=%s", available)

    @property
    def sage_attention_checked(self) -> bool:
        return bool(self._data.get("sage_attention_checked", False))

    def set_sage_attention_checked(self, checked: bool) -> None:
        self._data["sage_attention_checked"] = checked
        self._save()
