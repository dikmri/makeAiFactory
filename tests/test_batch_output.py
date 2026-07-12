import tempfile
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from makeaifactory.core.batch_output import (
    finalize_batch_item,
    save_generated_output,
    unique_destination,
)


def test_unique_destination_collision():
    """既存a.mp4があるとa_1.mp4を返し、それも埋まっていればa_2.mp4を返す。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        dest_dir = Path(tmpdir)

        # 何も無ければそのまま a.mp4
        assert unique_destination(dest_dir, "a") == dest_dir / "a.mp4"

        (dest_dir / "a.mp4").write_bytes(b"x")
        assert unique_destination(dest_dir, "a") == dest_dir / "a_1.mp4"

        (dest_dir / "a_1.mp4").write_bytes(b"x")
        assert unique_destination(dest_dir, "a") == dest_dir / "a_2.mp4"


def test_save_generated_output_basic():
    """一時srcを保存するとfinalが存在し中身が一致、tmp(.part)が残らない。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_root = Path(tmpdir)
        src = tmp_root / "src.mp4"
        src.write_bytes(b"fake mp4 content")
        dest_dir = tmp_root / "out"

        final = save_generated_output(src, dest_dir, "a")

        assert final == dest_dir / "a.mp4"
        assert final.read_bytes() == b"fake mp4 content"
        leftovers = [p for p in dest_dir.iterdir() if p.name != "a.mp4"]
        assert leftovers == []


def test_save_generated_output_no_overwrite():
    """既存a.mp4があっても上書きせず別名で保存する。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_root = Path(tmpdir)
        dest_dir = tmp_root / "out"
        dest_dir.mkdir()
        (dest_dir / "a.mp4").write_bytes(b"old content")

        src = tmp_root / "src.mp4"
        src.write_bytes(b"new content")

        final = save_generated_output(src, dest_dir, "a")

        assert final == dest_dir / "a_1.mp4"
        assert (dest_dir / "a.mp4").read_bytes() == b"old content"
        assert final.read_bytes() == b"new content"


def test_finalize_input_remains_on_failure():
    """output_srcが存在しない場合、例外が上がりinput_pathは元の場所に残る。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_root = Path(tmpdir)
        input_dir = tmp_root / "input"
        input_dir.mkdir()
        end_dir = tmp_root / "end"
        end_dir.mkdir()
        output_dir = tmp_root / "output"

        input_path = input_dir / "a.png"
        input_path.write_bytes(b"image bytes")

        missing_output_src = tmp_root / "does_not_exist.mp4"

        with pytest.raises(OSError):
            finalize_batch_item(input_path, missing_output_src, output_dir, end_dir)

        # 入力は元の場所に残っている(移動されていない)
        assert input_path.exists()
        assert not (end_dir / "a.png").exists()
        # 成果物も生成されていない
        assert not output_dir.exists() or list(output_dir.iterdir()) == []


def test_finalize_success_moves_input():
    """正常時、finalがoutput_dirにでき、inputはend_dirへ移動している。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_root = Path(tmpdir)
        input_dir = tmp_root / "input"
        input_dir.mkdir()
        end_dir = tmp_root / "end"
        end_dir.mkdir()
        output_dir = tmp_root / "output"

        input_path = input_dir / "a.png"
        input_path.write_bytes(b"image bytes")

        output_src = tmp_root / "generated.mp4"
        output_src.write_bytes(b"video bytes")

        final = finalize_batch_item(input_path, output_src, output_dir, end_dir)

        assert final == output_dir / "a.mp4"
        assert final.read_bytes() == b"video bytes"
        assert not input_path.exists()
        assert (end_dir / "a.png").exists()
        assert (end_dir / "a.png").read_bytes() == b"image bytes"


def test_finalize_end_dir_collision():
    """end_dirに同名が既にあっても入力moveが衝突せず別名になる。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_root = Path(tmpdir)
        input_dir = tmp_root / "input"
        input_dir.mkdir()
        end_dir = tmp_root / "end"
        end_dir.mkdir()
        output_dir = tmp_root / "output"

        # end_dir に同名の既存ファイルを用意しておく
        (end_dir / "a.png").write_bytes(b"previous run's input")

        input_path = input_dir / "a.png"
        input_path.write_bytes(b"current image bytes")

        output_src = tmp_root / "generated.mp4"
        output_src.write_bytes(b"video bytes")

        final = finalize_batch_item(input_path, output_src, output_dir, end_dir)

        assert final == output_dir / "a.mp4"
        assert not input_path.exists()
        # 既存の a.png は上書きされず、新しい入力は a_1.png として退避される
        assert (end_dir / "a.png").read_bytes() == b"previous run's input"
        assert (end_dir / "a_1.png").read_bytes() == b"current image bytes"
