from __future__ import annotations

import copy
import logging
import random
from dataclasses import dataclass, field

from ..constants import (
    BASE_VIDEO_NODE_ID,
    LOADIMAGE_NODE_ID,
    OUTPUT_VIDEO_NODE_ID,
    SAGE_ATTN_HIGH_NODE_ID,
    SAGE_ATTN_LOW_NODE_ID,
    SEED_NODE_ID,
    UNET_HIGH_NODE_ID,
    UNET_LOW_NODE_ID,
)

logger = logging.getLogger(__name__)


@dataclass
class DevModeOverrides:
    """開発モードで上書きするワークフローパラメーター。None の項目は変更しない。"""
    # プロンプト
    positive_prompt: str | None = None        # node 48  inputs.value
    negative_prompt: str | None = None        # node 51  inputs.value
    # 生成設定
    steps: int | None = None                  # node 252 inputs.Number
    cfg: float | None = None                  # node 248 inputs.Number
    motion_cfg: float | None = None           # node 250 inputs.Number
    motion_pass_steps: int | None = None      # node 242 inputs.Number
    # 動画設定
    video_length_sec: int | None = None       # node 246 inputs.value
    video_fps: int | None = None              # node 244 inputs.Number
    resolution_mode: str | None = None        # node 291 inputs.resolution_mode
    upscale_multiplier: int | None = None     # node 237 inputs.Number
    crf: int | None = None                    # node 188 inputs.crf
    # シード
    seed: int | None = None                   # node 251 inputs.seed (None=テンプレートのまま)
    # 上級設定
    sage_attention: str | None = None         # node 6/7  inputs.sage_attention
    model_shift: float | None = None          # node 31/34 inputs.shift
    lightx2v_strength: float | None = None    # node 219/217 inputs.strength_model
    nag_scale: float | None = None            # node 37/41 inputs.nag_scale
    nag_alpha: float | None = None            # node 37/41 inputs.nag_alpha
    nag_tau: float | None = None              # node 37/41 inputs.nag_tau

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}

    @classmethod
    def from_dict(cls, d: dict) -> "DevModeOverrides":
        valid = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in valid})


def apply_dev_overrides(wf: dict, ov: DevModeOverrides) -> dict:
    """DevModeOverrides をワークフローに適用した新しい dict を返す（deepcopy済み）。"""
    wf = copy.deepcopy(wf)

    def _s(nid: str, key: str, val) -> None:
        if val is not None and nid in wf:
            wf[nid]["inputs"][key] = val

    _s("48",  "value",          ov.positive_prompt)
    _s("51",  "value",          ov.negative_prompt)
    if ov.steps is not None:            _s("252", "Number", str(ov.steps))
    if ov.cfg is not None:              _s("248", "Number", str(ov.cfg))
    if ov.motion_cfg is not None:       _s("250", "Number", str(ov.motion_cfg))
    if ov.motion_pass_steps is not None: _s("242", "Number", str(ov.motion_pass_steps))
    _s("246", "value",          ov.video_length_sec)
    if ov.video_fps is not None:        _s("244", "Number", str(ov.video_fps))
    _s("291", "resolution_mode", ov.resolution_mode)
    if ov.upscale_multiplier is not None: _s("237", "Number", str(ov.upscale_multiplier))
    _s("188", "crf",            ov.crf)
    _s("251", "seed",           ov.seed)
    if ov.sage_attention is not None:
        _s("6", "sage_attention", ov.sage_attention)
        _s("7", "sage_attention", ov.sage_attention)
    if ov.model_shift is not None:
        _s("31", "shift", ov.model_shift)
        _s("34", "shift", ov.model_shift)
    if ov.lightx2v_strength is not None:
        _s("219", "strength_model", ov.lightx2v_strength)
        _s("217", "strength_model", ov.lightx2v_strength)
    if ov.nag_scale is not None:
        _s("37", "nag_scale", ov.nag_scale)
        _s("41", "nag_scale", ov.nag_scale)
    if ov.nag_alpha is not None:
        _s("37", "nag_alpha", ov.nag_alpha)
        _s("41", "nag_alpha", ov.nag_alpha)
    if ov.nag_tau is not None:
        _s("37", "nag_tau", ov.nag_tau)
        _s("41", "nag_tau", ov.nag_tau)

    logger.debug("dev overrides 適用完了")
    return wf


@dataclass
class WorkflowPatchContext:
    job_id: str
    uploaded_image_name: str
    output_prefix: str
    seed: int | None
    unet_high_name: str = ""   # node 295: UnetLoaderGGUF 高ノイズ段
    unet_low_name: str  = ""   # node 296: UnetLoaderGGUF 低ノイズ段
    sage_attention_mode: str = "disabled"   # node 6/7: PathchSageAttentionKJ


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

    if SAGE_ATTN_HIGH_NODE_ID in wf:
        wf[SAGE_ATTN_HIGH_NODE_ID]["inputs"]["sage_attention"] = ctx.sage_attention_mode
    if SAGE_ATTN_LOW_NODE_ID in wf:
        wf[SAGE_ATTN_LOW_NODE_ID]["inputs"]["sage_attention"] = ctx.sage_attention_mode

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
