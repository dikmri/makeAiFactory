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
from ..comfy.output_resolver import resolve_output_image
from ..comfy.progress_tracker import ProgressTracker, build_node_labels
from ..comfy.workflow_patcher import WorkflowPatchContext, make_output_prefix, patch_workflow
from ..comfy.server_controller import ComfyServerController
from ..core.paths import AppPaths
from ..core.settings_store import SettingsStore
from ..core.vram_monitor import RamMonitor, VramMonitor
from ..domain.job import Job
from ..domain.errors import GenerationError
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
        ram_total_gb: float = 0.0,
        sage_attention_mode: str = "",
        sage_attention_available: bool = False,
    ):
        self._paths = paths
        self._server = server
        self._settings = settings
        self._template = template
        self._gpu_info = gpu_info
        self._ram_total_gb = ram_total_gb
        self._sage_attention_mode = sage_attention_mode or "disabled"
        self._sage_attention_available = sage_attention_available
        self._current_job: Job | None = None
        self._client: ComfyApiClient | None = None

    async def run_job(
        self,
        positive_prompt: str,
        negative_prompt: str,
        on_progress: ProgressCallback | None = None,
        width: int | None = None,
        height: int | None = None,
    ) -> tuple[Path, BenchmarkResult]:
        job = Job()
        self._current_job = job
        logger.info("Job開始: %s", job.job_id)
        start_time = time.monotonic()

        job_dir = self._paths.job_dir(job.job_id, job.date_str)
        job_dir.mkdir(parents=True, exist_ok=True)

        if not self._server.is_running:
            self._server.start()

        client = ComfyApiClient(self._server.base_url)
        self._client = client
        await client.wait_until_ready()

        job.status = JobState.QUEUED
        _notify(on_progress, JobProgress(state=JobState.QUEUED, message="生成キューに追加しています..."))

        w = width or self._settings.width
        h = height or self._settings.height
        if self._settings.seed_mode == "fixed":
            seed = self._settings.seed_value
        else:
            seed = random.randint(0, 2**32 - 1)

        naming_pattern = self._settings.naming_pattern

        ctx = WorkflowPatchContext(
            job_id=job.job_id,
            positive_prompt=positive_prompt,
            negative_prompt=negative_prompt,
            output_prefix=make_output_prefix(job.job_id, seed=seed, pattern=naming_pattern),
            seed=seed,
            width=w,
            height=h,
        )
        patched = patch_workflow(self._template, ctx)
        job.seed = seed

        with (job_dir / "workflow.json").open("w", encoding="utf-8") as f:
            json.dump(patched, f, ensure_ascii=False, indent=2)

        # WebSocket先接続→prompt投入→監視（レースコンディション防止）
        try:
            async with VramMonitor() as vram, RamMonitor(total_gb=self._ram_total_gb) as ram:
                tracker = ProgressTracker(
                    on_progress=on_progress,
                    node_labels=build_node_labels(self._template),
                )
                async for event in client.submit_and_watch(patched, timeout_sec=1800.0):
                    if event.event_type in ("execution_start", "execution_cached"):
                        job.prompt_id = event.prompt_id
                    tracker.handle_event(event)
        except TimeoutError as e:
            logger.error("生成タイムアウト: %s", e)
            job.status = JobState.FAILED
            job.error = str(e)
            raise GenerationError(str(e))
        except GenerationError:
            raise
        except Exception as e:
            logger.error("生成監視中にエラー: %s", e)
            job.status = JobState.FAILED
            job.error = str(e)
            raise GenerationError(f"生成中にエラーが発生しました: {e}")

        prompt_id = job.prompt_id or ""
        if not prompt_id:
            raise GenerationError("prompt_idが取得できませんでした")

        job.status = JobState.RESOLVING_OUTPUT
        history = await client.get_history(prompt_id)
        with (job_dir / "history.json").open("w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)

        output_image = resolve_output_image(
            history,
            prompt_id,
            self._paths.comfyui_output_dir,
            job.job_id,
        )

        ext = output_image.suffix
        output_basename = output_image.stem
        final_output = job_dir / f"{output_basename}{ext}"
        shutil.copy2(output_image, final_output)
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
            ram_peak_gb=ram.peak_used_gb,
            ram_avg_gb=ram.avg_used_gb,
            ram_total_gb=self._ram_total_gb,
        )

        with (job_dir / "job.json").open("w", encoding="utf-8") as f:
            json.dump(job.to_dict(), f, ensure_ascii=False, indent=2)

        logger.info(
            "Job完了: %s → %s (%.0fs, VRAMピーク=%.1fGB)",
            job.job_id, final_output, elapsed, vram.peak_gb,
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
