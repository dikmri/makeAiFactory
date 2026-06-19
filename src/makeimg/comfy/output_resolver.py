from __future__ import annotations

import logging
from pathlib import Path

from ..constants import SAVE_IMAGE_NODE_ID
from ..domain.errors import OutputNotFoundError

logger = logging.getLogger(__name__)


def resolve_output_image(
    history: dict,
    prompt_id: str,
    comfyui_output_dir: Path,
    job_id: str,
) -> Path:
    entry = history.get(prompt_id, {})
    outputs = entry.get("outputs", {})

    if SAVE_IMAGE_NODE_ID in outputs:
        images = outputs[SAVE_IMAGE_NODE_ID].get("images", [])
        for img in images:
            filename = img.get("filename", "")
            subfolder = img.get("subfolder", "")
            candidate = comfyui_output_dir / subfolder / filename
            if candidate.exists():
                logger.info("output resolve: history経由 %s", candidate)
                return candidate

    candidates = list(comfyui_output_dir.rglob(f"*{job_id}*.png"))
    if not candidates:
        candidates = list(comfyui_output_dir.rglob("makeImg/**/*.png"))

    if candidates:
        best = max(candidates, key=lambda p: p.stat().st_mtime)
        logger.info("output resolve: ファイル探索 %s", best)
        return best

    all_images = list(comfyui_output_dir.rglob("*.png"))
    if all_images:
        best = max(all_images, key=lambda p: p.stat().st_mtime)
        logger.warning("output resolve: 最新画像をfallbackとして使用 %s", best)
        return best

    raise OutputNotFoundError(
        f"生成された画像が見つかりません。\n"
        f"ComfyUI outputディレクトリ: {comfyui_output_dir}\n"
        f"prompt_id: {prompt_id}"
    )
