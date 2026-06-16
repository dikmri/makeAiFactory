import json
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from makeaifactory.comfy.workflow_patcher import WorkflowPatchContext, patch_workflow, make_output_prefix


TEMPLATE_JSON = Path(__file__).parent.parent / "app" / "workflow" / "makeAiFactory_runtime_template.json"


@pytest.fixture
def template():
    with TEMPLATE_JSON.open("r", encoding="utf-8") as f:
        return json.load(f)


def test_image_patched(template):
    ctx = WorkflowPatchContext(
        job_id="test_job_001",
        uploaded_image_name="my_image.png",
        output_prefix="makeAiFactory/test_job_001/output",
        seed=None,
    )
    result = patch_workflow(template, ctx)
    assert result["189"]["inputs"]["image"] == "my_image.png"


def test_output_prefix_patched(template):
    ctx = WorkflowPatchContext(
        job_id="test_job_001",
        uploaded_image_name="my_image.png",
        output_prefix="makeAiFactory/test_job_001/output",
        seed=None,
    )
    result = patch_workflow(template, ctx)
    assert result["188"]["inputs"]["filename_prefix"] == "makeAiFactory/test_job_001/output"


def test_seed_patched(template):
    ctx = WorkflowPatchContext(
        job_id="test_job_001",
        uploaded_image_name="my_image.png",
        output_prefix="makeAiFactory/test_job_001/output",
        seed=12345,
    )
    result = patch_workflow(template, ctx)
    assert result["251"]["inputs"]["seed"] == 12345


def test_seed_none_keeps_original(template):
    original_seed = template["251"]["inputs"]["seed"]
    ctx = WorkflowPatchContext(
        job_id="test_job_001",
        uploaded_image_name="my_image.png",
        output_prefix="makeAiFactory/test_job_001/output",
        seed=None,
    )
    result = patch_workflow(template, ctx)
    assert result["251"]["inputs"]["seed"] == original_seed


def test_original_not_mutated(template):
    original_image = template["189"]["inputs"]["image"]
    ctx = WorkflowPatchContext(
        job_id="test_job_001",
        uploaded_image_name="new_image.png",
        output_prefix="makeAiFactory/test_job_001/output",
        seed=None,
    )
    patch_workflow(template, ctx)
    assert template["189"]["inputs"]["image"] == original_image


def test_make_output_prefix():
    prefix = make_output_prefix("20260614_203015_a1b2c3")
    assert prefix == "makeAiFactory/20260614_203015_a1b2c3/output"
