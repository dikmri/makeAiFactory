"""ワークフロープリセット (default / pai / fe) の健全性テスト。

各バンドルプリセットが正しくサニタイズでき、アプリがハードコードしている
必須ノードを満たし、参照切れ・危険ノード・翻訳ノードの残存が無いことを確認する。
"""
import json
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from makeaifactory.comfy.workflow_sanitizer import (
    sanitize_workflow,
    _inline_removed_text_nodes,
)
from makeaifactory.constants import (
    DEFAULT_WORKFLOW,
    WORKFLOW_PRESETS,
    LOADIMAGE_NODE_ID,
    OUTPUT_VIDEO_NODE_ID,
)

PRESETS_DIR = Path(__file__).parent.parent / "app" / "workflow" / "presets"

# アプリが patch_workflow / apply_dev_overrides でハードコード参照する必須ノード。
# 129 (base video) は最終出力 188 から到達不可の独立ブランチで、サニタイズで正当に
# 剪定される (既存の default でも runtime_template に含まれない) ため除外する。
_REQUIRED_NODE_IDS = ["189", "188", "251", "291", "295", "296", "48", "51"]
_DANGEROUS_CLASS_TYPES = {
    "Post Request Node",
    "HTMLRendererNode",
    "easy loadImagesForLoop",
    "easy imagesCountInDirectory",
    "GoogleTranslateTextNode",
}


def _is_ref(v):
    return isinstance(v, list) and len(v) == 2 and isinstance(v[0], str)


def _dangling_refs(wf: dict):
    dangling = []
    for nid, node in wf.items():
        for key, value in node.get("inputs", {}).items():
            refs = []
            if _is_ref(value):
                refs.append(value[0])
            elif isinstance(value, dict):
                for nested in value.values():
                    if _is_ref(nested):
                        refs.append(nested[0])
            for r in refs:
                if r not in wf:
                    dangling.append((nid, key, r))
    return dangling


def test_default_is_registered():
    assert DEFAULT_WORKFLOW in WORKFLOW_PRESETS


@pytest.mark.parametrize("workflow_id", list(WORKFLOW_PRESETS.keys()))
def test_preset_source_exists(workflow_id):
    src = PRESETS_DIR / WORKFLOW_PRESETS[workflow_id]["source"]
    assert src.exists(), f"プリセットソースが存在しません: {src}"


@pytest.mark.parametrize("workflow_id", list(WORKFLOW_PRESETS.keys()))
def test_preset_sanitizes_to_valid_template(workflow_id):
    src_path = PRESETS_DIR / WORKFLOW_PRESETS[workflow_id]["source"]
    source = json.loads(src_path.read_text(encoding="utf-8"))
    result = sanitize_workflow(source)

    # 必須ノードがすべて残っている
    for nid in _REQUIRED_NODE_IDS:
        assert nid in result, f"{workflow_id}: 必須ノード {nid} が欠落"

    # 出力/画像入力ノードが残っている (apply_workflow_json と同じ最低条件)
    assert OUTPUT_VIDEO_NODE_ID in result
    assert LOADIMAGE_NODE_ID in result

    # 参照切れが無い
    assert _dangling_refs(result) == [], f"{workflow_id}: 参照切れ {_dangling_refs(result)}"

    # 危険ノードが残っていない
    for node in result.values():
        assert node.get("class_type") not in _DANGEROUS_CLASS_TYPES

    # ポジティブプロンプト (node 48) が文字列に解決されている
    n48 = result["48"]["inputs"].get("value")
    assert isinstance(n48, str) and n48, f"{workflow_id}: node48 が文字列に解決されていない: {n48!r}"


def test_workflow_loras_are_ondemand_not_setup_models():
    """pai/fe/naka等の専用LoRAはセットアップ時の自動DL対象に含まれず、
    各ワークフローの専用モデルとしてのみ列挙されること。
    対象ワークフローはregistry(WORKFLOW_PRESETS)から動的に列挙するため、
    将来ワークフローが増えても追随する。"""
    import json
    from makeaifactory.domain.manifest import ModelManifest
    from makeaifactory.runtime.model_installer import _needed_models, workflow_models

    manifest_path = Path(__file__).parent.parent / "app" / "manifest" / "model_manifest.json"
    manifest = ModelManifest.from_dict(json.loads(manifest_path.read_text(encoding="utf-8")))

    setup_names = {m.name for m in _needed_models(manifest, ["normal", "lite", "ultralite"])}
    target_workflows = [wid for wid in WORKFLOW_PRESETS if workflow_models(manifest, wid)]
    assert target_workflows, "専用モデルを持つワークフローが1つも見つからない"
    for wid in target_workflows:
        wf_models = workflow_models(manifest, wid)
        assert wf_models, f"{wid} の専用モデルが定義されていない"
        for m in wf_models:
            assert not m.is_shared
            assert m.is_downloadable, f"{m.name} がDL不可 (URL/sha256未設定)"
            assert m.name not in setup_names, f"{m.name} がセットアップDL対象に混入"


def test_inline_removed_text_nodes_resolves_passthrough():
    wf = {
        "1": {"class_type": "GoogleTranslateTextNode", "inputs": {"text": "テスト原文"}},
        "2": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": ["1", 0]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": ["2", 0]}},
    }
    _inline_removed_text_nodes(wf)
    assert wf["2"]["inputs"]["value"] == "テスト原文"
    # 元の翻訳ノード参照は残らず、CLIPTextEncode は node2 を指したまま
    assert wf["3"]["inputs"]["text"] == ["2", 0]
