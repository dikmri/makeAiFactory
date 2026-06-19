from __future__ import annotations

import logging
from typing import Callable

from ..domain.progress import ComfyProgressEvent, JobProgress, JobState

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[JobProgress], None]

_CLASS_TYPE_MESSAGES: dict[str, str] = {
    "CheckpointLoaderSimple": "チェックポイントを読み込み中...",
    "LoraLoader": "LoRAを適用中...",
    "CLIPTextEncode": "プロンプトをエンコード中...",
    "EmptySD3LatentImage": "潜在空間を準備中...",
    "KSampler": "画像を生成中...",
    "VAEDecode": "画像をデコード中...",
    "SaveImage": "画像を保存中...",
    "StringConcatenate": "パラメータを処理中...",
}


def build_node_labels(template: dict) -> dict[str, str]:
    labels: dict[str, str] = {}
    for node_id, node_data in template.items():
        if not isinstance(node_data, dict):
            continue
        class_type = node_data.get("class_type", "")
        msg = _CLASS_TYPE_MESSAGES.get(class_type)
        if msg:
            labels[str(node_id)] = msg
    return labels


class ProgressTracker:
    def __init__(
        self,
        on_progress: ProgressCallback | None = None,
        node_labels: dict[str, str] | None = None,
    ):
        self._cb = on_progress
        self._node_labels = node_labels or {}
        self._progress = JobProgress(state=JobState.GENERATING)
        self.latest_preview: bytes | None = None

    def handle_event(self, event: ComfyProgressEvent) -> None:
        if event.event_type == "preview" and event.preview_data:
            logger.info("プレビューイベント受信: %d bytes", len(event.preview_data))
            self.latest_preview = event.preview_data
            self._progress.message = "画像生成中... (プレビュー更新)"
            self._notify()
            return

        etype = event.event_type

        if etype == "execution_start":
            self._progress.state = JobState.GENERATING
            self._progress.message = "生成を開始しています..."

        elif etype == "executing":
            node_id = event.node_id
            if node_id:
                label = self._node_labels.get(node_id, f"ノード {node_id} を処理中...")
                self._progress.message = label
            else:
                self._progress.message = "生成中..."

        elif etype == "progress":
            self._progress.step = event.step
            self._progress.total_steps = event.max_steps
            pct = self._progress.percent
            self._progress.message = f"画像生成中... {pct:.0f}%"

        elif etype == "executed":
            self._progress.state = JobState.RESOLVING_OUTPUT
            self._progress.message = "画像を取得しています..."

        elif etype == "execution_error":
            self._progress.state = JobState.FAILED
            self._progress.message = "生成エラー"

        self._notify()

    def _notify(self) -> None:
        if self._cb:
            self._cb(JobProgress(
                state=self._progress.state,
                message=self._progress.message,
                step=self._progress.step,
                total_steps=self._progress.total_steps,
                preview_data=self.latest_preview,
            ))
