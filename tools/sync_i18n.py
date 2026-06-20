"""tr()でラップされた日本語文字列をソースコードから抽出し、未翻訳分を
機械翻訳 (deep-translator) で自動生成して app/i18n/{en,zh,ko}.json に追記する。

新しい画面・項目名を追加したとき (tr("...") で日本語原文をラップするだけで)
このスクリプトを実行すれば多言語ファイルが自動的に追従する。

使い方:
    uv run --group dev python tools/sync_i18n.py
"""
from __future__ import annotations

import ast
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
SRC_DIR = ROOT / "src" / "makeaifactory"
I18N_DIR = ROOT / "app" / "i18n"

# deep-translator (GoogleTranslator) のターゲット言語コード
TARGET_CODES = {
    "en": "en",
    "zh": "zh-CN",
    "ko": "ko",
}


def extract_strings() -> set[str]:
    """tr("...") 呼び出しの文字列リテラル第一引数をASTで抽出する。"""
    strings: set[str] = set()
    for path in SRC_DIR.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as e:
            print(f"skip (syntax error): {path}: {e}", file=sys.stderr)
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "tr"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and node.args[0].value.strip()
            ):
                strings.add(node.args[0].value)
    return strings


def load_table(lang: str) -> dict[str, str]:
    path = I18N_DIR / f"{lang}.json"
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_table(lang: str, table: dict[str, str]) -> None:
    I18N_DIR.mkdir(parents=True, exist_ok=True)
    path = I18N_DIR / f"{lang}.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(table, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def main() -> None:
    from deep_translator import GoogleTranslator

    strings = extract_strings()
    print(f"{len(strings)} 件の翻訳対象文字列を検出しました")

    for lang, target_code in TARGET_CODES.items():
        table = load_table(lang)

        stale = [k for k in table if k not in strings]
        for k in stale:
            del table[k]
        if stale:
            print(f"[{lang}] 未使用エントリ {len(stale)} 件を削除")

        missing = sorted(s for s in strings if s not in table)
        if not missing:
            print(f"[{lang}] 新規翻訳なし")
            save_table(lang, table)
            continue

        translator = GoogleTranslator(source="ja", target=target_code)
        translated = 0
        for s in missing:
            try:
                table[s] = translator.translate(s)
                translated += 1
            except Exception as e:
                print(f"[{lang}] 翻訳失敗 (スキップ): {e}", file=sys.stderr)
            time.sleep(0.1)  # 無料エンドポイントへの過剰アクセスを避ける

        print(f"[{lang}] 新規翻訳 {translated}/{len(missing)} 件を追加")
        save_table(lang, table)

    print("完了")


if __name__ == "__main__":
    main()
