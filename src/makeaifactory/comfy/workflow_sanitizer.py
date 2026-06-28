from __future__ import annotations

import copy
import json
import logging
from pathlib import Path

from ..constants import (
    BASE_VIDEO_NODE_ID,
    LOADIMAGE_NODE_ID,
    OUTPUT_VIDEO_NODE_ID,
    RESOLUTION_PICKER_NODE_ID,
)

logger = logging.getLogger(__name__)

_DANGEROUS_CLASS_TYPES = {
    "Post Request Node",
    "HTMLRendererNode",
    "easy loadImagesForLoop",
    "easy imagesCountInDirectory",
    "GoogleTranslateTextNode",
}


def _is_node_ref(value) -> bool:
    return isinstance(value, list) and len(value) == 2 and isinstance(value[0], str)


# 除去対象だが、出力を参照しているノードへ「中身の文字列」をインライン展開してから
# 取り除くべきノード。これをしないと、参照元 (例: Positive Prompt の node 48) が
# 削除済みノードを指したまま残り、生成時に参照切れエラーになる。
_INLINE_TEXT_CLASS_TYPES = {
    # 日本語/多言語プロンプトを翻訳して下流へ渡すノード。翻訳はオフラインで再現できない
    # ため、翻訳前の原文 (text 入力) をそのまま文字列として埋め込む。Wan2.2 の umt5
    # テキストエンコーダは多言語対応のため、原文のままでも生成は機能する。
    "GoogleTranslateTextNode",
}


def _inline_removed_text_nodes(workflow: dict) -> None:
    """_INLINE_TEXT_CLASS_TYPES のノードを、参照しているノードの入力へ
    文字列としてインライン展開する (workflow を破壊的に更新)。

    展開後、元ノードは未参照になり依存グラフ探索で自然に取り除かれる。
    text 入力が文字列でない (別ノードへの参照など) 場合は展開できないため
    そのまま残し、警告ログのみ出す。
    """
    for node_id, node in list(workflow.items()):
        if node.get("class_type") not in _INLINE_TEXT_CLASS_TYPES:
            continue
        text = node.get("inputs", {}).get("text")
        if not isinstance(text, str):
            logger.warning(
                "インライン展開不可 (text が文字列でない): node=%s class=%s",
                node_id, node.get("class_type"),
            )
            continue
        replaced = 0
        for other in workflow.values():
            inputs = other.get("inputs", {})
            for key, value in list(inputs.items()):
                if _is_node_ref(value) and value[0] == node_id:
                    inputs[key] = text
                    replaced += 1
        if replaced:
            logger.info(
                "インライン展開: node %s (%s) の原文を %d 箇所へ埋め込み",
                node_id, node.get("class_type"), replaced,
            )


def _collect_dependencies(workflow: dict, root_node_ids: list[str]) -> set[str]:
    visited: set[str] = set()
    stack = list(root_node_ids)

    while stack:
        node_id = stack.pop()
        if node_id in visited or node_id not in workflow:
            continue
        visited.add(node_id)
        node = workflow[node_id]

        for value in node.get("inputs", {}).values():
            if _is_node_ref(value):
                stack.append(str(value[0]))
            elif isinstance(value, dict):
                for nested in value.values():
                    if _is_node_ref(nested):
                        stack.append(str(nested[0]))

    return visited


def sanitize_workflow(source: dict) -> dict:
    """
    API版workflowを読み込み、makeAiFactory用のruntime templateを生成する。

    変更内容:
    - 291.inputs.image を ["189", 0] に変更 (フォルダswitchを除去)
    - 188.inputs.filename_prefix を "__OUTPUT_PREFIX__" に変更
    - 129.inputs.save_output を False に変更
    - 188から逆探索し、到達不可ノードを除去
    - 危険ノードを強制除去
    """
    workflow = copy.deepcopy(source)

    # 翻訳ノード等を文字列としてインライン展開してから依存探索する。
    # (先に除去すると Positive Prompt 等が参照切れになるため、必ずパッチより前に行う)
    _inline_removed_text_nodes(workflow)

    # 必須パッチ
    if RESOLUTION_PICKER_NODE_ID in workflow:
        workflow[RESOLUTION_PICKER_NODE_ID]["inputs"]["image"] = [LOADIMAGE_NODE_ID, 0]
        logger.info("パッチ: %s.inputs.image → [%s, 0]", RESOLUTION_PICKER_NODE_ID, LOADIMAGE_NODE_ID)

    if OUTPUT_VIDEO_NODE_ID in workflow:
        workflow[OUTPUT_VIDEO_NODE_ID]["inputs"]["filename_prefix"] = "__OUTPUT_PREFIX__"
        logger.info("パッチ: %s.inputs.filename_prefix → __OUTPUT_PREFIX__", OUTPUT_VIDEO_NODE_ID)

    if BASE_VIDEO_NODE_ID in workflow:
        workflow[BASE_VIDEO_NODE_ID]["inputs"]["save_output"] = False
        logger.info("パッチ: %s.inputs.save_output → False", BASE_VIDEO_NODE_ID)

    # 188 から依存グラフを逆探索
    keep = _collect_dependencies(workflow, [OUTPUT_VIDEO_NODE_ID])
    logger.info("依存グラフ探索完了: %d ノードを保持", len(keep))

    # 到達可能ノードのみ残す
    sanitized = {
        node_id: node
        for node_id, node in workflow.items()
        if node_id in keep
    }

    # 危険ノードを強制除去
    removed_dangerous = []
    for node_id, node in list(sanitized.items()):
        cls = node.get("class_type", "")
        if cls in _DANGEROUS_CLASS_TYPES:
            del sanitized[node_id]
            removed_dangerous.append(f"{node_id} ({cls})")

    if removed_dangerous:
        logger.info("危険ノード除去: %s", ", ".join(removed_dangerous))

    logger.info("サニタイズ完了: %d ノード (元: %d ノード)", len(sanitized), len(source))
    return sanitized


def generate_analysis_report(source: dict, sanitized: dict) -> str:
    removed = set(source.keys()) - set(sanitized.keys())
    class_types: dict[str, list[str]] = {}
    for node_id, node in sanitized.items():
        cls = node.get("class_type", "unknown")
        class_types.setdefault(cls, []).append(node_id)

    lines = [
        "# workflow_analysis_report",
        "",
        f"## 元ノード数: {len(source)}",
        f"## 保持ノード数: {len(sanitized)}",
        f"## 除去ノード数: {len(removed)}",
        "",
        "## 除去されたノード",
    ]
    for node_id in sorted(removed, key=lambda x: int(x)):
        node = source.get(node_id, {})
        cls = node.get("class_type", "")
        title = node.get("_meta", {}).get("title", "")
        lines.append(f"- {node_id}: {cls} ({title})")

    lines += ["", "## 保持class_type一覧"]
    for cls, ids in sorted(class_types.items()):
        lines.append(f"- {cls}: ノード {', '.join(sorted(ids, key=lambda x: int(x)))}")

    return "\n".join(lines)


def load_and_sanitize(
    source_json: Path,
    output_template: Path,
    output_report: Path | None = None,
) -> dict:
    with source_json.open("r", encoding="utf-8") as f:
        source = json.load(f)

    sanitized = sanitize_workflow(source)

    output_template.parent.mkdir(parents=True, exist_ok=True)
    with output_template.open("w", encoding="utf-8") as f:
        json.dump(sanitized, f, ensure_ascii=False, indent=2)
    logger.info("runtime template保存: %s", output_template)

    if output_report:
        report = generate_analysis_report(source, sanitized)
        with output_report.open("w", encoding="utf-8") as f:
            f.write(report)
        logger.info("analysis report保存: %s", output_report)

    return sanitized
