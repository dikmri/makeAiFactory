import hashlib
import tempfile
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from makeaifactory.runtime.hash_verifier import compute_sha256, verify_sha256
from makeaifactory.domain.errors import HashMismatchError


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_compute_sha256():
    with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
        f.write(b"hello world")
        path = Path(f.name)
    try:
        result = compute_sha256(path)
        expected = _sha256(b"hello world")
        assert result == expected
    finally:
        path.unlink()


def test_verify_sha256_ok():
    with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
        f.write(b"test data")
        path = Path(f.name)
    try:
        expected = _sha256(b"test data")
        verify_sha256(path, expected)
    finally:
        path.unlink()


def test_verify_sha256_mismatch():
    with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
        f.write(b"test data")
        path = Path(f.name)
    try:
        with pytest.raises(HashMismatchError):
            verify_sha256(path, "0" * 64)
    finally:
        path.unlink()


def test_verify_sha256_skips_unfilled():
    with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
        f.write(b"any content")
        path = Path(f.name)
    try:
        verify_sha256(path, "TO_BE_FILLED")
    finally:
        path.unlink()
