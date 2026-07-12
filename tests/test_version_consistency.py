"""バージョン表記の単一ソース性を検証するテスト。

constants.APP_VERSION / pyproject.toml の [project].version /
makeaifactory.__version__ の3箇所が常に一致していることを保証する。
(uv.lock はロック生成物でありオーケストレーターが `uv lock` で更新するため
 検証対象に含めない)
"""
import re
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import makeaifactory
from makeaifactory.constants import APP_VERSION

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+")


def _pyproject_version() -> str:
    toml_path = Path(__file__).parent.parent / "pyproject.toml"
    data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    return data["project"]["version"]


def test_constants_and_pyproject_versions_match():
    assert APP_VERSION == _pyproject_version(), (
        f"constants.APP_VERSION={APP_VERSION!r} != pyproject.toml version={_pyproject_version()!r}"
    )


def test_package_dunder_version_matches_constants():
    assert makeaifactory.__version__ == APP_VERSION, (
        f"makeaifactory.__version__={makeaifactory.__version__!r} != "
        f"constants.APP_VERSION={APP_VERSION!r}"
    )


def test_version_is_semver():
    assert _SEMVER_RE.match(APP_VERSION), f"APP_VERSION={APP_VERSION!r} が semver形式ではない"
    assert _SEMVER_RE.match(_pyproject_version()), (
        f"pyproject version={_pyproject_version()!r} が semver形式ではない"
    )
