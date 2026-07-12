import tempfile
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from makeaifactory.core.settings_store import SettingsStore
from makeaifactory.gui.first_run_dialog import CURRENT_TERMS_VERSION


def test_accepted_terms_version_default_is_zero():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "settings.json"
        settings = SettingsStore(path)

        assert settings.accepted_terms_version == 0


def test_set_accepted_terms_version_updates_value():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "settings.json"
        settings = SettingsStore(path)

        settings.set_accepted_terms_version(1)

        assert settings.accepted_terms_version == 1


def test_accepted_terms_version_persists_across_reload():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "settings.json"
        settings = SettingsStore(path)
        settings.set_accepted_terms_version(1)

        reloaded = SettingsStore(path)

        assert reloaded.accepted_terms_version == 1


def test_current_terms_version_is_valid_int():
    assert isinstance(CURRENT_TERMS_VERSION, int)
    assert CURRENT_TERMS_VERSION >= 1


def test_unagreed_version_requires_reagreement():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "settings.json"
        settings = SettingsStore(path)

        # 未同意(既定値0)の場合は再同意が必要と判定されること
        assert settings.accepted_terms_version < CURRENT_TERMS_VERSION


def test_agreed_current_version_does_not_require_reagreement():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "settings.json"
        settings = SettingsStore(path)
        settings.set_accepted_terms_version(CURRENT_TERMS_VERSION)

        assert not (settings.accepted_terms_version < CURRENT_TERMS_VERSION)
