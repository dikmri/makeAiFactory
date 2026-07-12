import json
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from makeaifactory.comfy.workflow_sanitizer import sanitize_workflow, _collect_dependencies


# 実行時に再生成される makeAiFactory_api_source.json ではなく、コミット済みで
# 不変の presets/default.json を使う (テストをヘルメチックにし、CIのクリーン
# チェックアウトでも安定させるため)。
SOURCE_JSON = Path(__file__).parent.parent / "app" / "workflow" / "presets" / "default.json"


@pytest.fixture
def source_workflow():
    with SOURCE_JSON.open("r", encoding="utf-8") as f:
        return json.load(f)


def test_post_request_node_removed(source_workflow):
    result = sanitize_workflow(source_workflow)
    for node in result.values():
        assert node.get("class_type") != "Post Request Node", "Post Request Nodeが残っています"


def test_html_renderer_removed(source_workflow):
    result = sanitize_workflow(source_workflow)
    for node in result.values():
        assert node.get("class_type") != "HTMLRendererNode"


def test_folder_nodes_removed(source_workflow):
    result = sanitize_workflow(source_workflow)
    removed_classes = {"easy loadImagesForLoop", "easy imagesCountInDirectory", "easy imageSwitch"}
    for node in result.values():
        assert node.get("class_type") not in removed_classes


def test_resolution_picker_patched(source_workflow):
    result = sanitize_workflow(source_workflow)
    node291 = result.get("291")
    assert node291 is not None
    assert node291["inputs"]["image"] == ["189", 0], "291のimageが189に変更されていません"


def test_output_prefix_patched(source_workflow):
    result = sanitize_workflow(source_workflow)
    node188 = result.get("188")
    assert node188 is not None
    assert node188["inputs"]["filename_prefix"] == "__OUTPUT_PREFIX__"


def test_output_node_188_present(source_workflow):
    result = sanitize_workflow(source_workflow)
    assert "188" in result, "出力ノード188が存在しません"
    assert result["188"]["class_type"] == "VHS_VideoCombine"


def test_loadimage_node_189_present(source_workflow):
    result = sanitize_workflow(source_workflow)
    assert "189" in result
    assert result["189"]["class_type"] == "LoadImage"


def test_node_count_reduced(source_workflow):
    result = sanitize_workflow(source_workflow)
    assert len(result) < len(source_workflow), "ノード数が減っていません"


def test_collect_dependencies():
    workflow = {
        "A": {"inputs": {"x": ["B", 0]}},
        "B": {"inputs": {"y": ["C", 0]}},
        "C": {"inputs": {}},
        "D": {"inputs": {}},
    }
    deps = _collect_dependencies(workflow, ["A"])
    assert "A" in deps
    assert "B" in deps
    assert "C" in deps
    assert "D" not in deps
