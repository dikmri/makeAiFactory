from __future__ import annotations

import logging
from typing import Callable

from ..domain.progress import ComfyProgressEvent, JobProgress, JobState

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[JobProgress], None]

# class_type → 表示メッセージのマッピング
_CLASS_TYPE_MESSAGES: dict[str, str] = {
    "LoadImage": "画像を読み込み中...",
    "ImageRemoveAlpha+": "画像を前処理中...",
    "ResolutionPickerI2V": "解像度を判定中...",
    "GoogleTranslateTextNode": "プロンプトを翻訳中...",
    "CLIPLoader": "テキストエンコーダーを読み込み中...",
    "CLIPTextEncode": "プロンプトをエンコード中...",
    "CLIPVisionLoader": "ビジョンモデルを読み込み中...",
    "CLIPVisionEncode": "参照画像をエンコード中...",
    "VAELoader": "VAEモデルを読み込み中...",
    "UnetLoaderGGUF": "動画生成モデルを読み込み中...",
    "Power Lora Loader (rgthree)": "LoRAを適用中...",
    "LoraLoaderModelOnly": "LoRAを適用中...",
    "PathchSageAttentionKJ": "Attentionを最適化中...",
    "CFGZeroStarAndInit": "CFG設定を初期化中...",
    "WanVideoNAG": "NAGガイダンスを適用中...",
    "ModelSamplingSD3": "サンプラーを設定中...",
    "WanImageToVideo": "動画の潜在空間を準備中...",
    "KSamplerAdvanced": "動画を生成中...",
    "VAEDecode": "動画をデコード中...",
    "UpscaleModelLoader": "アップスケールモデルを読み込み中...",
    "ImageUpscaleWithModel": "動画をアップスケール中...",
    "ImageScale": "解像度を調整中...",
    "VHS_VideoCombine": "動画をエンコード・保存中...",
    "SomethingToString": "パラメータを変換中...",
    "String to Float": "パラメータを変換中...",
    "easy mathInt": "フレーム数を計算中...",
    "easy int": "パラメータを設定中...",
    "easy seed": "シードを設定中...",
}


def build_node_labels(template: dict) -> dict[str, str]:
    """ワークフローテンプレートから {node_id: 表示メッセージ} を構築する。"""
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

    def handle_event(self, event: ComfyProgressEvent) -> None:
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
            self._progress.message = f"Wan2.2 動画生成中... {pct:.0f}%"

        elif etype == "execution_error":
            self._progress.state = JobState.FAILED
            self._progress.message = "生成エラー"

        else:
            # executed / execution_cached などはコールバックを呼ばない
            return

        self._notify()

    def _notify(self) -> None:
        if self._cb:
            self._cb(JobProgress(
                state=self._progress.state,
                message=self._progress.message,
                step=self._progress.step,
                total_steps=self._progress.total_steps,
            ))
