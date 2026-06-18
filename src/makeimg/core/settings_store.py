from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULTS = {
    "agreed_to_terms": False,
    "vram_mode": "normal",
    "model_preset": "normal",
    "installed_presets": ["normal"],
    "sage_attention_enabled": False,
    "auto_save_folder": "",
    "auto_save_enabled": False,
    "se_enabled": True,
    "se_volume": 75,
    "always_on_top": False,
    "positive_prompt": ",very aesthetic, masterpiece, best quality, ultra-detailed,miaoka, high contrast,huge filesize,\nslightly overexposed,mild film grain,faded color,low saturation,\n(1girls),\nskinny,slim,,\n\ndetailed nipples,detailed skin,realistic skin,\n\n,hachimiya meguru, idolmaster shiny colors,,tsukioka kogane,idolmaster shiny colors,,\n\n,(sagging breasts:1.2),,\n\nbare breasts, bare pussy, bare nipples, bare areolae, \nnipples,\n\n,(huge breasts:1.5),,\n\n,(large areolae),,\n,puffy nipples,,\n\nexplicit, \n\nsex, vaginal sex, penis, pussy, pussy juice, blush, 1boy, \nback, , \n\nseductive smile, , \n, , \n, , \ngrabbing another's ass, , \n\ndetailed background, \non bed, indoor, bedroom, at night,,",
    "negative_prompt": "bad anatomy, worst quality, low quality, text, motion lines, motion blur, (logo), (multiple views), mosaic censoring, censored, bar censor, framed, black border, turn pale, lowres, text, word, text watermark, artist logo, patreon logo,long body,bad hands,\n\n",
    "width": 1280,
    "height": 720,
    "seed_mode": "random",
    "seed_value": 0,
    "naming_pattern": "{timestamp}_{seed}",
    "active_workflow": "画像用_master_api.json",
    "last_preset": "",
    "se_batch_mode": "final",
    "gaming_skin": True,
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
        from ..constants import _VALID_PRESETS
        v = str(self.get("model_preset") or "normal")
        return v if v in _VALID_PRESETS else "normal"

    def set_model_preset(self, preset: str) -> None:
        self.set("model_preset", preset)

    @property
    def installed_presets(self) -> list[str]:
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
    def always_on_top(self) -> bool:
        return bool(self.get("always_on_top"))

    def set_always_on_top(self, enabled: bool) -> None:
        self.set("always_on_top", enabled)

    @property
    def positive_prompt(self) -> str:
        return str(self.get("positive_prompt") or "")

    def set_positive_prompt(self, text: str) -> None:
        self.set("positive_prompt", text)

    @property
    def negative_prompt(self) -> str:
        return str(self.get("negative_prompt") or "")

    def set_negative_prompt(self, text: str) -> None:
        self.set("negative_prompt", text)

    @property
    def width(self) -> int:
        return int(self.get("width") or 1280)

    def set_width(self, w: int) -> None:
        self.set("width", w)

    @property
    def height(self) -> int:
        return int(self.get("height") or 720)

    def set_height(self, h: int) -> None:
        self.set("height", h)

    @property
    def active_workflow(self) -> str:
        return str(self.get("active_workflow") or "画像用_master_api.json")

    def set_active_workflow(self, name: str) -> None:
        self.set("active_workflow", name)

    @property
    def seed_mode(self) -> str:
        return str(self.get("seed_mode") or "random")

    def set_seed_mode(self, mode: str) -> None:
        self.set("seed_mode", mode)

    @property
    def seed_value(self) -> int:
        return int(self.get("seed_value") or 0)

    def set_seed_value(self, value: int) -> None:
        self.set("seed_value", value)

    @property
    def naming_pattern(self) -> str:
        return str(self.get("naming_pattern") or "{timestamp}_{seed}")

    def set_naming_pattern(self, pattern: str) -> None:
        self.set("naming_pattern", pattern)

    @property
    def prompt_presets(self) -> list[dict]:
        v = self.get("prompt_presets")
        if isinstance(v, list):
            return v
        return []

    def add_prompt_preset(self, name: str, positive: str, negative: str) -> None:
        presets = self.prompt_presets
        for p in presets:
            if p.get("name") == name:
                p["positive"] = positive
                p["negative"] = negative
                self.set("prompt_presets", presets)
                return
        presets.append({"name": name, "positive": positive, "negative": negative})
        self.set("prompt_presets", presets)

    def remove_prompt_preset(self, name: str) -> None:
        presets = self.prompt_presets
        presets = [p for p in presets if p.get("name") != name]
        self.set("prompt_presets", presets)

    def rename_prompt_preset(self, old_name: str, new_name: str) -> None:
        presets = self.prompt_presets
        for p in presets:
            if p.get("name") == old_name:
                p["name"] = new_name
                break
        self.set("prompt_presets", presets)

    @property
    def downloaded_models(self) -> list[str]:
        v = self.get("downloaded_models")
        if isinstance(v, list):
            return v
        return []

    def mark_model_downloaded(self, model_name: str) -> None:
        models = self.downloaded_models
        if model_name not in models:
            models.append(model_name)
            self.set("downloaded_models", models)

    def is_model_downloaded(self, model_name: str) -> bool:
        return model_name in self.downloaded_models

    @property
    def last_preset(self) -> str:
        return str(self.get("last_preset") or "")

    def set_last_preset(self, name: str) -> None:
        self.set("last_preset", name)

    @property
    def se_batch_mode(self) -> str:
        v = str(self.get("se_batch_mode") or "final")
        return v if v in ("each", "final") else "final"

    def set_se_batch_mode(self, mode: str) -> None:
        self.set("se_batch_mode", mode)

    @property
    def gaming_skin(self) -> bool:
        return bool(self.get("gaming_skin"))

    def set_gaming_skin(self, enabled: bool) -> None:
        self.set("gaming_skin", enabled)
