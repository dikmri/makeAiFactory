from __future__ import annotations

import copy
import logging
import random
from dataclasses import dataclass

from ..constants import (
    BASE_VIDEO_NODE_ID,
    LOADIMAGE_NODE_ID,
    OUTPUT_VIDEO_NODE_ID,
    SEED_NODE_ID,
    UNET_HIGH_NODE_ID,
    UNET_LOW_NODE_ID,
)

logger = logging.getLogger(__name__)


@dataclass
class WorkflowPatchContext:
    job_id: str
    uploaded_image_name: str
    output_prefix: str
    seed: int | None
    unet_high_name: str = ""   # node 295: UnetLoaderGGUF 高ノイズ段
    unet_low_name: str  = ""   # node 296: UnetLoaderGGUF 低ノイズ段


def patch_workflow(template: dict, ctx: WorkflowPatchContext) -> dict:
    wf = copy.deepcopy(template)

    if LOADIMAGE_NODE_ID in wf:
        wf[LOADIMAGE_NODE_ID]["inputs"]["image"] = ctx.uploaded_image_name

    if OUTPUT_VIDEO_NODE_ID in wf:
        wf[OUTPUT_VIDEO_NODE_ID]["inputs"]["filename_prefix"] = ctx.output_prefix

    if BASE_VIDEO_NODE_ID in wf:
        wf[BASE_VIDEO_NODE_ID]["inputs"]["save_output"] = False

    if ctx.seed is not None and SEED_NODE_ID in wf:
        wf[SEED_NODE_ID]["inputs"]["seed"] = ctx.seed

    if ctx.unet_high_name and UNET_HIGH_NODE_ID in wf:
        wf[UNET_HIGH_NODE_ID]["inputs"]["unet_name"] = ctx.unet_high_name
    if ctx.unet_low_name and UNET_LOW_NODE_ID in wf:
        wf[UNET_LOW_NODE_ID]["inputs"]["unet_name"] = ctx.unet_low_name

    logger.debug(
        "workflow patch完了: job=%s image=%s prefix=%s seed=%s",
        ctx.job_id,
        ctx.uploaded_image_name,
        ctx.output_prefix,
        ctx.seed,
    )
    return wf


def make_output_prefix(job_id: str) -> str:
    return f"makeAiFactory/{job_id}/output"
