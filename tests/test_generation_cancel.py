"""SCH-01 PR4: GenerationExecutor.submit() / request_cancel() の単体テスト。

キャンセルのグローバル/interrupt誤爆(別経路のジョブ実行中に自分のcancelを押すと
他人のジョブを殺してしまう問題)を解消するための2機能を検証する。

- submit(): gate.wait_acquire(owner) → run() → release の一括化。
- request_cancel(owner, job_id=None): 実行中レジストリとowner(+job_id)を照合し、
  一致した場合のみ (a)cancelledフラグを立てる (b)/interrupt をfire-and-forget発行する。
  一致しなければ何もしない(=誤爆しない)。

test_generation_executor.py の _FakeComfyClient と同系統だが、watch_progress を
テスト側から一時停止できるようにして、run()が「実行中」の間にrequest_cancelを
呼べるようにしている。
"""
from __future__ import annotations

import asyncio
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from makeaifactory.comfy.workflow_sanitizer import sanitize_workflow
from makeaifactory.core import generation_executor as ge_module
from makeaifactory.core.generation_executor import GenerationExecutor, GenerationRequest
from makeaifactory.core.generation_gate import GenerationGate
from makeaifactory.domain.errors import JobCancelledError

SOURCE_JSON = Path(__file__).parent.parent / "app" / "workflow" / "presets" / "default.json"


@pytest.fixture
def template():
    with SOURCE_JSON.open("r", encoding="utf-8") as f:
        return sanitize_workflow(json.load(f))


class _FakeComfyClient:
    """ComfyApiClientのフェイク。base_urlを公開し、watch_progressを
    test側から一時停止できる(watch_release Event未setの間は完了しない)。
    """

    def __init__(
        self,
        prompt_id: str = "prompt_test",
        base_url: str = "http://127.0.0.1:9999",
        history: dict | None = None,
        queue_exc: Exception | None = None,
    ):
        self.base_url = base_url
        self._prompt_id = prompt_id
        self._history = history if history is not None else {}
        self.queue_exc = queue_exc
        self.calls: list[str] = []
        # 未setの間はwatch_progressが完了しない。テスト側でsetして先へ進める。
        self.watch_release = asyncio.Event()

    async def wait_until_ready(self, timeout_sec: int = 120) -> None:
        self.calls.append("wait_until_ready")

    async def upload_image(self, path: Path) -> str:
        self.calls.append("upload_image")
        return f"uploaded_{path.name}"

    async def queue_prompt(self, workflow: dict) -> str:
        self.calls.append("queue_prompt")
        if self.queue_exc is not None:
            raise self.queue_exc
        return self._prompt_id

    async def watch_progress(self, prompt_id: str):
        self.calls.append("watch_progress")
        await self.watch_release.wait()
        return
        yield  # pragma: no cover — 到達しないがasync generatorにするためのダミー

    async def get_history(self, prompt_id: str) -> dict:
        self.calls.append("get_history")
        return self._history


def _make_paths(comfyui_output_dir: Path) -> SimpleNamespace:
    return SimpleNamespace(comfyui_output_dir=comfyui_output_dir)


def _make_request(template: dict, tmp_path: Path, *, job_id: str = "job1", owner: str = "desktop") -> tuple[GenerationRequest, Path]:
    job_dir = tmp_path / f"job_dir_{job_id}"
    job_dir.mkdir(parents=True, exist_ok=True)
    input_image = tmp_path / f"input_{job_id}.png"
    input_image.write_bytes(b"fake-image-bytes")
    req = GenerationRequest(
        owner=owner,
        job_id=job_id,
        input_image=input_image,
        job_dir=job_dir,
        template=template,
        seed=123,
        unet_high_name="high.gguf",
        unet_low_name="low.gguf",
        upload_basename=f"upload_{job_id}.png",
    )
    return req, job_dir


def _write_output(output_dir: Path, job_id: str, prompt_id: str, filename: str = "out.mp4") -> dict:
    """resolve_output_mp4が解決できる mp4 ファイル+history を用意する。"""
    subfolder = f"makeAiFactory/{job_id}"
    (output_dir / subfolder).mkdir(parents=True, exist_ok=True)
    (output_dir / subfolder / filename).write_bytes(b"fake mp4 bytes")
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


async def _run_registered(executor: GenerationExecutor, req: GenerationRequest, client: _FakeComfyClient):
    """run()をバックグラウンドタスクとして起動し、watch_progressに到達する
    (=実行中レジストリへの登録が完了し、watch_releaseの待ちで一時停止する)
    までポーリングしてから、そのタスクを返す。
    """
    task = asyncio.create_task(executor.run(req, client))
    for _ in range(200):
        if "watch_progress" in client.calls:
            return task
        await asyncio.sleep(0.005)
    raise AssertionError("watch_progressに到達しませんでした")


# ── submit(): 取得→run→release ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_submit_immediate_acquire_runs_and_releases(template, tmp_path):
    gate = GenerationGate(None)
    output_dir = tmp_path / "comfyui_output"
    executor = GenerationExecutor(_make_paths(output_dir), gate)
    req, _job_dir = _make_request(template, tmp_path, job_id="job_ok")
    history = _write_output(output_dir, "job_ok", "prompt_ok")
    client = _FakeComfyClient(prompt_id="prompt_ok", history=history)
    client.watch_release.set()  # watch_progressを即完了させる

    assert gate.holder is None
    result = await executor.submit(req, client)

    assert result.output_path.exists()
    assert gate.holder is None  # release済み


@pytest.mark.asyncio
async def test_submit_holds_gate_only_while_run_is_in_progress(template, tmp_path):
    """gate取得→run→release の順序そのものを検証する。

    watch_progressで意図的にrun()を一時停止させ、その間はgateがacquireされた
    ままであること(=runの前に取得済み)、releaseされるのはrun完了後である
    ことを確認する。
    """
    gate = GenerationGate(None)
    output_dir = tmp_path / "comfyui_output"
    executor = GenerationExecutor(_make_paths(output_dir), gate)
    req, _job_dir = _make_request(template, tmp_path, job_id="job_order")
    history = _write_output(output_dir, "job_order", "prompt_order")
    client = _FakeComfyClient(prompt_id="prompt_order", history=history)
    # watch_releaseは未setのまま: submit()内部のrun()がwatch_progressで一時停止する

    assert gate.holder is None
    task = asyncio.create_task(executor.submit(req, client))
    for _ in range(200):
        if "watch_progress" in client.calls:
            break
        await asyncio.sleep(0.005)
    else:
        raise AssertionError("watch_progressに到達しませんでした")

    # run()実行中(=watch_progressで一時停止中)はgateを保持している
    assert gate.holder == "desktop"

    client.watch_release.set()
    result = await task

    # run()完了後は必ずreleaseされている
    assert gate.holder is None
    assert result.output_path.exists()


@pytest.mark.asyncio
async def test_submit_releases_even_on_run_exception(template, tmp_path):
    gate = GenerationGate(None)
    output_dir = tmp_path / "comfyui_output"
    executor = GenerationExecutor(_make_paths(output_dir), gate)
    req, _job_dir = _make_request(template, tmp_path, job_id="job_fail")
    client = _FakeComfyClient(prompt_id="prompt_fail", queue_exc=RuntimeError("boom"))
    client.watch_release.set()

    with pytest.raises(RuntimeError, match="boom"):
        await executor.submit(req, client)

    assert gate.holder is None


@pytest.mark.asyncio
async def test_submit_cancel_during_wait_raises_and_leaves_holder_untouched(template, tmp_path):
    gate = GenerationGate(None)
    other_lease = gate.try_acquire("discord")  # 他ownerが既に保持している状況
    assert other_lease is not None

    output_dir = tmp_path / "comfyui_output"
    executor = GenerationExecutor(_make_paths(output_dir), gate)
    req, _job_dir = _make_request(template, tmp_path, job_id="job_wait")
    client = _FakeComfyClient(prompt_id="prompt_wait")

    with pytest.raises(JobCancelledError):
        await executor.submit(req, client, cancel_check=lambda: True)

    # 取得できていないので他ownerのholderがそのまま(誤ってreleaseしていない)
    assert gate.holder == "discord"
    # runにすら入っていない(wait_acquireの時点でキャンセルされたため)
    assert client.calls == []

    gate.release(other_lease)


@pytest.mark.asyncio
async def test_submit_immediate_acquire_does_not_call_on_wait(template, tmp_path):
    gate = GenerationGate(None)
    output_dir = tmp_path / "comfyui_output"
    executor = GenerationExecutor(_make_paths(output_dir), gate)
    req, _job_dir = _make_request(template, tmp_path, job_id="job_imm")
    history = _write_output(output_dir, "job_imm", "prompt_imm")
    client = _FakeComfyClient(prompt_id="prompt_imm", history=history)
    client.watch_release.set()

    on_wait_calls: list[int] = []
    result = await executor.submit(req, client, on_wait=lambda: on_wait_calls.append(1))

    assert on_wait_calls == []
    assert result.output_path.exists()


@pytest.mark.asyncio
async def test_submit_delayed_acquire_calls_on_wait_once(template, tmp_path):
    gate = GenerationGate(None)
    other_lease = gate.try_acquire("discord")
    assert other_lease is not None

    output_dir = tmp_path / "comfyui_output"
    executor = GenerationExecutor(_make_paths(output_dir), gate)
    req, _job_dir = _make_request(template, tmp_path, job_id="job_delay")
    history = _write_output(output_dir, "job_delay", "prompt_delay")
    client = _FakeComfyClient(prompt_id="prompt_delay", history=history)
    client.watch_release.set()

    on_wait_calls: list[int] = []

    async def _release_soon() -> None:
        await asyncio.sleep(0.05)
        gate.release(other_lease)

    asyncio.create_task(_release_soon())
    result = await executor.submit(req, client, on_wait=lambda: on_wait_calls.append(1))

    assert on_wait_calls == [1]
    assert result.output_path.exists()
    assert gate.holder is None


# ── request_cancel(): owner/job_id照合 ──────────────────────────────────────

@pytest.mark.asyncio
async def test_request_cancel_owner_match_sets_flag_and_fires_interrupt(template, tmp_path, monkeypatch):
    posted: list[str] = []
    posted_event = threading.Event()

    def _fake_post(url, timeout=None):
        posted.append(url)
        posted_event.set()
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(ge_module, "httpx", SimpleNamespace(post=_fake_post))

    gate = GenerationGate(None)
    output_dir = tmp_path / "comfyui_output"
    executor = GenerationExecutor(_make_paths(output_dir), gate)
    req, _job_dir = _make_request(template, tmp_path, job_id="job_a", owner="desktop")
    client = _FakeComfyClient(prompt_id="prompt_a", base_url="http://127.0.0.1:12345")

    task = await _run_registered(executor, req, client)

    ok = executor.request_cancel("desktop")
    assert ok is True
    assert posted_event.wait(timeout=2), "interrupt発行スレッドが完了しませんでした"
    assert posted == ["http://127.0.0.1:12345/interrupt"]

    client.watch_release.set()
    with pytest.raises(JobCancelledError):
        await task
    # get_historyまで到達していない(watch完了直後のcancel判定でJobCancelledErrorに
    # なったため。=レジストリのcancelledフラグがcancel判定へ合流している)
    assert "get_history" not in client.calls


@pytest.mark.asyncio
async def test_request_cancel_owner_mismatch_returns_false_and_does_not_fire(template, tmp_path, monkeypatch):
    posted: list[str] = []
    monkeypatch.setattr(
        ge_module, "httpx",
        SimpleNamespace(post=lambda url, timeout=None: posted.append(url)),
    )

    gate = GenerationGate(None)
    output_dir = tmp_path / "comfyui_output"
    executor = GenerationExecutor(_make_paths(output_dir), gate)
    req, _job_dir = _make_request(template, tmp_path, job_id="job_b", owner="desktop")
    history = _write_output(output_dir, "job_b", "prompt_b")
    client = _FakeComfyClient(prompt_id="prompt_b", history=history)

    task = await _run_registered(executor, req, client)

    ok = executor.request_cancel("discord")  # reqのownerは"desktop"なので不一致
    assert ok is False

    await asyncio.sleep(0.05)
    assert posted == []  # interrupt発行されていない(誤爆していない)

    client.watch_release.set()
    result = await task  # 正常完了(キャンセルされていない)
    assert result.output_path.exists()


@pytest.mark.asyncio
async def test_request_cancel_job_id_match_and_mismatch(template, tmp_path):
    gate = GenerationGate(None)
    output_dir = tmp_path / "comfyui_output"
    executor = GenerationExecutor(_make_paths(output_dir), gate)
    req, _job_dir = _make_request(template, tmp_path, job_id="job_c", owner="desktop")
    # base_url="" にしてinterrupt発行(別スレッド)を起こさず、照合ロジックのみ検証する
    client = _FakeComfyClient(prompt_id="prompt_c", base_url="")

    task = await _run_registered(executor, req, client)

    assert executor.request_cancel("desktop", job_id="not_job_c") is False
    assert executor.request_cancel("desktop", job_id="job_c") is True

    client.watch_release.set()
    with pytest.raises(JobCancelledError):
        await task


def test_request_cancel_returns_false_when_nothing_running(tmp_path):
    gate = GenerationGate(None)
    executor = GenerationExecutor(_make_paths(tmp_path), gate)
    assert executor.request_cancel("desktop") is False
    assert executor.request_cancel("desktop", job_id="whatever") is False


# ── interrupt発行の例外握りつぶし ────────────────────────────────────────────

def test_fire_interrupt_exception_is_swallowed(tmp_path, monkeypatch):
    def _raising_post(url, timeout=None):
        raise RuntimeError("network down")

    monkeypatch.setattr(ge_module, "httpx", SimpleNamespace(post=_raising_post))

    gate = GenerationGate(None)
    executor = GenerationExecutor(_make_paths(tmp_path), gate)

    done = threading.Event()
    errors: list[Exception] = []
    original_fire = executor._fire_interrupt

    def _tracking_fire(base_url: str, owner: str) -> None:
        try:
            original_fire(base_url, owner)
        except Exception as e:  # pragma: no cover — 本来ここに来ないことを確認するため
            errors.append(e)
        finally:
            done.set()

    executor._fire_interrupt = _tracking_fire

    # レジストリへ手動登録してrequest_cancelの発火経路を検証する
    # (実際の run() を通さず、_activeへ直接1件だけ入れる軽量な方法)
    from makeaifactory.core.generation_executor import _ActiveRun
    active = _ActiveRun(job_id="job_x", base_url="http://127.0.0.1:1")
    executor._active["desktop"] = active

    ok = executor.request_cancel("desktop")

    assert ok is True
    assert done.wait(timeout=2), "interrupt発行スレッドが完了しませんでした"
    # _fire_interrupt自体は例外を外へ漏らさない(内部でtry/exceptしている)
    assert errors == []
