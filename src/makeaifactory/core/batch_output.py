from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from uuid import uuid4

logger = logging.getLogger(__name__)


def unique_destination(dest_dir: Path, stem: str, suffix: str = ".mp4") -> Path:
    """dest_dir 内で衝突しない保存先パスを返す。

    `dest_dir/stem+suffix` が未使用ならそれを返す。既に存在する場合は
    `stem_1`, `stem_2`, ... のように連番を振り、衝突しない最初のパスを返す。
    """
    candidate = dest_dir / f"{stem}{suffix}"
    if not candidate.exists():
        return candidate

    n = 1
    while True:
        candidate = dest_dir / f"{stem}_{n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def save_generated_output(output_src: Path, dest_dir: Path, stem: str) -> Path:
    """生成物を dest_dir へ安全に保存する。

    同一ディレクトリ内へ一時ファイルとしてコピーしてから `os.replace` で
    確定先へリネームすることで、コピー途中の失敗や電源断があっても
    dest_dir に不完全なファイルが残らないようにする(os.replace は同一
    ファイルシステム内であれば原子的)。

    1. dest_dir を作成(既存でもOK)。
    2. dest_dir 内の一時名へ shutil.copy2 でコピー。
    3. コピー結果を検証(0バイト等の失敗を弾く)。
    4. unique_destination で最終ファイル名を決め、os.replace で確定。

    失敗時は一時ファイルを後始末してから例外を re-raise する。
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    tmp = dest_dir / f".{stem}.{uuid4().hex}.part"

    try:
        shutil.copy2(str(output_src), str(tmp))

        if not tmp.exists() or tmp.stat().st_size == 0:
            raise OSError(f"生成物のコピーに失敗しました(空ファイル): {output_src} -> {tmp}")

        final = unique_destination(dest_dir, stem)
        os.replace(str(tmp), str(final))
        return final
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError as cleanup_err:
            logger.debug("一時ファイル削除に失敗しました: %s", cleanup_err)
        raise


def finalize_batch_item(input_path: Path, output_src: Path, output_dir: Path, end_dir: Path) -> Path:
    """バッチ1件分の後処理をトランザクション的に行う。

    先に成果物を output_dir へ確定保存し、それが成功した場合に限って
    入力ファイルを end_dir へ移動する。保存に失敗した場合は入力を
    移動せず例外を伝播させる(=入力は元の場所に残り、再実行可能)。

    end_dir 側も同名ファイルが既に存在し得るため、unique_destination で
    衝突を避けた移動先を選ぶ。
    """
    final = save_generated_output(output_src, output_dir, input_path.stem)

    # save_generated_output が成功した後にのみ入力を移動する。
    moved_to = unique_destination(end_dir, input_path.stem, input_path.suffix)
    shutil.move(str(input_path), str(moved_to))

    return final
