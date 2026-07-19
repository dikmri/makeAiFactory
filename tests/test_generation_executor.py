"""SCH-01 PR3: GenerationExecutor.run (3経路共通の生成実行本体) の単体テスト。

Desktop (`core/job_controller.py`) / Discord (`core/discord_bot_controller.py`) /
Remote Room (`remote_room/controller.py`) の3経路が共有する
「wait_until_ready→入力画像コピー+upload_image→WorkflowPatchContext組立+
patch_workflow→queue_prompt→watch_progressループ→resolve_output_with_retry→
job_dir/output.mp4コピー」という一連の流れを、フェイクComfyApiClientを注入して検証する。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from makeaifactory.comfy.workflow_patcher import DevModeOverrides
from makeaifactory.comfy.workflow_sanitizer import sanitize_workflow
from makeaifactory.constants import (
    LOADIMAGE_NODE_ID,
    SAGE_ATTN_HIGH_NODE_ID,
    SAGE_ATTN_LOW_NODE_ID,
    SEED_NODE_ID,
    UNET_HIGH_NODE_ID,
    UNET_LOW_NODE_ID,
)
from makeaifactory.core.generation_executor import GenerationExecutor, GenerationRequest
from makeaifactory.domain.errors import JobCancelledError, OutputNotFoundError
from makeaifactory.domain.progress import ComfyProgressEvent

# 実行時に再生成される runtime_template ではなく、コミット済みで不変の
# presets/default.json をサニタイズしたものをテンプレートに使う (test_workflow_patcher.pyと同様)。
SOURCE_JSON = Path(__file__).parent.parent / "app" / "workflow" / "presets" / "default.json"


@pytest.fixture
def template():
    with SOURCE_JSON.open("r", encoding="utf-8") as f:
        return sanitize_workflow(json.load(f))


class _FakeComfyClient:
    """ComfyApiClient のフェイク。呼び出し順序・引数を記録し、任意の失敗を注入できる。"""

    def __init__(
        self,
        histories: list[dict] | None = None,
        watch_events: list[ComfyProgressEvent] | None = None,
        prompt_id: str = "prompt_test_1",
        wait_exc: Exception | None = None,
        upload_exc: Exception | None = None,
        queue_exc: Exception | None = None,
        watch_exc: Exception | None = None,
    ):
        self.histories = histories if histories is not None else [{}]
        self.watch_events = watch_events if watch_events is not None else []
        self._prompt_id = prompt_id
        self.wait_exc = wait_exc
        self.upload_exc = upload_exc
        self.queue_exc = queue_exc
        self.watch_exc = watch_exc

        self.calls: list[str] = []
        self.wait_until_ready_timeout: int | None = None
        self.uploaded_path: Path | None = None
        self.queued_workflow: dict | None = None
        self.watch_prompt_id: str | None = None
        self.get_history_calls = 0

    async def wait_until_ready(self, timeout_sec: int = 120) -> None:
        self.calls.append("wait_until_ready")
        self.wait_until_ready_timeout = timeout_sec
        if self.wait_exc is not None:
            raise self.wait_exc

    async def upload_image(self, path: Path) -> str:
        self.calls.append("upload_image")
        self.uploaded_path = path
        if self.upload_exc is not None:
            raise self.upload_exc
        return f"uploaded_{path.name}"

    async def queue_prompt(self, workflow: dict) -> str:
        self.calls.append("queue_prompt")
        self.queued_workflow = workflow
        if self.queue_exc is not None:
            raise self.queue_exc
        return self._prompt_id

    async def watch_progress(self, prompt_id: str):
        self.calls.append("watch_progress")
        self.watch_prompt_id = prompt_id
        for event in self.watch_events:
            yield event
        if self.watch_exc is not None:
            raise self.watch_exc

    async def get_history(self, prompt_id: str) -> dict:
        self.calls.append("get_history")
        idx = min(self.get_history_calls, len(self.histories) - 1)
        self.get_history_calls += 1
        return self.histories[idx]


def _make_paths(comfyui_output_dir: Path) -> SimpleNamespace:
    return SimpleNamespace(comfyui_output_dir=comfyui_output_dir)


def _history_with_gifs(prompt_id: str, subfolder: str, filename: str) -> dict:
    # VHS_VideoCombine実物のhistory形: 動画(mp4)は "gifs" キーに入る
    return {
        prompt_id: {
            "outputs": {
                "188": {
                    "gifs": [
                        {"filename": filename, "subfolder": subfolder, "type": "output", "format": "video/h264-mp4"}
                    ]
                }
            }
        }
    }


def _make_request(
    template: dict,
    tmp_path: Path,
    *,
    job_id: str = "job1",
    seed: int | None = 999,
    sage_attention_mode: str = "disabled",
    upload_basename: str = "makeaifactory_job1.png",
    dev_overrides: DevModeOverrides | None = None,
    save_workflow_json: bool = False,
    ready_timeout_sec: int | None = None,
) -> tuple[GenerationRequest, Path, Path]:
    job_dir = tmp_path / "job_dir"
    job_dir.mkdir(parents=True, exist_ok=True)
    input_image = tmp_path / "input_src.png"
    input_image.write_bytes(b"fake-image-bytes")

    req = GenerationRequest(
        owner="desktop",
        job_id=job_id,
        input_image=input_image,
        job_dir=job_dir,
        template=template,
        seed=seed,
        unet_high_name="high.gguf",
        unet_low_name="low.gguf",
        sage_attention_mode=sage_attention_mode,
        upload_basename=upload_basename,
        dev_overrides=dev_overrides,
        save_workflow_json=save_workflow_json,
        ready_timeout_sec=ready_timeout_sec,
    )
    return req, job_dir, input_image


# ── happy path ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_happy_path_call_order_and_workflow_patch(template, tmp_path):
    req, job_dir, _input_image = _make_request(
        template, tmp_path, seed=12345, sage_attention_mode="sageattn_qk_int8_pv_fp16_cuda",
    )

    output_dir = tmp_path / "comfyui_output"
    subfolder = "makeAiFactory/job1"
    filename = "output_00001.mp4"
    (output_dir / subfolder).mkdir(parents=True)
    mp4_path = output_dir / subfolder / filename
    mp4_path.write_bytes(b"fake mp4 bytes")

    prompt_id = "prompt_abc"
    history = _history_with_gifs(prompt_id, subfolder, filename)
    client = _FakeComfyClient(histories=[history], prompt_id=prompt_id)

    executor = GenerationExecutor(_make_paths(output_dir))
    result = await executor.run(req, client)

    # 呼び出し順序: wait_ready → upload → queue → watch → history
    assert client.calls == ["wait_until_ready", "upload_image", "queue_prompt", "watch_progress", "get_history"]

    # workflowへの反映 (patch_workflow実物経由)
    assert client.queued_workflow[SEED_NODE_ID]["inputs"]["seed"] == 12345
    assert client.queued_workflow[LOADIMAGE_NODE_ID]["inputs"]["image"] == "uploaded_makeaifactory_job1.png"
    assert client.queued_workflow[SAGE_ATTN_HIGH_NODE_ID]["inputs"]["sage_attention"] == "sageattn_qk_int8_pv_fp16_cuda"
    assert client.queued_workflow[SAGE_ATTN_LOW_NODE_ID]["inputs"]["sage_attention"] == "sageattn_qk_int8_pv_fp16_cuda"
    assert client.queued_workflow[UNET_HIGH_NODE_ID]["inputs"]["unet_name"] == "high.gguf"
    assert client.queued_workflow[UNET_LOW_NODE_ID]["inputs"]["unet_name"] == "low.gguf"

    # アップロード用コピーがjob_dirへ既存命名で作られていること
    assert client.uploaded_path == job_dir / "makeaifactory_job1.png"
    assert client.uploaded_path.exists()

    # gifs形式historyからoutput.mp4がjob_dirへコピーされていること
    assert result.output_path == job_dir / "output.mp4"
    assert result.output_path.read_bytes() == b"fake mp4 bytes"
    assert result.prompt_id == prompt_id
    assert result.uploaded_image_name == "uploaded_makeaifactory_job1.png"
    assert result.history == history


@pytest.mark.asyncio
async def test_on_stage_fires_in_order(template, tmp_path):
    req, job_dir, _ = _make_request(template, tmp_path)
    output_dir = tmp_path / "comfyui_output"
    subfolder = "makeAiFactory/job1"
    filename = "out.mp4"
    (output_dir / subfolder).mkdir(parents=True)
    (output_dir / subfolder / filename).write_bytes(b"x")

    prompt_id = "prompt_stage"
    history = _history_with_gifs(prompt_id, subfolder, filename)
    client = _FakeComfyClient(histories=[history], prompt_id=prompt_id)

    stages: list[str] = []
    executor = GenerationExecutor(_make_paths(output_dir))
    await executor.run(req, client, on_stage=stages.append)

    assert stages == ["connecting", "uploading", "queueing", "generating", "resolving"]


@pytest.mark.asyncio
async def test_on_event_receives_same_sequence_as_watch_progress(template, tmp_path):
    events = [
        ComfyProgressEvent(event_type="execution_start", prompt_id="prompt_ev"),
        ComfyProgressEvent(event_type="progress", prompt_id="prompt_ev", node_id="10", step=1, max_steps=4),
        ComfyProgressEvent(event_type="progress", prompt_id="prompt_ev", node_id="10", step=4, max_steps=4),
        ComfyProgressEvent(event_type="executing", prompt_id="prompt_ev", node_id=""),
    ]
    req, job_dir, _ = _make_request(template, tmp_path)
    output_dir = tmp_path / "comfyui_output"
    subfolder = "makeAiFactory/job1"
    filename = "out.mp4"
    (output_dir / subfolder).mkdir(parents=True)
    (output_dir / subfolder / filename).write_bytes(b"x")

    prompt_id = "prompt_ev"
    history = _history_with_gifs(prompt_id, subfolder, filename)
    client = _FakeComfyClient(histories=[history], watch_events=events, prompt_id=prompt_id)

    received: list[object] = []
    executor = GenerationExecutor(_make_paths(output_dir))
    await executor.run(req, client, on_event=received.append)

    assert received == events


# ── save_workflow_json ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_save_workflow_json_true_writes_file(template, tmp_path):
    req, job_dir, _ = _make_request(template, tmp_path, save_workflow_json=True)
    output_dir = tmp_path / "comfyui_output"
    subfolder = "makeAiFactory/job1"
    filename = "out.mp4"
    (output_dir / subfolder).mkdir(parents=True)
    (output_dir / subfolder / filename).write_bytes(b"x")
    prompt_id = "prompt_wf"
    history = _history_with_gifs(prompt_id, subfolder, filename)
    client = _FakeComfyClient(histories=[history], prompt_id=prompt_id)

    executor = GenerationExecutor(_make_paths(output_dir))
    await executor.run(req, client)

    assert (job_dir / "workflow.json").exists()
    saved = json.loads((job_dir / "workflow.json").read_text(encoding="utf-8"))
    assert saved[SEED_NODE_ID]["inputs"]["seed"] == req.seed


@pytest.mark.asyncio
async def test_save_workflow_json_false_no_file(template, tmp_path):
    req, job_dir, _ = _make_request(template, tmp_path, save_workflow_json=False)
    output_dir = tmp_path / "comfyui_output"
    subfolder = "makeAiFactory/job1"
    filename = "out.mp4"
    (output_dir / subfolder).mkdir(parents=True)
    (output_dir / subfolder / filename).write_bytes(b"x")
    prompt_id = "prompt_wf2"
    history = _history_with_gifs(prompt_id, subfolder, filename)
    client = _FakeComfyClient(histories=[history], prompt_id=prompt_id)

    executor = GenerationExecutor(_make_paths(output_dir))
    await executor.run(req, client)

    assert not (job_dir / "workflow.json").exists()


# ── dev_overrides ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dev_overrides_applied_after_patch(template, tmp_path):
    ov = DevModeOverrides(positive_prompt="新しいプロンプト")
    req, job_dir, _ = _make_request(template, tmp_path, dev_overrides=ov)
    output_dir = tmp_path / "comfyui_output"
    subfolder = "makeAiFactory/job1"
    filename = "out.mp4"
    (output_dir / subfolder).mkdir(parents=True)
    (output_dir / subfolder / filename).write_bytes(b"x")
    prompt_id = "prompt_dev"
    history = _history_with_gifs(prompt_id, subfolder, filename)
    client = _FakeComfyClient(histories=[history], prompt_id=prompt_id)

    executor = GenerationExecutor(_make_paths(output_dir))
    await executor.run(req, client)

    assert client.queued_workflow["48"]["inputs"]["value"] == "新しいプロンプト"


# ── 失敗注入 ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_queue_prompt_exception_propagates(template, tmp_path):
    req, job_dir, _ = _make_request(template, tmp_path)
    output_dir = tmp_path / "comfyui_output"
    client = _FakeComfyClient(queue_exc=RuntimeError("queue failed"))

    executor = GenerationExecutor(_make_paths(output_dir))
    with pytest.raises(RuntimeError, match="queue failed"):
        await executor.run(req, client)

    assert client.calls == ["wait_until_ready", "upload_image", "queue_prompt"]


@pytest.mark.asyncio
async def test_watch_progress_exception_propagates(template, tmp_path):
    events = [ComfyProgressEvent(event_type="execution_start", prompt_id="p")]
    req, job_dir, _ = _make_request(template, tmp_path)
    output_dir = tmp_path / "comfyui_output"
    client = _FakeComfyClient(watch_events=events, watch_exc=RuntimeError("watch failed"))

    received: list[object] = []
    executor = GenerationExecutor(_make_paths(output_dir))
    with pytest.raises(RuntimeError, match="watch failed"):
        await executor.run(req, client, on_event=received.append)

    assert received == events
    assert "get_history" not in client.calls


@pytest.mark.asyncio
async def test_output_not_found_after_three_empty_histories(template, tmp_path):
    req, job_dir, _ = _make_request(template, tmp_path)
    output_dir = tmp_path / "comfyui_output"
    client = _FakeComfyClient(histories=[{}, {}, {}])

    executor = GenerationExecutor(_make_paths(output_dir))
    with pytest.raises(OutputNotFoundError) as exc_info:
        await executor.run(req, client)

    assert getattr(exc_info.value, "history", None) == {}
    assert client.get_history_calls == 3


@pytest.mark.asyncio
async def test_succeeds_on_third_history_attempt(template, tmp_path):
    req, job_dir, _ = _make_request(template, tmp_path, job_id="job_retry")
    output_dir = tmp_path / "comfyui_output"
    subfolder = "makeAiFactory/job_retry"
    filename = "out.mp4"
    (output_dir / subfolder).mkdir(parents=True)
    (output_dir / subfolder / filename).write_bytes(b"retried mp4")

    prompt_id = "prompt_retry"
    ok_history = _history_with_gifs(prompt_id, subfolder, filename)
    client = _FakeComfyClient(histories=[{}, {}, ok_history], prompt_id=prompt_id)

    executor = GenerationExecutor(_make_paths(output_dir))
    result = await executor.run(req, client)

    assert client.get_history_calls == 3
    assert result.output_path.read_bytes() == b"retried mp4"


@pytest.mark.asyncio
async def test_cancel_check_true_raises_job_cancelled_error(template, tmp_path):
    req, job_dir, _ = _make_request(template, tmp_path)
    output_dir = tmp_path / "comfyui_output"
    client = _FakeComfyClient()

    executor = GenerationExecutor(_make_paths(output_dir))
    with pytest.raises(JobCancelledError):
        await executor.run(req, client, cancel_check=lambda: True)

    # キャンセル判定はresolve(get_history)より前に行われる
    assert "get_history" not in client.calls


@pytest.mark.asyncio
async def test_upload_image_exception_propagates(template, tmp_path):
    req, job_dir, _ = _make_request(template, tmp_path)
    output_dir = tmp_path / "comfyui_output"
    client = _FakeComfyClient(upload_exc=RuntimeError("upload failed"))

    executor = GenerationExecutor(_make_paths(output_dir))
    with pytest.raises(RuntimeError, match="upload failed"):
        await executor.run(req, client)

    assert client.calls == ["wait_until_ready", "upload_image"]


# ── ready_timeout_sec ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ready_timeout_sec_passed_through_when_set(template, tmp_path):
    req, job_dir, _ = _make_request(template, tmp_path, ready_timeout_sec=30)
    output_dir = tmp_path / "comfyui_output"
    subfolder = "makeAiFactory/job1"
    filename = "out.mp4"
    (output_dir / subfolder).mkdir(parents=True)
    (output_dir / subfolder / filename).write_bytes(b"x")
    prompt_id = "prompt_timeout"
    history = _history_with_gifs(prompt_id, subfolder, filename)
    client = _FakeComfyClient(histories=[history], prompt_id=prompt_id)

    executor = GenerationExecutor(_make_paths(output_dir))
    await executor.run(req, client)

    assert client.wait_until_ready_timeout == 30


@pytest.mark.asyncio
async def test_ready_timeout_sec_none_uses_client_default(template, tmp_path):
    req, job_dir, _ = _make_request(template, tmp_path, ready_timeout_sec=None)
    output_dir = tmp_path / "comfyui_output"
    subfolder = "makeAiFactory/job1"
    filename = "out.mp4"
    (output_dir / subfolder).mkdir(parents=True)
    (output_dir / subfolder / filename).write_bytes(b"x")
    prompt_id = "prompt_default_timeout"
    history = _history_with_gifs(prompt_id, subfolder, filename)
    client = _FakeComfyClient(histories=[history], prompt_id=prompt_id)

    executor = GenerationExecutor(_make_paths(output_dir))
    await executor.run(req, client)

    # req.ready_timeout_sec未指定の場合はwait_until_ready()を引数無しで呼ぶため、
    # フェイク側のデフォルト値(120)がそのまま観測される
    assert client.wait_until_ready_timeout == 120
