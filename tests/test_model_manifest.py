import json
import re
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


MANIFEST_PATH = Path(__file__).parent.parent / "app" / "manifest" / "model_manifest.json"

REQUIRED_FIELDS = ["name", "type", "target", "source_url", "sha256", "size_bytes", "license"]

PLACEHOLDER_VALUES = {"to_be_confirmed", "tbd", "unknown", "placeholder"}

SHA256_HEX_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _load_models():
    data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return data["models"]


def test_no_license_placeholder():
    models = _load_models()
    offenders = []
    for m in models:
        license_value = m.get("license", "")
        if not license_value or license_value.strip().lower() in PLACEHOLDER_VALUES:
            offenders.append(m.get("name", "<unknown>"))
    assert not offenders, f"license未確定のモデルが残っています: {offenders}"


def test_required_fields_present():
    models = _load_models()
    for m in models:
        missing = [f for f in REQUIRED_FIELDS if f not in m]
        assert not missing, f"{m.get('name', '<unknown>')} に必須フィールドが不足: {missing}"


def test_sha256_format():
    models = _load_models()
    for m in models:
        sha = m.get("sha256", "")
        assert SHA256_HEX_RE.match(sha), f"{m.get('name')} のsha256が64桁hexではありません: {sha!r}"


def test_target_no_traversal():
    models = _load_models()
    for m in models:
        target = m.get("target", "")
        assert not Path(target).is_absolute(), f"{m.get('name')} のtargetが絶対パスです: {target!r}"
        assert ".." not in Path(target).parts, f"{m.get('name')} のtargetにトラバーサルが含まれます: {target!r}"
        assert target.startswith("ComfyUI/"), f"{m.get('name')} のtargetがComfyUI/配下ではありません: {target!r}"


def test_no_duplicate_targets():
    models = _load_models()
    targets = [m.get("target") for m in models]
    duplicates = {t for t in targets if targets.count(t) > 1}
    assert not duplicates, f"targetが重複しています: {duplicates}"


def test_size_positive():
    models = _load_models()
    for m in models:
        size = m.get("size_bytes")
        assert isinstance(size, int) and not isinstance(size, bool) and size > 0, (
            f"{m.get('name')} のsize_bytesが正の整数ではありません: {size!r}"
        )
