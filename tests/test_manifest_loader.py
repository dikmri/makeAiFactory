import json
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from makeaifactory.domain.manifest import ModelManifest, CustomNodesManifest, RuntimeManifest


MANIFEST_DIR = Path(__file__).parent.parent / "app" / "manifest"


def test_runtime_manifest_loads():
    with (MANIFEST_DIR / "runtime_manifest.json").open("r", encoding="utf-8") as f:
        data = json.load(f)
    rm = RuntimeManifest.from_dict(data)
    assert rm.python_version == "3.13"
    assert rm.torch_cuda_variant == "cu124"


def test_model_manifest_loads():
    with (MANIFEST_DIR / "model_manifest.json").open("r", encoding="utf-8") as f:
        data = json.load(f)
    mm = ModelManifest.from_dict(data)
    assert len(mm.models) > 0
    names = [m.name for m in mm.models]
    assert "Wan22-I2V-FastMix_v10-H-Q4_K_M.gguf" in names
    assert "wan_2.1_vae.safetensors" in names


def test_custom_nodes_manifest_loads():
    with (MANIFEST_DIR / "custom_nodes_manifest.json").open("r", encoding="utf-8") as f:
        data = json.load(f)
    cm = CustomNodesManifest.from_dict(data)
    assert len(cm.custom_nodes) > 0
    names = [n.name for n in cm.custom_nodes]
    assert "ComfyUI-GGUF" in names
    assert "ComfyUI-VideoHelperSuite" in names


def test_model_manifest_has_required_classes():
    with (MANIFEST_DIR / "custom_nodes_manifest.json").open("r", encoding="utf-8") as f:
        data = json.load(f)
    cm = CustomNodesManifest.from_dict(data)
    all_classes = [cls for n in cm.custom_nodes for cls in n.required_classes]
    assert "UnetLoaderGGUF" in all_classes
    assert "VHS_VideoCombine" in all_classes
    assert "WanVideoNAG" in all_classes


def test_model_not_downloadable_when_source_unfilled():
    with (MANIFEST_DIR / "model_manifest.json").open("r", encoding="utf-8") as f:
        data = json.load(f)
    mm = ModelManifest.from_dict(data)
    for m in mm.models:
        if m.source_url == "TO_BE_FILLED":
            assert not m.is_downloadable
