"""エラー報告用の診断データ収集。

ユーザー環境で発生したエラーを開発者が調査できるよう、システム情報・
直近ログ・エラー内容を1つのペイロードにまとめる。個人情報(ユーザー名を
含むパス、Discord Bot トークン等)は送信前に必ずマスクする。
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..constants import APP_VERSION, RUNTIME_VERSION
from ..runtime.system_probe import SystemInfo

_WINDOWS_PATH_RE = re.compile(r"[A-Za-z]:\\[^\s\"'<>]*")
_FILENAME_RE = re.compile(r"[^\s/\\\"'<>]+\.(?:png|jpe?g|webp|mp4)\b", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[MN][A-Za-z0-9_-]{23,25}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,}")
_SENSITIVE_LINE_RE = re.compile(r"discord_token|bot_token", re.IGNORECASE)

_MAX_LOG_LINES = 300
_MAX_LOG_BYTES = 256 * 1024


def sanitize_text(text: str) -> str:
    """個人パス・ファイル名・トークンらしき文字列をマスクする。

    入力/出力画像のファイル名はユーザーのプライベートな内容を示唆する場合が
    あるため、パス全体だけでなく拡張子付きの単体ファイル名も丸ごと置き換える。
    """
    text = _WINDOWS_PATH_RE.sub("<PATH_REDACTED>", text)
    text = _FILENAME_RE.sub("<FILENAME_REDACTED>", text)
    text = _TOKEN_RE.sub("<REDACTED_TOKEN>", text)
    return text


def tail_log(log_path: Path, max_lines: int = _MAX_LOG_LINES, max_bytes: int = _MAX_LOG_BYTES) -> str:
    """app.log の末尾を取得し、機密行を除外・マスクして返す。"""
    if not log_path.exists():
        return ""
    try:
        raw = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""

    lines = [ln for ln in raw.splitlines() if not _SENSITIVE_LINE_RE.search(ln)]
    lines = lines[-max_lines:]
    excerpt = sanitize_text("\n".join(lines))

    encoded = excerpt.encode("utf-8")
    if len(encoded) > max_bytes:
        excerpt = encoded[-max_bytes:].decode("utf-8", errors="ignore")
    return excerpt


@dataclass
class DiagnosticPayload:
    report_id: str
    timestamp: str
    app_version: str
    runtime_version: str
    title: str
    message: str
    detail: str
    system: dict[str, Any]
    vram_mode: str
    model_preset: str
    sage_attention_enabled: bool
    runtime_state: str
    log_excerpt: str
    user_comment: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "timestamp": self.timestamp,
            "app_version": self.app_version,
            "runtime_version": self.runtime_version,
            "title": self.title,
            "message": self.message,
            "detail": self.detail,
            "system": self.system,
            "vram_mode": self.vram_mode,
            "model_preset": self.model_preset,
            "sage_attention_enabled": self.sage_attention_enabled,
            "runtime_state": self.runtime_state,
            "log_excerpt": self.log_excerpt,
            "user_comment": self.user_comment,
        }


def _system_info_to_dict(system_info: SystemInfo | None) -> dict[str, Any]:
    if system_info is None:
        return {}
    gpu = system_info.primary_gpu
    return {
        "os_name": system_info.os_name,
        "os_version": system_info.os_version,
        "cpu": system_info.cpu,
        "ram_gb": round(system_info.ram_gb, 1),
        "gpu_name": gpu.name if gpu else "",
        "vram_gb": round(gpu.vram_gb, 1) if gpu else 0.0,
        "gpu_driver_version": gpu.driver_version if gpu else "",
        "nvidia_smi_available": system_info.nvidia_smi_available,
        "disk_free_gb": round(system_info.disk_free_gb, 1),
    }


def build_diagnostic_payload(
    *,
    title: str,
    message: str,
    detail: str,
    system_info: SystemInfo | None,
    vram_mode: str,
    model_preset: str,
    sage_attention_enabled: bool,
    runtime_state: str,
    app_log_path: Path,
    user_comment: str = "",
) -> DiagnosticPayload:
    """エラーダイアログの内容とシステム情報から報告ペイロードを構築する。"""
    return DiagnosticPayload(
        report_id=str(uuid.uuid4()),
        timestamp=datetime.now(timezone.utc).isoformat(),
        app_version=APP_VERSION,
        runtime_version=RUNTIME_VERSION,
        title=sanitize_text(title),
        message=sanitize_text(message),
        detail=sanitize_text(detail),
        system=_system_info_to_dict(system_info),
        vram_mode=vram_mode,
        model_preset=model_preset,
        sage_attention_enabled=sage_attention_enabled,
        runtime_state=runtime_state,
        log_excerpt=tail_log(app_log_path),
        user_comment=sanitize_text(user_comment),
    )
