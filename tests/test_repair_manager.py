import tempfile
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

from makeaifactory.runtime.repair_manager import RepairManager, can_start_repair
from makeaifactory.runtime.runtime_state import RuntimeState


# ── can_start_repair ─────────────────────────────────────────────────────

def test_can_start_repair_false_during_single_job():
    assert can_start_repair("single") is False


def test_can_start_repair_false_during_batch_job():
    assert can_start_repair("batch") is False


def test_can_start_repair_true_when_idle():
    assert can_start_repair("idle") is True


def test_can_start_repair_true_when_offline():
    assert can_start_repair("offline") is True


# ── _assert_within_root ──────────────────────────────────────────────────

def test_assert_within_root_raises_for_outside_path():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir) / "runtime"
        root.mkdir()
        state = RuntimeState(root)
        repair = RepairManager(root, state)

        outside = Path(tmpdir) / "outside"
        outside.mkdir()

        with pytest.raises(ValueError):
            repair._assert_within_root(outside)


def test_assert_within_root_ok_for_inside_path():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir) / "runtime"
        root.mkdir()
        state = RuntimeState(root)
        repair = RepairManager(root, state)

        inside = root / "ComfyUI" / "custom_nodes" / "dummy_node"
        inside.mkdir(parents=True)

        # 例外が出ないこと
        repair._assert_within_root(inside)


def test_assert_within_root_ok_for_root_itself():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir) / "runtime"
        root.mkdir()
        state = RuntimeState(root)
        repair = RepairManager(root, state)

        # 例外が出ないこと
        repair._assert_within_root(root)


# ── reset_custom_nodes の安全性 ───────────────────────────────────────────

def test_reset_custom_nodes_removes_only_custom_nodes_children():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir) / "runtime"
        root.mkdir()

        node_dir = root / "ComfyUI" / "custom_nodes"
        dummy_node = node_dir / "dummy_node"
        dummy_node.mkdir(parents=True)
        (dummy_node / "marker.txt").write_text("hello", encoding="utf-8")

        # root外に無関係なファイル/ディレクトリを置き、削除の巻き添えにならないことを確認する
        outside_dir = Path(tmpdir) / "outside"
        outside_dir.mkdir()
        outside_marker = outside_dir / "keep_me.txt"
        outside_marker.write_text("keep", encoding="utf-8")

        state = RuntimeState(root)
        repair = RepairManager(root, state)
        repair.reset_custom_nodes()

        assert not dummy_node.exists()
        assert node_dir.exists()  # custom_nodes 自体は残る (中身だけ削除)
        assert outside_marker.exists()  # root外は一切触られない
