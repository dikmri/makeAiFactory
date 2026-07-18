"""SCH-01: 生成実行の共通部品。

Desktop (`core/job_controller.py`) / Discord (`core/discord_bot_controller.py`) /
Remote Room (`remote_room/controller.py`) の3経路に逐語コピペされていた
「history取得リトライ」「テンプレ選択fallback」ロジックをここへ集約する。

PR2 では純粋なヘルパー関数のみを提供する (挙動は既存3箇所と完全に同一)。
PR3 で GenerationExecutor 本体 (3経路の生成フロー自体の統合) を追加予定。
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from ..comfy.output_resolver import resolve_output_mp4
from ..domain.errors import OutputNotFoundError

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
