"""SCH-01/PR2: generation_executor の共通ヘルパー(resolve_output_with_retry /
load_template_for_workflow)の単体テスト。

Desktop/Discord/Remote Room の3経路に逐語コピペされていたロジックを
共通化したもの。挙動が既存3箇所と完全に同一であることを検証する。
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from makeaifactory.core.generation_executor import (
    load_template_for_workflow,
    resolve_output_with_retry,
)
from makeaifactory.domain.errors import OutputNotFoundError


class _FakeClient:
    """get_historyの返り値列を注入できるスタブ。呼び出し回数を記録する。"""

    def __init__(self, histories: list[dict]):
        self._histories = histories
        self.call_count = 0

    async def get_history(self, prompt_id: str) -> dict:
        self.call_count += 1
        # 呼び出し回数がリストを超えたら最後の要素を返す(安全側)
        idx = min(self.call_count - 1, len(self._histories) - 1)
        return self._histories[idx]


def _history_with_videos(prompt_id: str, subfolder: str, filename: str) -> dict:
    return {
        prompt_id: {
            "outputs": {
                "188": {
                    "videos": [{"filename": filename, "subfolder": subfolder}]
                }
            }
        }
    }


def _history_with_gifs(prompt_id: str, subfolder: str, filename: str) -> dict:
    # VHS_VideoCombine実物のhistory形: filename/subfolder/type/format が "gifs" キーに入る
    return {
        prompt_id: {
            "outputs": {
                "188": {
                    "gifs": [
                        {
                            "filename": filename,
                            "subfolder": subfolder,
                            "type": "output",
                            "format": "video/h264-mp4",
                        }
                    ]
                }
            }
        }
    }


# ── resolve_output_with_retry: リトライ回数系 ──────────────────────────────

@pytest.mark.asyncio
async def test_retry_succeeds_on_third_attempt(tmp_path):
    prompt_id = "prompt_abc"
    job_id = "job1"
    subfolder = "makeAiFactory/job1"
    filename = "output_00001.mp4"
    (tmp_path / subfolder).mkdir(parents=True)
    mp4_path = tmp_path / subfolder / filename
    mp4_path.write_bytes(b"fake mp4")

    empty_history = {}
    ok_history = _history_with_videos(prompt_id, subfolder, filename)
    client = _FakeClient([empty_history, empty_history, ok_history])

    output_mp4, history = await resolve_output_with_retry(
        client, prompt_id, tmp_path, job_id, delay_sec=0.0,
    )

    assert output_mp4 == mp4_path
    assert history == ok_history
    assert client.call_count == 3


@pytest.mark.asyncio
async def test_retry_exhausted_raises_output_not_found(tmp_path):
    prompt_id = "prompt_abc"
    job_id = "job1"
    client = _FakeClient([{}, {}, {}])

    with pytest.raises(OutputNotFoundError):
        await resolve_output_with_retry(client, prompt_id, tmp_path, job_id, delay_sec=0.0)

    assert client.call_count == 3


@pytest.mark.asyncio
async def test_success_on_first_attempt_calls_once(tmp_path):
    prompt_id = "prompt_abc"
    job_id = "job1"
    subfolder = "makeAiFactory/job1"
    filename = "output_00001.mp4"
    (tmp_path / subfolder).mkdir(parents=True)
    mp4_path = tmp_path / subfolder / filename
    mp4_path.write_bytes(b"fake mp4")

    ok_history = _history_with_videos(prompt_id, subfolder, filename)
    client = _FakeClient([ok_history])

    output_mp4, history = await resolve_output_with_retry(
        client, prompt_id, tmp_path, job_id, delay_sec=0.0,
    )

    assert output_mp4 == mp4_path
    assert client.call_count == 1


# ── gifs/videosキー回帰固定 ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resolves_gifs_key_format(tmp_path):
    """VHS_VideoCombineは動画(mp4)を"gifs"キーに格納する。これを解決できること。"""
    prompt_id = "prompt_gifs"
    job_id = "job_gifs"
    subfolder = "makeAiFactory/job_gifs"
    filename = "output_00001.mp4"
    (tmp_path / subfolder).mkdir(parents=True)
    mp4_path = tmp_path / subfolder / filename
    mp4_path.write_bytes(b"fake mp4")

    history = _history_with_gifs(prompt_id, subfolder, filename)
    client = _FakeClient([history])

    output_mp4, returned_history = await resolve_output_with_retry(
        client, prompt_id, tmp_path, job_id, delay_sec=0.0,
    )

    assert output_mp4 == mp4_path
    assert returned_history == history


@pytest.mark.asyncio
async def test_resolves_videos_key_format(tmp_path):
    prompt_id = "prompt_videos"
    job_id = "job_videos"
    subfolder = "makeAiFactory/job_videos"
    filename = "output_00002.mp4"
    (tmp_path / subfolder).mkdir(parents=True)
    mp4_path = tmp_path / subfolder / filename
    mp4_path.write_bytes(b"fake mp4")

    history = _history_with_videos(prompt_id, subfolder, filename)
    client = _FakeClient([history])

    output_mp4, _ = await resolve_output_with_retry(
        client, prompt_id, tmp_path, job_id, delay_sec=0.0,
    )

    assert output_mp4 == mp4_path


# ── 失敗時もhistoryへアクセスできること ─────────────────────────────────────

@pytest.mark.asyncio
async def test_failure_exposes_last_history_via_exception(tmp_path):
    """呼び出し側(Desktop経路)がOutputNotFoundError時もhistory.jsonを保存できるよう、
    例外に最後のhistoryが添付されていること。"""
    prompt_id = "prompt_abc"
    job_id = "job1"
    last_history = {"some": "partial-history-from-third-attempt"}
    client = _FakeClient([{}, {}, last_history])

    with pytest.raises(OutputNotFoundError) as exc_info:
        await resolve_output_with_retry(client, prompt_id, tmp_path, job_id, delay_sec=0.0)

    assert getattr(exc_info.value, "history", None) == last_history


# ── load_template_for_workflow ────────────────────────────────────────────

def _make_paths(tmp_path: Path, runtime_template: dict | None) -> SimpleNamespace:
    runtime_template_path = tmp_path / "makeAiFactory_runtime_template.json"
    if runtime_template is not None:
        runtime_template_path.write_text(
            json.dumps(runtime_template), encoding="utf-8"
        )
    return SimpleNamespace(
        runtime_root=tmp_path,
        runtime_template_json=lambda: runtime_template_path,
    )


def test_load_template_prefers_workflow_specific_file(tmp_path):
    templates_dir = tmp_path / "remote_room" / "templates"
    templates_dir.mkdir(parents=True)
    wf_template = {"marker": "workflow-specific"}
    (templates_dir / "myworkflow.json").write_text(json.dumps(wf_template), encoding="utf-8")

    paths = _make_paths(tmp_path, {"marker": "runtime-default"})

    result = load_template_for_workflow(paths, "myworkflow")
    assert result == wf_template


def test_load_template_falls_back_when_workflow_file_missing(tmp_path):
    # templates/<wf>.json が存在しない場合はruntime_templateへフォールバック
    runtime_template = {"marker": "runtime-default"}
    paths = _make_paths(tmp_path, runtime_template)

    result = load_template_for_workflow(paths, "unknown_workflow")
    assert result == runtime_template


def test_load_template_none_workflow_uses_runtime_template(tmp_path):
    runtime_template = {"marker": "runtime-default"}
    paths = _make_paths(tmp_path, runtime_template)

    result = load_template_for_workflow(paths, None)
    assert result == runtime_template


def test_load_template_raises_when_nothing_available(tmp_path):
    # workflow指定なし・runtime_templateも存在しない場合は例外
    paths = _make_paths(tmp_path, runtime_template=None)

    with pytest.raises(FileNotFoundError):
        load_template_for_workflow(paths, None)
