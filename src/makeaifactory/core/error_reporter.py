"""エラー報告(diagnostics.DiagnosticPayload)をDiscord Webhookへ送信する。

Webhook URLは投稿専用権限のみを持つため、漏洩しても被害はスパム程度に
限定される。実際のURLは constants.ERROR_REPORT_WEBHOOK_URL に
ビルド時(release.yml)で注入される。
"""
from __future__ import annotations

import json
import logging
import os

import httpx

from ..constants import ERROR_REPORT_WEBHOOK_URL
from .diagnostics import DiagnosticPayload

logger = logging.getLogger(__name__)

_TIMEOUT = 15.0


def _webhook_url() -> str:
    # MAF_ERROR_REPORT_WEBHOOK_URL は検証用のローカル上書き。
    # ビルド本番ではconstants.ERROR_REPORT_WEBHOOK_URLのみが使われる。
    return os.environ.get("MAF_ERROR_REPORT_WEBHOOK_URL") or ERROR_REPORT_WEBHOOK_URL


def report_enabled() -> bool:
    return bool(_webhook_url())


def _build_embed(payload: DiagnosticPayload) -> dict:
    system = payload.system
    fields = [
        {"name": "app_version", "value": payload.app_version or "-", "inline": True},
        {"name": "runtime_version", "value": payload.runtime_version or "-", "inline": True},
        {"name": "report_id", "value": payload.report_id, "inline": True},
        {"name": "OS", "value": f"{system.get('os_name', '-')} {system.get('os_version', '')}".strip(), "inline": True},
        {
            "name": "GPU",
            "value": f"{system.get('gpu_name') or '-'} ({system.get('vram_gb', 0):.1f}GB)",
            "inline": True,
        },
        {"name": "vram_mode / model_preset", "value": f"{payload.vram_mode} / {payload.model_preset}", "inline": True},
        {"name": "RuntimeState", "value": payload.runtime_state or "-", "inline": True},
        {"name": "メッセージ", "value": (payload.message or "-")[:1000], "inline": False},
    ]
    if payload.user_comment:
        fields.append({"name": "ユーザーコメント", "value": payload.user_comment[:1000], "inline": False})

    return {
        "title": f"[エラー報告] {payload.title}"[:256],
        "color": 0xE53935,
        "fields": fields,
        "timestamp": payload.timestamp,
    }


async def send_error_report(payload: DiagnosticPayload) -> tuple[bool, str]:
    """Discord Webhookへ報告を送信する。(成功フラグ, ユーザー向けメッセージ)を返す。"""
    if not report_enabled():
        return False, "報告先が設定されていません"

    embed = _build_embed(payload)
    data = {"payload_json": json.dumps({"embeds": [embed]}, ensure_ascii=False)}
    files = {
        "files[0]": (
            f"{payload.report_id}_log.txt",
            (payload.log_excerpt or "(ログなし)").encode("utf-8"),
            "text/plain",
        )
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(_webhook_url(), data=data, files=files)
        if resp.status_code in (200, 204):
            logger.info("エラー報告を送信しました: %s", payload.report_id)
            return True, "送信しました。ご協力ありがとうございます。"
        logger.warning("エラー報告の送信に失敗しました: %d %s", resp.status_code, resp.text[:200])
        return False, f"送信に失敗しました (HTTP {resp.status_code})"
    except Exception as e:
        logger.warning("エラー報告の送信中に例外: %s", e)
        return False, f"送信に失敗しました: {e}"
