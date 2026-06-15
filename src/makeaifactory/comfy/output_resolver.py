from __future__ import annotations

import logging
from pathlib import Path

from ..constants import OUTPUT_VIDEO_NODE_ID
from ..domain.errors import OutputNotFoundError

logger = logging.getLogger(__name__)


def resolve_output_mp4(
    history: dict,
    prompt_id: str,
    comfyui_output_dir: Path,
    job_id: str,
) -> Path:
    """
    historyレスポンスからMP4ファイルを特定する。

    優先順位:
    1. /history/{prompt_id} の outputs から VHS_VideoCombine の動画を取得
    2. filename_prefix + job_id を含む mp4 を output dir から探す
    3. 最新の mp4 を候補にする
    """
    entry = history.get(prompt_id, {})
    outputs = entry.get("outputs", {})

    # ノード188 (Save Video - Upscaled) の出力を優先
    if OUTPUT_VIDEO_NODE_ID in outputs:
        videos = outputs[OUTPUT_VIDEO_NODE_ID].get("videos", [])
        for v in videos:
            filename = v.get("filename", "")
            subfolder = v.get("subfolder", "")
            candidate = comfyui_output_dir / subfolder / filename
            if candidate.exists():
                logger.info("output resolve: history経由 %s", candidate)
                return candidate

    # fallback: job_id含むmp4を探す
    candidates = list(comfyui_output_dir.rglob(f"*{job_id}*.mp4"))
    if not candidates:
        candidates = list(comfyui_output_dir.rglob("makeAiFactory/**/*.mp4"))

    if candidates:
        best = max(candidates, key=lambda p: p.stat().st_mtime)
        logger.info("output resolve: ファイル探索 %s", best)
        return best

    # 最終fallback: 最新mp4
    all_mp4 = list(comfyui_output_dir.rglob("*.mp4"))
    if all_mp4:
        best = max(all_mp4, key=lambda p: p.stat().st_mtime)
        logger.warning("output resolve: 最新mp4をfallbackとして使用 %s", best)
        return best

    raise OutputNotFoundError(
        f"生成されたMP4が見つかりません。\n"
        f"ComfyUI outputディレクトリ: {comfyui_output_dir}\n"
        f"prompt_id: {prompt_id}"
    )
