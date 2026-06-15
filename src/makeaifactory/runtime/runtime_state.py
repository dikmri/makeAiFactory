from __future__ import annotations

import json
import logging
from pathlib import Path

from ..domain.progress import SetupState

logger = logging.getLogger(__name__)

_STATE_FILE = "runtime_state.json"


class RuntimeState:
    def __init__(self, runtime_root: Path):
        self._path = runtime_root / _STATE_FILE
        self._data: dict = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                with self._path.open("r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except Exception:
                self._data = {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2)

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
