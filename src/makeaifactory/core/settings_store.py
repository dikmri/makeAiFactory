from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULTS = {
    "seed_randomize": False,
    "save_base_video": False,
    "developer_mode": False,
    "agreed_to_terms": False,
}


class SettingsStore:
    def __init__(self, config_path: Path):
        self._path = config_path
        self._data: dict = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                with self._path.open("r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except Exception as e:
                logger.warning("設定ファイル読み込み失敗。デフォルト値を使用します: %s", e)
                self._data = {}
        else:
            self._data = {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def get(self, key: str):
        return self._data.get(key, _DEFAULTS.get(key))

    def set(self, key: str, value) -> None:
        self._data[key] = value
        self._save()

    @property
    def seed_randomize(self) -> bool:
        return bool(self.get("seed_randomize"))

    @property
    def save_base_video(self) -> bool:
        return bool(self.get("save_base_video"))

    @property
    def developer_mode(self) -> bool:
        return bool(self.get("developer_mode"))

    @property
    def agreed_to_terms(self) -> bool:
        return bool(self.get("agreed_to_terms"))

    def agree_to_terms(self) -> None:
        self.set("agreed_to_terms", True)
