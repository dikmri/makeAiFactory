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
    "vram_mode": "normal",        # "normal" | "novram"
    "model_preset": "normal",     # "normal" | "lite" | "ultralite"
    "installed_presets": ["normal"],
    "sage_attention_enabled": False,  # 高速化(SageAttention)。未インストール環境では無視されdisabledのまま
    "auto_save_folder": "",       # 動画完成時の自動保存先フォルダ (パスのみ。有効/無効は別フラグ)
    "auto_save_enabled": False,   # 自動保存のON/OFF。フォルダ設定とは独立
    "batch_input_folder": "",     # フォルダ一括生成: 前回入力した入力フォルダ
    "batch_output_folder": "",    # フォルダ一括生成: 前回入力した出力フォルダ
    "se_enabled": True,            # 完成通知音のON/OFF (マスタースイッチ)
    "se_volume": 75,                # 完成通知音の音量 (0-100)
    "se_on_batch_complete": True,   # フォルダ(バッチ)生成完了時にも通知音を鳴らすか
    "always_on_top": False,         # ウィンドウを常に最前面に表示するか
    "discord_bot_enabled": False,
    "discord_token": "",
    "discord_channel_ids": [],      # list[int]
    "discord_bot_interrupt": False, # フォルダ生成中に Discord リクエストを割り込ませる
    "remote_room": {
        "room_ttl_minutes": 180,
        "require_pin": True,
        "max_upload_mb": 20,
        "max_queue_size": 3,
        "per_session_cooldown_seconds": 600,
        "output_retention_hours": 24,
    },
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

    @property
    def vram_mode(self) -> str:
        v = str(self.get("vram_mode"))
        return v if v in ("normal", "novram") else "normal"

    @property
    def model_preset(self) -> str:
        """現在アクティブなモデルプリセット。"""
        from ..constants import _VALID_PRESETS
        v = str(self.get("model_preset") or "normal")
        return v if v in _VALID_PRESETS else "normal"

    def set_model_preset(self, preset: str) -> None:
        self.set("model_preset", preset)

    @property
    def installed_presets(self) -> list[str]:
        """インストール済みプリセットのリスト。"""
        from ..constants import _VALID_PRESETS
        v = self.get("installed_presets")
        if isinstance(v, list) and v:
            return [p for p in v if p in _VALID_PRESETS]
        return ["normal"]

    def add_installed_preset(self, preset: str) -> None:
        presets = self.installed_presets
        if preset not in presets:
            presets.append(preset)
        self.set("installed_presets", presets)

    @property
    def sage_attention_enabled(self) -> bool:
        return bool(self.get("sage_attention_enabled"))

    def set_sage_attention_enabled(self, enabled: bool) -> None:
        self.set("sage_attention_enabled", enabled)

    @property
    def auto_save_folder(self) -> str:
        return str(self.get("auto_save_folder") or "")

    def set_auto_save_folder(self, folder: str) -> None:
        self.set("auto_save_folder", folder)

    @property
    def auto_save_enabled(self) -> bool:
        return bool(self.get("auto_save_enabled"))

    def set_auto_save_enabled(self, enabled: bool) -> None:
        self.set("auto_save_enabled", enabled)

    @property
    def batch_input_folder(self) -> str:
        return str(self.get("batch_input_folder") or "")

    def set_batch_input_folder(self, folder: str) -> None:
        self.set("batch_input_folder", folder)

    @property
    def batch_output_folder(self) -> str:
        return str(self.get("batch_output_folder") or "")

    def set_batch_output_folder(self, folder: str) -> None:
        self.set("batch_output_folder", folder)

    @property
    def se_enabled(self) -> bool:
        return bool(self.get("se_enabled"))

    def set_se_enabled(self, enabled: bool) -> None:
        self.set("se_enabled", enabled)

    @property
    def se_volume(self) -> int:
        v = int(self.get("se_volume") or 0)
        return min(100, max(0, v))

    def set_se_volume(self, volume: int) -> None:
        self.set("se_volume", min(100, max(0, int(volume))))

    @property
    def se_on_batch_complete(self) -> bool:
        return bool(self.get("se_on_batch_complete"))

    def set_se_on_batch_complete(self, enabled: bool) -> None:
        self.set("se_on_batch_complete", enabled)

    @property
    def always_on_top(self) -> bool:
        return bool(self.get("always_on_top"))

    def set_always_on_top(self, enabled: bool) -> None:
        self.set("always_on_top", enabled)

    @property
    def discord_bot_enabled(self) -> bool:
        return bool(self.get("discord_bot_enabled"))

    def set_discord_bot_enabled(self, enabled: bool) -> None:
        self.set("discord_bot_enabled", enabled)

    @property
    def discord_token(self) -> str:
        return str(self.get("discord_token") or "")

    def set_discord_token(self, token: str) -> None:
        self.set("discord_token", token)

    @property
    def discord_channel_ids(self) -> list[int]:
        v = self.get("discord_channel_ids")
        if isinstance(v, list):
            return [int(x) for x in v if isinstance(x, (int, float)) and int(x) > 0]
        return []

    def set_discord_channel_ids(self, ids: list[int]) -> None:
        self.set("discord_channel_ids", [int(x) for x in ids])

    @property
    def discord_bot_interrupt(self) -> bool:
        return bool(self.get("discord_bot_interrupt"))

    def set_discord_bot_interrupt(self, enabled: bool) -> None:
        self.set("discord_bot_interrupt", enabled)

    @property
    def dev_mode_params(self) -> dict:
        v = self.get("dev_mode_params")
        return v if isinstance(v, dict) else {}

    def set_dev_mode_params(self, params: dict) -> None:
        self.set("dev_mode_params", params)

    @property
    def remote_room_config(self) -> dict:
        v = self.get("remote_room")
        return v if isinstance(v, dict) else {}

    def set_remote_room_config(self, config: dict) -> None:
        self.set("remote_room", config)
