from __future__ import annotations

import json
import logging
import random
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from ..comfy.api_client import ComfyApiClient
from ..comfy.progress_tracker import ProgressTracker, build_node_labels, count_progress_stages
from ..comfy.workflow_patcher import DevModeOverrides
from ..comfy.server_controller import ComfyServerController
from ..core.atomic_json import write_json_atomic
from ..core.generation_executor import GenerationExecutor, GenerationRequest
from ..core.paths import AppPaths
from ..core.settings_store import SettingsStore
from ..core.vram_monitor import RamMonitor, VramMonitor
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
        executor: GenerationExecutor,
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
        # 現在実行中(直近に開始した)ジョブのowner。cancel_current()がexecutor.
        # request_cancel(owner, job_id)で照合するために保持する。
        self._current_owner: str = "desktop"
        # SCH-01 PR4: gate取得(submit)〜release、request_cancelによる照合キャンセルまで
        # 一括で担う共有executor。app.py/AppControllerから配布される単一インスタンス。
        self._executor = executor

    def reload_template(self) -> None:
        """workflow templateをディスクから再読み込みする。

        開発モードでworkflow JSONを直接編集・保存した直後に呼び、
        以降の生成 (本体/Discord/インターネット投入口いずれも) へ反映させる。
        """
        with self._paths.runtime_template_json().open("r", encoding="utf-8") as f:
            self._template = json.load(f)

    async def run_job(
        self,
        input_image: Path,
        on_progress: ProgressCallback | None = None,
        dev_overrides: DevModeOverrides | None = None,
        owner: str = "desktop",
        on_wait: Callable[[], None] | None = None,
    ) -> tuple[Path, BenchmarkResult]:
        job = Job()
        self._current_job = job
        self._current_owner = owner
        logger.info("Job開始: %s (owner=%s)", job.job_id, owner)
        start_time = time.monotonic()

        job_dir = self._paths.job_dir(job.job_id, job.date_str)
        job_dir.mkdir(parents=True, exist_ok=True)

        # 入力画像をジョブディレクトリへコピー (アーカイブ用。ComfyUIへの
        # アップロード用コピーはGenerationExecutorが別名 (makeaifactory_<job_id>) で行う)
        input_copy = job_dir / ("input" + input_image.suffix)
        shutil.copy2(input_image, input_copy)
        job.input_path = str(input_copy)

        # ComfyUI起動確認
        if not self._server.is_running:
            self._server.start()

        client = ComfyApiClient(self._server.base_url)

        # シード決定 (dev mode: オーバーライドが持つ。通常: 設定次第でランダム)
        if dev_overrides is not None:
            seed = dev_overrides.seed
        else:
            seed = random.randint(0, 2**32 - 1) if self._settings.seed_randomize else None
        job.seed = seed

        from ..constants import MODEL_PRESETS
        preset_def = MODEL_PRESETS.get(self._settings.model_preset, MODEL_PRESETS["normal"])

        req = GenerationRequest(
            owner=owner,
            job_id=job.job_id,
            input_image=input_image,
            job_dir=job_dir,
            template=self._template,
            seed=seed,
            unet_high_name=preset_def["unet_high"],
            unet_low_name=preset_def["unet_low"],
            sage_attention_mode=(
                self._sage_attention_mode
                if self._sage_attention_available and self._settings.sage_attention_enabled
                else "disabled"
            ),
            upload_basename=f"makeaifactory_{job.job_id}{input_image.suffix}",
            dev_overrides=dev_overrides,
            save_workflow_json=True,
        )

        def _save_history(h: dict) -> None:
            # DAT-01: job_dir はジョブごとの専用ディレクトリ (outputs配下、
            # ユーザーが直接開く場所) で history.json は1回しか書かないため、
            # ".bak" が無駄に増えないよう make_backup=False にする。
            write_json_atomic(job_dir / "history.json", h, ensure_ascii=False, indent=2, make_backup=False)

        def _on_stage(stage: str) -> None:
            # "connecting"/"generating" は既存実装でも追加のJobProgress通知が
            # 無かった段階のためここでは何もしない
            # (generatingの進捗はProgressTracker.handle_eventがon_event経由で担う)
            if stage == "uploading":
                job.status = JobState.UPLOADING
                _notify(on_progress, JobProgress(state=JobState.UPLOADING, message="画像をアップロードしています..."))
            elif stage == "queueing":
                job.status = JobState.QUEUED
                _notify(on_progress, JobProgress(state=JobState.QUEUED, message="生成キューに追加しています..."))
            elif stage == "resolving":
                job.status = JobState.RESOLVING_OUTPUT
                _notify(on_progress, JobProgress(state=JobState.RESOLVING_OUTPUT, message="動画を取得しています..."))

        # VRAM・RAM を計測しながら生成を実行する。従来はwatch_progressループのみを
        # 計測していたが、SCH-01 PR3の統合によりexecutor.run()全体
        # (接続確認〜アップロード〜監視〜出力解決) を計測窓とする
        # (計測窓がアップロード/出力解決分だけ広がるのは計画上の既知差分として許容する)。
        # SCH-01 PR4: run()をsubmit()に置き換えたことで、gate取得待ち
        # (=他経路の生成完了待ち) が発生した場合はその待機時間も計測窓・elapsed_sec
        # に含まれるようになる (待たされない通常時は従来と同じ)。これも既知差分として許容する。
        async with VramMonitor() as vram, RamMonitor(total_gb=self._ram_total_gb) as ram:
            tracker = ProgressTracker(
                on_progress=on_progress,
                node_labels=build_node_labels(self._template),
                stage_count=count_progress_stages(self._template),
            )
            try:
                result = await self._executor.submit(
                    req, client,
                    on_stage=_on_stage,
                    on_event=tracker.handle_event,
                    cancel_check=lambda: job.status == JobState.CANCELLED,
                    on_wait=on_wait,
                )
            except OutputNotFoundError as e:
                # 既存挙動を維持: 失敗時もhistory.jsonを保存してからraiseする
                _save_history(getattr(e, "history", {}))
                raise

        job.uploaded_image_name = result.uploaded_image_name
        job.prompt_id = result.prompt_id
        _save_history(result.history)

        final_output = result.output_path
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

        # DAT-01: history.json と同じ理由でmake_backup=False (per-jobファイル)。
        write_json_atomic(job_dir / "job.json", job.to_dict(), ensure_ascii=False, indent=2, make_backup=False)

        logger.info(
            "Job完了: %s → %s (%.0fs, VRAMピーク=%.1fGB, モード=%s)",
            job.job_id, final_output, elapsed, vram.peak_gb, self._settings.vram_mode,
        )
        self._log_benchmark(bench, input_image.name)
        _notify(on_progress, JobProgress(state=JobState.COMPLETED, message="完成！"))
        return final_output, bench

    def _log_benchmark(self, bench: BenchmarkResult, image_name: str) -> None:
        """生成結果をログと benchmark.csv に記録する。"""
        import csv
        from datetime import datetime
        from ..constants import VRAM_MODE_LABELS

        now = datetime.now()
        timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
        mode_label = VRAM_MODE_LABELS.get(bench.vram_mode, bench.vram_mode)
        mins = int(bench.elapsed_sec // 60)
        secs_rem = int(bench.elapsed_sec % 60)
        time_str = f"{mins}分{secs_rem}秒 ({bench.elapsed_sec:.1f}秒)"
        vram_pct = (bench.vram_peak_gb / bench.vram_total_gb * 100) if bench.vram_total_gb > 0 else 0.0
        ram_pct  = (bench.ram_peak_gb  / bench.ram_total_gb  * 100) if bench.ram_total_gb  > 0 else 0.0

        # ── 人間向けログ (メインログファイルに記録) ─────────────────────
        sep = "=" * 60
        lines = [
            sep,
            "  [BENCHMARK] 生成ベンチマーク結果",
            f"  日時      : {timestamp}",
            f"  GPU       : {bench.gpu_name or '不明'} ({bench.vram_total_gb:.1f} GB VRAM)",
            f"  VRAMモード : {mode_label} ({bench.vram_mode})",
            f"  入力画像   : {image_name}",
            f"  生成時間   : {time_str}",
        ]
        if bench.vram_available:
            lines.append(
                f"  VRAM使用量 : ピーク {bench.vram_peak_gb:.1f} GB / "
                f"平均 {bench.vram_avg_gb:.1f} GB / "
                f"搭載 {bench.vram_total_gb:.1f} GB "
                f"(ピーク {vram_pct:.1f}%)"
            )
        else:
            lines.append("  VRAM使用量 : 計測不可 (nvidia-smi が見つかりません)")
        if bench.ram_available:
            lines.append(
                f"  RAM使用量  : ピーク {bench.ram_peak_gb:.1f} GB / "
                f"平均 {bench.ram_avg_gb:.1f} GB / "
                f"搭載 {bench.ram_total_gb:.1f} GB "
                f"(ピーク {ram_pct:.1f}%)"
            )
        else:
            lines.append("  RAM使用量  : 計測不可")
        lines.append(sep)
        for line in lines:
            logger.info(line)

        # ── CSV (benchmark.csv) に追記 ────────────────────────────────
        csv_path = self._paths.logs_dir / "benchmark.csv"
        write_header = not csv_path.exists()
        try:
            csv_path.parent.mkdir(parents=True, exist_ok=True)
            with csv_path.open("a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                if write_header:
                    w.writerow([
                        "timestamp", "gpu_name", "vram_total_gb",
                        "vram_mode", "image_name",
                        "elapsed_sec", "elapsed_min",
                        "vram_peak_gb", "vram_avg_gb", "vram_peak_pct",
                        "ram_total_gb", "ram_peak_gb", "ram_avg_gb", "ram_peak_pct",
                    ])
                w.writerow([
                    timestamp,
                    bench.gpu_name,
                    f"{bench.vram_total_gb:.1f}",
                    bench.vram_mode,
                    image_name,
                    f"{bench.elapsed_sec:.1f}",
                    f"{bench.elapsed_sec / 60:.2f}",
                    f"{bench.vram_peak_gb:.1f}" if bench.vram_available else "",
                    f"{bench.vram_avg_gb:.1f}"  if bench.vram_available else "",
                    f"{vram_pct:.1f}"           if bench.vram_available else "",
                    f"{bench.ram_total_gb:.1f}",
                    f"{bench.ram_peak_gb:.1f}"  if bench.ram_available else "",
                    f"{bench.ram_avg_gb:.1f}"   if bench.ram_available else "",
                    f"{ram_pct:.1f}"            if bench.ram_available else "",
                ])
            logger.info("ベンチマークCSV更新: %s", csv_path)
        except Exception as exc:
            logger.warning("ベンチマークCSV書き込み失敗: %s", exc)

    async def cancel_current(self) -> None:
        """現在実行中のジョブをキャンセルする。

        SCH-01 PR4: ComfyUIへの `/interrupt` はグローバルに効く (実行中の
        ジョブなら誰のものでも止めてしまう) ため、無条件発行はやめ、
        executor.request_cancel(owner, job_id) による registry 照合
        (自分のジョブが実際に実行中の時だけ発行) へ置き換える。
        job.status=CANCELLED は submit()/run() のcancel_check
        (取得待ち中・watch後いずれも) が拾うため、request_cancelが不一致で
        interruptを発行しなかった場合でも、次にgateが空いた時点でこのジョブは
        キャンセル済みとして扱われる。
        """
        if self._current_job:
            self._current_job.status = JobState.CANCELLED
            self._executor.request_cancel(self._current_owner, self._current_job.job_id)


def _notify(cb: ProgressCallback | None, progress: JobProgress) -> None:
    if cb:
        cb(progress)
