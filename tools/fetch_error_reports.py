"""開発者専用CLI。エラー報告用Discordチャンネルから報告メッセージを取得し、
devs/error_reports/<report_id>/{report.json, log_excerpt.txt} にローカル保存する。

error_reporter.py が使う「投稿専用」Webhookとは別に、メッセージ読み取りには
チャンネルの閲覧権限を持つBotトークンが必要(developer-only)。
Discord REST APIを直接叩くだけなので discord.py のゲートウェイ接続は不要。

使い方:
    devs/.env.error_reports に下記2行を書いておけば自動で読み込まれる
    (環境変数が既に設定されている場合はそちらが優先される):
        MAF_REPORT_BOT_TOKEN=...   (読み取り専用権限のみ付与したBotのトークン)
        MAF_REPORT_CHANNEL_ID=...  (報告先チャンネルID)
    uv run --group dev python tools/fetch_error_reports.py [--limit 50]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).parent.parent
OUT_DIR = ROOT / "devs" / "error_reports"
ENV_FILE = ROOT / "devs" / ".env.error_reports"
API_BASE = "https://discord.com/api/v10"

# report_id として許可する形式: UUID(ハイフンあり/なし) または
# Discordスノーフレーク(数字のみ、17〜20桁程度)。これ以外は保存しない
# (report_idはそのままローカルディレクトリ名として使うため、"../" 等の
# パストラバーサル文字列が紛れ込むのを許可リスト方式で防ぐ)。
_UUID_DASHED_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_UUID_HEX_RE = re.compile(r"^[0-9a-fA-F]{32}$")
_SNOWFLAKE_RE = re.compile(r"^[0-9]{17,20}$")

# 添付ファイル(log_excerpt等)を保存する際の先頭バイト上限。
# 報告チャンネルにサイズの大きいファイルが投稿されても、ローカル保存が
# 際限なく肥大化しないようにする。
_MAX_ATTACHMENT_BYTES = 2 * 1024 * 1024  # 2MB


def _is_valid_report_id(s: str) -> bool:
    """report_id が許可された形式(UUID または Discordスノーフレーク)かを判定する。"""
    if not isinstance(s, str) or not s:
        return False
    return bool(_UUID_DASHED_RE.match(s) or _UUID_HEX_RE.match(s) or _SNOWFLAKE_RE.match(s))


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if value and key not in os.environ:
            os.environ[key] = value


def _field_value(embed: dict, name: str) -> str:
    for f in embed.get("fields", []):
        if f.get("name") == name:
            return f.get("value", "")
    return ""


def fetch_messages(token: str, channel_id: str, limit: int) -> list[dict]:
    headers = {"Authorization": f"Bot {token}"}
    resp = httpx.get(
        f"{API_BASE}/channels/{channel_id}/messages",
        headers=headers,
        params={"limit": limit},
        timeout=15.0,
    )
    resp.raise_for_status()
    return resp.json()


def save_report(message: dict) -> str | None:
    embeds = message.get("embeds", [])
    if not embeds:
        return None
    embed = embeds[0]
    report_id = _field_value(embed, "report_id") or message["id"]

    if not _is_valid_report_id(report_id):
        print(f"不正な report_id のためスキップ: {report_id!r}", file=sys.stderr)
        return None

    report_dir = OUT_DIR / report_id

    # 二重防御: _is_valid_report_id を通過した値でも、解決後のパスが
    # OUT_DIR 直下から外れていないかを念のため確認する(パストラバーサル対策)。
    out_dir_resolved = OUT_DIR.resolve()
    if report_dir.resolve().parent != out_dir_resolved:
        print(f"report_id の解決パスが不正なためスキップ: {report_id!r}", file=sys.stderr)
        return None

    if report_dir.exists():
        return None  # 既に取得済み

    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "report.json").write_text(
        json.dumps(embed, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    for attachment in message.get("attachments", []):
        url = attachment.get("url")
        if not url:
            continue
        resp = httpx.get(url, timeout=15.0)
        resp.raise_for_status()
        # 先頭 _MAX_ATTACHMENT_BYTES バイトのみ保存し、肥大化を防ぐ。
        (report_dir / "log_excerpt.txt").write_bytes(resp.content[:_MAX_ATTACHMENT_BYTES])
        break

    return report_id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=50, help="取得するメッセージ件数 (最大100)")
    args = parser.parse_args()

    _load_env_file(ENV_FILE)

    token = os.environ.get("MAF_REPORT_BOT_TOKEN")
    channel_id = os.environ.get("MAF_REPORT_CHANNEL_ID")
    if not token or not channel_id:
        print(
            "環境変数 MAF_REPORT_BOT_TOKEN / MAF_REPORT_CHANNEL_ID を設定してください。",
            file=sys.stderr,
        )
        return 1

    messages = fetch_messages(token, channel_id, args.limit)
    saved = [report_id for m in messages if (report_id := save_report(m))]

    print(f"{len(saved)}件の新規報告を保存しました ({OUT_DIR})")
    for report_id in saved:
        print(f"  - {report_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
