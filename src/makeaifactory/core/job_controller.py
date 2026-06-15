from __future__ import annotations

import asyncio
import json
import logging
import random
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from ..comfy.api_client import ComfyApiClient
from ..comfy.output_resolver import resolve_output_mp4
from ..comfy.progress_tracker import ProgressTracker, build_node_labels
from ..comfy.workflow_patcher import WorkflowPatchContext, make_output_prefix, patch_workflow
from ..comfy.server_controller import ComfyServerController
from ..core.paths import AppPaths
from ..core.settings_store import SettingsStore
from ..core.vram_monitor import VramMonitor
from ..domain.errors import OutputNotFoundError
from ..domain.job import Job
from ..domain.progress import BenchmarkResult, JobProgress, JobState

if TYPE_CHECKING:
    from ..runtime.system_probe import GpuInfo

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[JobProgress], None]


class JobController:
    def __init__(
        self,
        paths: AppPaths,
        server: ComfyServerController,
        settings: SettingsStore,
        template: dict,
        gpu_info: "GpuInfo | None" = None,
    ):
        self._paths = paths
        self._server = server
        self._settings = settings
        self._template = template
        self._gpu_info = gpu_info
        self._current_job: Job | None = None
        self._client: ComfyApiClient | None = None

    async def run_job(
        self,
        input_image: Path,
        on_progress: ProgressCallback | None = None,
    ) -> tuple[Path, BenchmarkResult]:
        job = Job()
        self._current_job = job
        logger.info("Job開始: %s", job.job_id)
        start_time = time.monotonic()

        job_dir = self._paths.job_dir(job.job_id, job.date_str)
        job_dir.mkdir(parents=True, exist_ok=True)

        # 入力画像をジョブディレクトリへコピー
        input_copy = job_dir / ("input" + input_image.suffix)
        shutil.copy2(input_image, input_copy)
        job.input_path = str(input_copy)

        # ComfyUI起動確認
        if not self._server.is_running:
            self._server.start()

        client = ComfyApiClient(self._server.base_url)
        self._client = client
        await client.wait_until_ready()

        # 画像アップロード
        job.status = JobState.UPLOADING
        _notify(on_progress, JobProgress(state=JobState.UPLOADING, message="画像をアップロードしています..."))

        upload_name = f"makeaifactory_{job.job_id}{input_image.suffix}"
        renamed = job_dir / upload_name
        shutil.copy2(input_image, renamed)
        uploaded_name = await client.upload_image(renamed)
        job.uploaded_image_name = uploaded_name

        # workflow patch
        seed = random.randint(0, 2**32 - 1) if self._settings.seed_randomize else None
        ctx = WorkflowPatchContext(
            job_id=job.job_id,
            uploaded_image_name=uploaded_name,
            output_prefix=make_output_prefix(job.job_id),
            seed=seed,
        )
        patched = patch_workflow(self._template, ctx)
        job.seed = seed

        # workflow を保存
        with (job_dir / "workflow.json").open("w", encoding="utf-8") as f:
            json.dump(patched, f, ensure_ascii=False, indent=2)

        # prompt 投入
        job.status = JobState.QUEUED
        _notify(on_progress, JobProgress(state=JobState.QUEUED, message="生成キューに追加しています..."))
        prompt_id = await client.queue_prompt(patched)
        job.prompt_id = prompt_id

        # VRAM計測しながら生成を監視
        async with VramMonitor() as vram:
            tracker = ProgressTracker(
                on_progress=on_progress,
                node_labels=build_node_labels(self._template),
            )
            async for event in client.watch_progress(prompt_id):
                tracker.handle_event(event)
                if event.event_type == "execution_error":
                    break

        # history 取得
        job.status = JobState.RESOLVING_OUTPUT
        history = await client.get_history(prompt_id)
        with (job_dir / "history.json").open("w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)

        # output mp4 解決
        output_mp4 = resolve_output_mp4(
            history,
            prompt_id,
            self._paths.comfyui_output_dir,
            job.job_id,
        )

        final_output = job_dir / "output.mp4"
        shutil.copy2(output_mp4, final_output)
        job.output_path = str(final_output)
        job.status = JobState.COMPLETED

        elapsed = time.monotonic() - start_time
        bench = BenchmarkResult(
            elapsed_sec=elapsed,
            vram_peak_gb=vram.peak_gb,
            vram_avg_gb=vram.avg_gb,
            vram_total_gb=self._gpu_info.vram_gb if self._gpu_info else 0.0,
            gpu_name=self._gpu_info.name if self._gpu_info else "",
            vram_mode=self._settings.vram_mode,
        )

        with (job_dir / "job.json").open("w", encoding="utf-8") as f:
            json.dump(job.to_dict(), f, ensure_ascii=False, indent=2)

        logger.info(
            "Job完了: %s → %s (%.0fs, VRAMピーク=%.1fGB, モード=%s)",
            job.job_id, final_output, elapsed, vram.peak_gb, self._settings.vram_mode,
        )
        _notify(on_progress, JobProgress(state=JobState.COMPLETED, message="完成！"))
        return final_output, bench

    async def cancel_current(self) -> None:
        if self._client:
            await self._client.interrupt()
        if self._current_job:
            self._current_job.status = JobState.CANCELLED


def _notify(cb: ProgressCallback | None, progress: JobProgress) -> None:
    if cb:
        cb(progress)
