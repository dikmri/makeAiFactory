from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from makeaifactory.comfy.progress_tracker import (
    ProgressTracker,
    StageProgressEstimator,
    count_progress_stages,
)
from makeaifactory.domain.progress import ComfyProgressEvent


def test_count_progress_stages():
    template = {
        "228": {"class_type": "KSamplerAdvanced"},
        "275": {"class_type": "KSamplerAdvanced"},
        "282": {"class_type": "KSamplerAdvanced"},
        "167": {"class_type": "ImageUpscaleWithModel"},
        "189": {"class_type": "LoadImage"},
    }
    assert count_progress_stages(template) == 4


def test_count_progress_stages_minimum_one():
    assert count_progress_stages({}) == 1


def test_stage_estimator_does_not_go_backward_across_nodes():
    estimator = StageProgressEstimator(stage_count=2)

    pcts = [estimator.update("228", step, 20) for step in range(1, 21)]
    # ノード228が完了に近づくほど進捗は単調増加する
    assert pcts == sorted(pcts)
    assert pcts[-1] == 50.0  # 2ステージ中1つ完了 = 50%

    # 新しいノード(275)に切り替わってもステップが0から再開しても後退しない
    pct_after_switch = estimator.update("275", 1, 20)
    assert pct_after_switch >= pcts[-1]


def test_progress_tracker_handles_multi_node_workflow_without_regression():
    received: list[float] = []
    tracker = ProgressTracker(on_progress=lambda p: received.append(p.percent), stage_count=2)

    for step in range(1, 5):
        tracker.handle_event(ComfyProgressEvent(event_type="progress", node_id="275", step=step, max_steps=4))
    for step in [1, 3, 5, 7, 29]:
        tracker.handle_event(ComfyProgressEvent(event_type="progress", node_id="167", step=step, max_steps=29))

    assert received == sorted(received)
