import tempfile
import zipfile
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from makeaifactory.runtime.safe_extract import BadZipMemberError, safe_extract_zip


def _make_zip(zip_path: Path, entries: dict[str, bytes | None]) -> None:
    """entries: {メンバ名: 内容(Noneならディレクトリエントリ)} から zip を作成する。

    zipfile.ZipInfo を直接使うことで、write()/writestr() が行う暗黙の
    パス正規化を避け、任意のメンバ名(".." や絶対パス含む)をそのまま
    アーカイブへ書き込めるようにする。
    """
    with zipfile.ZipFile(zip_path, "w") as zf:
        for name, data in entries.items():
            if data is None:
                zi = zipfile.ZipInfo(filename=name if name.endswith("/") else name + "/")
                zf.writestr(zi, b"")
            else:
                zi = zipfile.ZipInfo(filename=name)
                zf.writestr(zi, data)


def test_safe_extract_normal_zip():
    """通常ファイル + サブディレクトリを含む正常な zip は dest 配下に展開される。"""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        zip_path = tmp_path / "normal.zip"
        dest = tmp_path / "dest"
        _make_zip(zip_path, {
            "readme.txt": b"hello",
            "sub/": None,
            "sub/inner.txt": b"world",
        })

        safe_extract_zip(zip_path, dest)

        assert (dest / "readme.txt").read_bytes() == b"hello"
        assert (dest / "sub").is_dir()
        assert (dest / "sub" / "inner.txt").read_bytes() == b"world"


def test_safe_extract_nested_path_ok():
    """ネストした正常パス a/b/c.txt は問題なく展開される。"""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        zip_path = tmp_path / "nested.zip"
        dest = tmp_path / "dest"
        _make_zip(zip_path, {"a/b/c.txt": b"nested-content"})

        safe_extract_zip(zip_path, dest)

        assert (dest / "a" / "b" / "c.txt").read_bytes() == b"nested-content"


def test_safe_extract_rejects_parent_traversal():
    """'../evil.txt' のような親ディレクトリ脱出メンバは拒否され、何も展開されない。"""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        zip_path = tmp_path / "evil.zip"
        dest = tmp_path / "dest"
        _make_zip(zip_path, {
            "safe.txt": b"ok",
            "../evil.txt": b"pwned",
        })

        with pytest.raises(BadZipMemberError):
            safe_extract_zip(zip_path, dest)

        # dest 外は元より、dest 配下にも一切書き込まれていないこと(all-or-nothing)
        assert not (tmp_path / "evil.txt").exists()
        assert not (dest / "safe.txt").exists()


def test_safe_extract_rejects_absolute_drive_path():
    """'C:\\x' 相当(ドライブレター付き絶対パス)のメンバは拒否される。

    実際に他ドライブ/システム領域へ書き込む危険を避けるため、書き込み先候補と
    なるパスは本テスト専用の一時ディレクトリ配下に限定する。
    """
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as other_tmp:
        tmp_path = Path(tmp)
        other_path = Path(other_tmp)
        zip_path = tmp_path / "evil_abs.zip"
        dest = tmp_path / "dest"

        outside_target = other_path / "evil.txt"
        _make_zip(zip_path, {str(outside_target): b"pwned"})

        try:
            with pytest.raises(BadZipMemberError):
                safe_extract_zip(zip_path, dest)
            assert not outside_target.exists()
        finally:
            outside_target.unlink(missing_ok=True)


def test_safe_extract_rejects_leading_slash_path():
    """'/etc/x' 相当(先頭スラッシュの絶対パス風)メンバは拒否される。"""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        zip_path = tmp_path / "evil_root.zip"
        dest = tmp_path / "dest"
        _make_zip(zip_path, {"/etc/evil.txt": b"pwned"})

        # 展開先ドライブのルート直下に書かれてしまわないよう保険のクリーンアップも行う
        drive_root_leak = Path(dest.resolve().anchor) / "etc" / "evil.txt"
        try:
            with pytest.raises(BadZipMemberError):
                safe_extract_zip(zip_path, dest)
            assert not drive_root_leak.exists()
            assert not (dest / "etc").exists()
        finally:
            if drive_root_leak.exists():
                drive_root_leak.unlink(missing_ok=True)
