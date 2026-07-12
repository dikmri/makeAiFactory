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

    安全のため、対象 prompt_id の history エントリに紐づく出力のみを候補にする。
    他の prompt_id の出力・過去ジョブ・無関係な mp4 を成功として誤って
    返してしまうことがないよう、fallback探索は一切行わない。

    優先順位:
    1. ノード OUTPUT_VIDEO_NODE_ID (Save Video - Upscaled) の videos
    2. 1が無い/採用できない場合、同entry内の他ノードの videos
       (いずれも prompt_id スコープ内なので安全)
    """
    entry = history.get(prompt_id, {})
    outputs = entry.get("outputs", {})
    out_root = comfyui_output_dir.resolve()

    def _first_valid(videos: list[dict]) -> Path | None:
        for v in videos:
            filename = v.get("filename", "")
            subfolder = v.get("subfolder", "")
            candidate = comfyui_output_dir / subfolder / filename
            resolved = candidate.resolve()
            # パス封じ込め: comfyui_output_dir配下から外れる候補(subfolderの ".." 等)は除外
            if not resolved.is_relative_to(out_root):
                logger.warning("output resolve: パス封じ込め違反のため除外 %s", candidate)
                continue
            if resolved.exists():
                return candidate
        return None

    # ノード188 (Save Video - Upscaled) の出力を優先
    if OUTPUT_VIDEO_NODE_ID in outputs:
        found = _first_valid(outputs[OUTPUT_VIDEO_NODE_ID].get("videos", []))
        if found is not None:
            logger.info("output resolve: history経由(node%s) %s", OUTPUT_VIDEO_NODE_ID, found)
            return found

    # 上記が無い/見つからない場合、同entry内の他ノードの videos を候補にする
    for node_id, node_output in outputs.items():
        if node_id == OUTPUT_VIDEO_NODE_ID:
            continue
        found = _first_valid(node_output.get("videos", []))
        if found is not None:
            logger.info("output resolve: history経由(node%s) %s", node_id, found)
            return found

    raise OutputNotFoundError(
        f"生成されたMP4が見つかりません。\n"
        f"ComfyUI outputディレクトリ: {comfyui_output_dir}\n"
        f"prompt_id: {prompt_id}\n"
        f"job_id: {job_id}"
    )
