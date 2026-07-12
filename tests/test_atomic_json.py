import json
import tempfile
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from makeaifactory.core.atomic_json import read_json_or_default, write_json_atomic


def test_write_then_read_roundtrip():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "data.json"
        data = {"a": 1, "b": ["x", "y"], "c": {"nested": True}}

        write_json_atomic(path, data)
        result = read_json_or_default(path, None)

        assert result == data


def test_no_temp_left():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "data.json"

        write_json_atomic(path, {"foo": "bar"})

        tmp_files = list(Path(tmpdir).glob("*.tmp"))
        assert tmp_files == []


def test_backup_created_on_overwrite():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "data.json"

        write_json_atomic(path, {"version": 1})
        write_json_atomic(path, {"version": 2}, make_backup=True)

        backup_path = path.with_suffix(path.suffix + ".bak")
        assert backup_path.exists()
        with backup_path.open("r", encoding="utf-8") as f:
            backup_data = json.load(f)
        assert backup_data == {"version": 1}

        with path.open("r", encoding="utf-8") as f:
            current_data = json.load(f)
        assert current_data == {"version": 2}


def test_read_default_when_missing():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "does_not_exist.json"

        result = read_json_or_default(path, {"default": True})

        assert result == {"default": True}


def test_corrupt_quarantined():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "data.json"
        path.write_text("{ this is not valid json", encoding="utf-8")

        result = read_json_or_default(path, {"default": True})

        assert result == {"default": True}
        corrupt_path = path.with_suffix(path.suffix + ".corrupt")
        assert corrupt_path.exists()
        assert not path.exists()


def test_atomic_replace_via_tmp():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "data.json"

        write_json_atomic(path, {"ok": True, "values": [1, 2, 3]})

        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        assert data == {"ok": True, "values": [1, 2, 3]}
