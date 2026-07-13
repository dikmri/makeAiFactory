"""is_model_valid() の検証: モデルの「存在」と「正常(hash)」を区別できているかのテスト。"""

import hashlib
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from makeaifactory.runtime.model_installer import is_model_valid  # noqa: E402


def _make_entry(size_bytes: int, sha256: str = "") -> types.SimpleNamespace:
    """ModelEntry のダミー (is_model_valid が参照する属性のみを持つ)。"""
    return types.SimpleNamespace(size_bytes=size_bytes, sha256=sha256)


def test_missing_file_is_invalid(tmp_path):
    path = tmp_path / "not_exists.safetensors"
    entry = _make_entry(size_bytes=100)
    assert is_model_valid(path, entry, "exists") is False
    assert is_model_valid(path, entry, "hash") is False


def test_existing_but_wrong_size_is_invalid(tmp_path):
    path = tmp_path / "partial.safetensors"
    path.write_bytes(b"0" * 10)  # 途中DLを模した短いファイル
    entry = _make_entry(size_bytes=100)
    assert is_model_valid(path, entry, "exists") is False


def test_existing_with_matching_size_is_valid(tmp_path):
    path = tmp_path / "full.safetensors"
    content = b"x" * 100
    path.write_bytes(content)
    entry = _make_entry(size_bytes=100)
    assert is_model_valid(path, entry, "exists") is True


def test_size_match_but_hash_mismatch(tmp_path):
    path = tmp_path / "corrupt.safetensors"
    content = b"y" * 100
    path.write_bytes(content)
    wrong_hash = "0" * 64
    entry = _make_entry(size_bytes=100, sha256=wrong_hash)
    # size は一致するので exists 判定は True、しかし hash 不一致なので hash 判定は False
    assert is_model_valid(path, entry, "exists") is True
    assert is_model_valid(path, entry, "hash") is False


def test_size_and_hash_match_is_valid(tmp_path):
    path = tmp_path / "ok.safetensors"
    content = b"z" * 100
    path.write_bytes(content)
    correct_hash = hashlib.sha256(content).hexdigest()
    entry = _make_entry(size_bytes=100, sha256=correct_hash)
    assert is_model_valid(path, entry, "exists") is True
    assert is_model_valid(path, entry, "hash") is True


def test_zero_size_bytes_falls_back_to_existence(tmp_path):
    path = tmp_path / "no_size_info.safetensors"
    path.write_bytes(b"anything")
    entry = _make_entry(size_bytes=0)  # size_bytes未設定(0)は存在のみで可
    assert is_model_valid(path, entry, "exists") is True
    # sha256未設定でもhash判定はverify_sha256側の仕様でスキップされ、size一致(この場合は0なので常に真)とみなされる
    assert is_model_valid(path, entry, "hash") is True
