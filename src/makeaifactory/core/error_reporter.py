"""エラー報告(diagnostics.DiagnosticPayload)をDiscord Webhookへ送信する。

Webhook URLは投稿専用権限のみを持つため、漏洩しても被害はスパム程度に
限定される。ただし ERR-01 対応により、配布ビルド(EXE)には実URLを埋め込まない
方針としたため、constants.ERROR_REPORT_WEBHOOK_URL は空文字のままとなり、
配布ビルドでの自動送信は無効になる(report_enabled() が False を返す)。
ローカル検証時のみ環境変数 MAF_ERROR_REPORT_WEBHOOK_URL で上書き可能。
"""
from __future__ import annotations

import json
import logging
import os
import re

import httpx

from ..constants import ERROR_REPORT_WEBHOOK_URL
from .diagnostics import DiagnosticPayload

logger = logging.getLogger(__name__)

_TIMEOUT = 15.0

# detail(スタックトレース等)を送信に含める際の上限。超過分は末尾に
# 省略表示を付けて切り詰める(肥大化したdetailでWebhook送信が失敗/
# 悪用されるのを防ぐ)。
_MAX_DETAIL_BYTES = 16 * 1024  # 16KB

# ── 送信直前マスキング(最終防御) ────────────────────────────────────────────
# diagnostics.sanitize_text は個人パス([A-Za-z]:\\...)・ファイル名・
# Discord Botトークンらしき文字列を対象にするが、それとは別に、送信直前の
# 最終防御として「送信経路そのものに関わる」情報 ── Webhook URL自体・
# 社内共有(UNC)パス・ユーザーホームのアカウント名・資格情報付きURL ──
# を対象にマスクする。sanitize_text と役割が重なる部分もあるため併用する。
_WEBHOOK_URL_RE = re.compile(
    r"https://(?:[\w.-]*\.)?discord(?:app)?\.com/api/webhooks/\d+/[\w-]+",
    re.IGNORECASE,
)
_CRED_URL_RE = re.compile(r"(https?://)[^/\s:@]+:[^/\s:@]+@")
_UNC_PATH_RE = re.compile(r"\\\\[^\s\\]+\\[^\s\"'<>]*")
_USER_HOME_RE = re.compile(r"(?<=[Uu]sers\\)[^\\/:*?\"<>|\s]+")


def mask_sensitive(text: str) -> str:
    """Webhook URL・UNCパス・ユーザーホームのアカウント名・資格情報付きURLをマスクする。"""
    if not text:
        return text
    text = _CRED_URL_RE.sub(r"\1<CREDENTIALS_REDACTED>@", text)
    text = _WEBHOOK_URL_RE.sub("<WEBHOOK_URL_REDACTED>", text)
    text = _UNC_PATH_RE.sub("<UNC_PATH_REDACTED>", text)
    text = _USER_HOME_RE.sub("<USER_REDACTED>", text)
    return text


def _truncate_detail(text: str, max_bytes: int = _MAX_DETAIL_BYTES) -> str:
    """detail テキストを上限バイト数(既定16KB)で切り詰める。

    超過時は末尾に省略表示を付ける。マルチバイト文字の途中で切れないよう
    errors="ignore" でデコードする。
    """
    if not text:
        return text
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    truncated = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return truncated + f"\n...(省略: {max_bytes // 1024}KB超のため切り詰めました)"


def _webhook_url() -> str:
    # MAF_ERROR_REPORT_WEBHOOK_URL は検証用のローカル上書き。
    # ビルド本番ではconstants.ERROR_REPORT_WEBHOOK_URLのみが使われる
    # (ERR-01対応によりビルド時注入を廃止したため、常に空文字＝送信無効)。
    return os.environ.get("MAF_ERROR_REPORT_WEBHOOK_URL") or ERROR_REPORT_WEBHOOK_URL


def report_enabled() -> bool:
    return bool(_webhook_url())


def _build_embed(payload: DiagnosticPayload) -> dict:
    system = payload.system
    # メッセージ/ユーザーコメントは diagnostics.sanitize_text 適用済みだが、
    # 送信直前の最終防御として mask_sensitive も重ねて適用する。
    message = mask_sensitive(payload.message or "-")
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
        {"name": "メッセージ", "value": message[:1000], "inline": False},
    ]
    if payload.user_comment:
        comment = mask_sensitive(payload.user_comment)
        fields.append({"name": "ユーザーコメント", "value": comment[:1000], "inline": False})

    title = mask_sensitive(payload.title)
    return {
        "title": f"[エラー報告] {title}"[:256],
        "color": 0xE53935,
        "fields": fields,
        "timestamp": payload.timestamp,
    }


async def send_error_report(payload: DiagnosticPayload) -> tuple[bool, str]:
    """Discord Webhookへ報告を送信する。(成功フラグ, ユーザー向けメッセージ)を返す。"""
    if not report_enabled():
        # ERROR_REPORT_WEBHOOK_URL(および開発用上書き)が共に空の場合は、
        # 送信を試みず即座にスキップする(既存挙動 / ERR-01対応で常時この経路)。
        return False, "報告先が設定されていません"

    embed = _build_embed(payload)
    data = {"payload_json": json.dumps({"embeds": [embed]}, ensure_ascii=False)}

    # ログ抜粋はtail_log側で既にマスク・上限処理済みだが、送信直前にも
    # mask_sensitive を重ねて適用する。
    log_excerpt = mask_sensitive(payload.log_excerpt or "(ログなし)")
    files = {
        "files[0]": (
            f"{payload.report_id}_log.txt",
            log_excerpt.encode("utf-8"),
            "text/plain",
        )
    }
    if payload.detail:
        # detail(スタックトレース等)は長大になりうるため、Discordの埋め込み
        # フィールド(1000文字制限)には入れず、ログ抜粋と同様に添付ファイルで送る。
        detail = mask_sensitive(_truncate_detail(payload.detail))
        files["files[1]"] = (
            f"{payload.report_id}_detail.txt",
            detail.encode("utf-8"),
            "text/plain",
        )
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
