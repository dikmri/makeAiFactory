from __future__ import annotations

import copy
import logging
import random
import re
from dataclasses import dataclass
from datetime import datetime

from ..constants import (
    EMPTY_LATENT_NODE_ID,
    KSAMPLER_NODE_ID,
    NEGATIVE_PROMPT_NODE_ID,
    POSITIVE_PROMPT_NODE_ID,
    SAVE_IMAGE_NODE_ID,
)

logger = logging.getLogger(__name__)


@dataclass
class WorkflowPatchContext:
    job_id: str
    positive_prompt: str
    negative_prompt: str
    output_prefix: str
    seed: int | None = None
    width: int = 1280
    height: int = 720
    batch_size: int = 1


def _expand_random_choices(text: str) -> str:
    def _replace_brace(m: re.Match) -> str:
        inner = m.group(1)
        if "|" in inner:
            choices = inner.split("|")
            chosen = random.choice(choices).strip()
            return chosen
        return m.group(0)
    return re.sub(r"\{([^}]*)\}", _replace_brace, text)


def patch_workflow(template: dict, ctx: WorkflowPatchContext) -> dict:
    wf = copy.deepcopy(template)

    seed = ctx.seed if ctx.seed is not None else random.randint(0, 2**32 - 1)

    positive = _expand_random_choices(ctx.positive_prompt)
    negative = _expand_random_choices(ctx.negative_prompt)

    if POSITIVE_PROMPT_NODE_ID in wf:
        wf[POSITIVE_PROMPT_NODE_ID]["inputs"]["text"] = positive

    if NEGATIVE_PROMPT_NODE_ID in wf:
        wf[NEGATIVE_PROMPT_NODE_ID]["inputs"]["text"] = negative

    if KSAMPLER_NODE_ID in wf:
        wf[KSAMPLER_NODE_ID]["inputs"]["seed"] = seed

    if SAVE_IMAGE_NODE_ID in wf:
        wf[SAVE_IMAGE_NODE_ID]["inputs"]["filename_prefix"] = ctx.output_prefix

    if EMPTY_LATENT_NODE_ID in wf:
        wf[EMPTY_LATENT_NODE_ID]["inputs"]["width"] = ctx.width
        wf[EMPTY_LATENT_NODE_ID]["inputs"]["height"] = ctx.height
        wf[EMPTY_LATENT_NODE_ID]["inputs"]["batch_size"] = ctx.batch_size

    logger.debug(
        "workflow patch完了: job=%s seed=%s",
        ctx.job_id,
        seed,
    )
    return wf


def make_output_prefix(job_id: str, seed: int = 0, pattern: str = "{timestamp}_{seed}") -> str:
    now = datetime.now()
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    name = pattern.format(timestamp=timestamp, seed=seed, job_id=job_id)
    return f"makeImg/{name}"
