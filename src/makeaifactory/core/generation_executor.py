"""SCH-01: 生成実行の共通部品。

Desktop (`core/job_controller.py`) / Discord (`core/discord_bot_controller.py`) /
Remote Room (`remote_room/controller.py`) の3経路に逐語コピペされていた
「history取得リトライ」「テンプレ選択fallback」ロジックをここへ集約する。

PR2 では純粋なヘルパー関数のみを提供していた (挙動は既存3箇所と完全に同一)。
PR3 で `GenerationExecutor` 本体 (3経路共通の「接続確認→アップロード→
workflow patch→queue投入→進捗監視→出力解決→job_dirへコピー」という生成フロー
自体の統合) を追加する。3経路 (Desktop/Discord/Remote Room) の各コントローラは
この `GenerationExecutor.run()` を呼ぶだけになり、経路固有の差分
(job_id採番規則・アップロードファイル名・進捗表示への変換・ベンチ計測等) だけを
各コントローラ側に残す。
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TYPE_CHECKING

from ..comfy.output_resolver import resolve_output_mp4
from ..comfy.workflow_patcher import (
    DevModeOverrides,
    WorkflowPatchContext,
    apply_dev_overrides,
    make_output_prefix,
    patch_workflow,
)
from ..domain.errors import JobCancelledError, OutputNotFoundError

if TYPE_CHECKING:
    from ..comfy.api_client import ComfyApiClient
    from .paths import AppPaths

logger = logging.getLogger(__name__)


async def resolve_output_with_retry(
    client: "ComfyApiClient",
    prompt_id: str,
    comfyui_output_dir: Path,
    job_id: str,
    attempts: int = 3,
    delay_sec: float = 0.3,
) -> tuple[Path, dict]:
    """historyからMP4を解決する。未反映競合に備え最大attempts回リトライする。

    prompt_idに紐づく動画がhistoryへ未反映な稀な競合に備えたリトライで、
    成功しているのに失敗扱いにしないための安全弁。history取得自体は毎回やり直す。

    戻り値は (mp4パス, 最後に取得したhistory)。
    全滅時は最後のOutputNotFoundErrorを送出する。呼び出し側が失敗時にも
    historyを保存できるよう、送出前に例外へ `history` 属性として添付する。

    resolve_output_mp4 を必ず経由する(VHS_VideoCombineの"gifs"キー対応を維持)。
    """
    history: dict = {}
    last_error: OutputNotFoundError | None = None
    for attempt in range(attempts):
        history = await client.get_history(prompt_id)
        try:
            output_mp4 = resolve_output_mp4(history, prompt_id, comfyui_output_dir, job_id)
            return output_mp4, history
        except OutputNotFoundError as e:
            last_error = e
            if attempt < attempts - 1:
                await asyncio.sleep(delay_sec)
    assert last_error is not None
    last_error.history = history  # type: ignore[attr-defined]  # 呼び出し側での保存用
    raise last_error


def load_template_for_workflow(paths: "AppPaths", workflow: str | None) -> dict:
    """workflow指定に応じてワークフローテンプレートを読み込む。

    workflow指定があれば、そのワークフロー用にサニタイズ済みのテンプレート
    (build_workflow_templates() で `<runtime_root>/remote_room/templates/<wf>.json`
    へ生成済み) を優先する。無ければ (または未生成なら) アプリでアクティブな
    runtime_template_json へフォールバックする。

    Discord Bot / Remote Room の両経路にあった逐語重複ロジックの共通化。
    (Desktopはワークフロー指定という概念自体が無く、常にアクティブテンプレートを
    使うためこの関数の対象外)
    """
    template_path = paths.runtime_template_json()
    if workflow:
        wf_path = paths.runtime_root / "remote_room" / "templates" / f"{workflow}.json"
        if wf_path.exists():
            template_path = wf_path
            logger.info("ワークフロー指定: %s (%s)", workflow, wf_path.name)
        else:
            logger.warning(
                "ワークフローテンプレート未生成: %s → 既定にフォールバック", workflow
            )
    if not template_path.exists():
        raise FileNotFoundError(f"ワークフローテンプレートが見つかりません: {template_path}")
    with template_path.open(encoding="utf-8") as f:
        return json.load(f)


# ── PR3: GenerationExecutor 本体 ───────────────────────────────────────────

# on_stage に渡ってくる段階名。呼び出し側はこの文字列で分岐し、経路ごとの
# 進捗通知 (JobProgress / Discord embed / RemoteRoom pct) へ変換する。
# "connecting"→wait_until_ready / "uploading"→画像コピー+upload_image /
# "queueing"→queue_prompt / "generating"→watch_progressループ /
# "resolving"→resolve_output_with_retry
StageCallback = Callable[[str], None]

# watch_progress が yield する ComfyProgressEvent をそのまま素通しするコールバック。
# generation_executor は comfy.progress_tracker に依存しないため、型は緩く object とする。
EventCallback = Callable[[object], None]


@dataclass
class GenerationRequest:
    """`GenerationExecutor.run()` への入力。

    経路 (Desktop/Discord/Remote Room) 固有の決定事項 (job_id採番規則・
    アップロードファイル名・シード決定ロジック・プリセット選択) はすべて
    呼び出し元が確定させた値をここへ詰める。GenerationExecutor 自身は
    これらの「どう決めるか」には一切関与しない。
    """
    owner: str                    # "desktop"|"batch"|"discord"|"remote"|"bridge"
    job_id: str                   # 採番は各入口の既存規則のまま (統一しない)
    input_image: Path              # コピー元の入力画像 (呼び出し元が保持する元ファイル)
    job_dir: Path                  # 呼び出し元がmkdir済み。output.mp4 はここへ配置する
    template: dict                 # 解決済みworkflowテンプレート (選択は呼び出し元)
    seed: int | None
    unet_high_name: str
    unet_low_name: str
    sage_attention_mode: str = "disabled"
    # ComfyUIへアップロードする際に job_dir 直下へコピーするファイル名。
    # 各経路の既存命名をそのまま再現すること
    # (Desktop: f"makeaifactory_{job_id}{suffix}" /
    #  Discord: f"discord_{job_id}{suffix}" / Remote: f"remote_{job_id}.png")。
    upload_basename: str = ""
    dev_overrides: DevModeOverrides | None = None
    save_workflow_json: bool = False       # DesktopのみTrue (patch直後・queue前にjob_dirへ保存)
    ready_timeout_sec: int | None = None   # Noneならwait_until_readyの既定値(120秒)を使う


@dataclass
class GenerationResult:
    """`GenerationExecutor.run()` の戻り値。"""
    output_path: Path   # job_dir/output.mp4 (コピー済み)
    prompt_id: str
    history: dict
    uploaded_image_name: str


class GenerationExecutor:
    """3経路 (Desktop/Discord/Remote Room) 共通の生成実行本体。

    従来 `JobController.run_job` / `DiscordBotController._generate_video` /
    `RemoteRoomController._generate_video` に逐語コピペされていた
    「wait_until_ready→入力画像コピー+upload_image→WorkflowPatchContext組立+
    patch_workflow→queue_prompt→watch_progressループ→
    resolve_output_with_retry→job_dir/output.mp4コピー」という一連の流れを
    ここへ集約する。各経路固有の振る舞い (進捗通知・ベンチ計測・キャンセル判定・
    ファイル命名) は `on_stage`/`on_event`/`cancel_check` 及び
    `GenerationRequest` の各フィールド経由で呼び出し元に委ねる。

    例外は一切変換せず伝播させる (OutputNotFoundError含む。
    resolve_output_with_retry の「例外にhistory属性を添付する」挙動もそのまま維持)。
    """

    def __init__(self, paths: "AppPaths") -> None:
        self._paths = paths

    async def run(
        self,
        req: GenerationRequest,
        client: "ComfyApiClient",
        *,
        on_stage: StageCallback | None = None,
        on_event: EventCallback | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> GenerationResult:
        def _stage(name: str) -> None:
            if on_stage is not None:
                on_stage(name)

        # 1. ComfyUI起動確認
        _stage("connecting")
        if req.ready_timeout_sec is not None:
            await client.wait_until_ready(timeout_sec=req.ready_timeout_sec)
        else:
            await client.wait_until_ready()

        # 2. 入力画像を job_dir へコピー (既存の経路別命名を再現) → アップロード
        _stage("uploading")
        upload_basename = req.upload_basename or req.input_image.name
        upload_copy = req.job_dir / upload_basename
        shutil.copy2(req.input_image, upload_copy)
        uploaded_name = await client.upload_image(upload_copy)
        logger.info("画像アップロード完了: job=%s → %s", req.job_id, uploaded_name)

        # 3. workflow patch (+ 開発モードのオーバーライド)
        ctx = WorkflowPatchContext(
            job_id=req.job_id,
            uploaded_image_name=uploaded_name,
            output_prefix=make_output_prefix(req.job_id),
            seed=req.seed,
            unet_high_name=req.unet_high_name,
            unet_low_name=req.unet_low_name,
            sage_attention_mode=req.sage_attention_mode,
        )
        patched = patch_workflow(req.template, ctx)
        if req.dev_overrides is not None:
            patched = apply_dev_overrides(patched, req.dev_overrides)

        # 4. workflow.json 保存 (Desktopのみ。queue投入前、既存の保存順序を踏襲)
        if req.save_workflow_json:
            with (req.job_dir / "workflow.json").open("w", encoding="utf-8") as f:
                json.dump(patched, f, ensure_ascii=False, indent=2)

        # 5. prompt投入
        _stage("queueing")
        prompt_id = await client.queue_prompt(patched)
        logger.info("生成キュー投入完了: job=%s prompt=%s", req.job_id, prompt_id)

        # 6. 進捗監視 (ComfyProgressEventをon_eventへ素通し)
        _stage("generating")
        async for event in client.watch_progress(prompt_id):
            if on_event is not None:
                on_event(event)

        # 7. キャンセル判定 (既存Desktopの `job.status == CANCELLED` 判定に相当する位置。
        # watch_progress完了後・出力解決前でチェックする)
        if cancel_check is not None and cancel_check():
            raise JobCancelledError("生成がキャンセルされました")

        # 8. history からのMP4解決 (未反映競合に備えたリトライ込み。PR2ヘルパー)
        _stage("resolving")
        output_mp4, history = await resolve_output_with_retry(
            client, prompt_id, self._paths.comfyui_output_dir, req.job_id,
        )

        # 9. job_dir/output.mp4 へコピーして結果を返す
        final_output = req.job_dir / "output.mp4"
        shutil.copy2(output_mp4, final_output)

        return GenerationResult(
            output_path=final_output,
            prompt_id=prompt_id,
            history=history,
            uploaded_image_name=uploaded_name,
        )
