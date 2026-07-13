"""zip 展開時の zip-slip (パストラバーサル) を防止する共通ユーティリティ。

`zipfile.ZipFile.extractall()` は、メンバ名に `../` を含むものや絶対パス
(`/etc/passwd` や `C:\\Windows\\...` 等) が含まれていても、Pythonバージョンに
よって黙って正規化してしまったり、環境によっては展開先ディレクトリの外側へ
書き込んでしまう可能性がある(いわゆる zip-slip 脆弱性)。

本モジュールはメンバごとの展開先パスを **展開前に全件検証** し、1件でも
`dest_dir` の外を指すメンバがあれば `BadZipMemberError` を送出して一切
展開しない、安全な展開関数 `safe_extract_zip` を提供する。

`comfy_installer.py` / `custom_node_installer.py` のように、GitHub 配布 zip
特有のトップレベルディレクトリを剥がしながら展開する箇所では、内部で使う
パス検証ロジック `resolve_safe_member_path` を直接 import して同じ安全性を
共有する。
"""
from __future__ import annotations

import logging
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)


class BadZipMemberError(Exception):
    """zip 内のメンバが展開先ディレクトリの外を指している場合に送出される例外。

    メンバ名 (zip内の相対パス文字列) を引数に取る。
    """


def resolve_safe_member_path(dest_dir: Path, member_name: str) -> Path:
    """zip メンバ名 `member_name` の展開先パスを解決し、安全性を検証する。

    `dest_dir` 配下 (dest_dir 自身を含む) に収まる場合のみ、解決済み
    (`resolve()` 済み) の展開先パスを返す。絶対パス・`..` による親ディレクトリ
    脱出・別ドライブへのパス指定など、`dest_dir` の外へ出るメンバの場合は
    `BadZipMemberError` を送出する。

    `dest_dir` は事前に存在している必要はない(存在しなくても resolve 自体は
    可能)が、呼び出し側で `mkdir(parents=True, exist_ok=True)` 済みであることを
    前提とする。
    """
    root = dest_dir.resolve()
    target = (dest_dir / member_name).resolve()
    if target != root and not target.is_relative_to(root):
        raise BadZipMemberError(member_name)
    return target


def safe_extract_zip(zip_path: Path, dest_dir: Path) -> None:
    """zip を `dest_dir` へ安全に展開する。

    各メンバの展開先が `dest_dir` 配下に収まることを確認し、外へ出る
    (絶対パス/`..`/ドライブ跨ぎ)メンバが1件でもあれば `BadZipMemberError` を
    送出して一切展開しない(all-or-nothing)。

    検証は `zipfile.ZipFile.extractall()` を呼び出す前に `namelist()` 全件に
    対して行うため、悪意あるメンバが含まれるzipは展開開始前に確実に拒否される。
    """
    dest_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path) as zf:
        # 展開前に全メンバを検証する。1件でも違反があれば例外を送出し、
        # extractall() 自体を呼ばない(ディスクへは何も書き込まれない)。
        for name in zf.namelist():
            resolve_safe_member_path(dest_dir, name)

        try:
            zf.extractall(dest_dir)
        except Exception:
            logger.warning("zip展開中にエラーが発生しました: %s -> %s", zip_path, dest_dir)
            raise
