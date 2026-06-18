from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from ..domain.errors import HashMismatchError

logger = logging.getLogger(__name__)

_CHUNK = 8 * 1024 * 1024


def compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def verify_sha256(path: Path, expected: str) -> None:
    if not expected or expected == "TO_BE_FILLED":
        logger.warning("SHA256未設定のためスキップ: %s", path.name)
        return
    actual = compute_sha256(path)
    if actual.lower() != expected.lower():
        raise HashMismatchError(str(path), expected, actual)
    logger.debug("SHA256検証OK: %s", path.name)
