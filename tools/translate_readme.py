"""README.md を機械翻訳して README.en.md / README.zh.md / README.ko.md を生成する。

コードブロック内・テーブルの区切り行は翻訳対象から除外し、Markdown構造を保つ。
GitHub Actions が README.md の変更を検知して自動実行する (.github/workflows/translate-readme.yml)。

使い方:
    uv run --group dev python tools/translate_readme.py
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
SRC = ROOT / "README.md"

# (deep-translatorのターゲットコード, 出力ファイル名)
TARGETS = {
    "en": ("en", "README.en.md"),
    "zh": ("zh-CN", "README.zh.md"),
    "ko": ("ko", "README.ko.md"),
}

_LANG_LINKS = (
    "🌐 **Languages:** [日本語](README.md) | [English](README.en.md) | "
    "[中文](README.zh.md) | [한국어](README.ko.md)\n\n---\n\n"
)
_LANG_LINKS_PATTERN = re.compile(r"^🌐 \*\*Languages:.*?\n\n---\n\n", re.DOTALL)


def _is_translatable(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if re.fullmatch(r"[|\-:\s]+", stripped):  # テーブルの区切り行 (|---|---|)
        return False
    return True


def translate_markdown(text: str, target_code: str) -> str:
    from deep_translator import GoogleTranslator
    translator = GoogleTranslator(source="ja", target=target_code)

    lines = text.split("\n")
    out: list[str] = []
    in_code_block = False
    for line in lines:
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            out.append(line)
            continue
        if in_code_block or not _is_translatable(line):
            out.append(line)
            continue
        try:
            out.append(translator.translate(line))
        except Exception as e:
            print(f"  翻訳失敗 (原文を保持): {line[:40]}... ({e})", file=sys.stderr)
            out.append(line)
        time.sleep(0.05)
    return "\n".join(out)


def main() -> None:
    text = SRC.read_text(encoding="utf-8")
    body = _LANG_LINKS_PATTERN.sub("", text)

    # 日本語版にも言語切り替えリンクを付与する (冪等: 既存リンクは上で除去済み)
    SRC.write_text(_LANG_LINKS + body, encoding="utf-8")

    for lang, (target_code, filename) in TARGETS.items():
        print(f"[{lang}] 翻訳中...")
        translated = translate_markdown(body, target_code)
        out_path = ROOT / filename
        out_path.write_text(_LANG_LINKS + translated, encoding="utf-8")
        print(f"[{lang}] 完了 -> {filename}")

    print("完了")


if __name__ == "__main__":
    main()
