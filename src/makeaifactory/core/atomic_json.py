from __future__ import annotations

"""JSONファイルの原子的な書き込み・安全な読み込みを提供するユーティリティ。

settings / RuntimeState / bot_state など、アプリのJSON永続化は従来
「開いて上書き」する実装だったため、書き込み中に強制終了されたり、
複数プロセスから同時に更新されたりすると、空ファイルや途中までしか
書かれていない壊れたJSONが残るリスクがあった。

本モジュールは以下の2つの関数でこれを緩和する。

- write_json_atomic: 同一ディレクトリの一時ファイルへ書き切ってから
  os.replace() で置き換える。os.replace() はPOSIX/Windowsいずれでも
  同一ボリューム上であれば原子的な置換になるため、書き込み途中の
  状態が本体ファイルに現れることはない。
- read_json_or_default: 読み込みに失敗した場合、壊れたファイルを
  ".corrupt" へ退避してから既定値を返す。次回起動時に同じ壊れた
  ファイルを読んでクラッシュを繰り返すことを防ぐ。

標準ライブラリのみに依存する。
"""

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)


def write_json_atomic(
    path: Path,
    data: Any,
    *,
    indent: int = 2,
    ensure_ascii: bool = False,
    make_backup: bool = True,
) -> None:
    """JSONデータを原子的にファイルへ書き込む。

    同一ディレクトリ内に一時ファイルを作成して書き込み、fsyncで
    ディスクへ確実に反映させてから os.replace() で本体ファイルへ
    置き換える。途中でプロセスが落ちても、本体ファイルは「更新前の
    内容のまま」か「更新後の内容のまま」のどちらかにしかならない。

    Args:
        path: 書き込み先のパス。
        data: json.dump に渡せるオブジェクト。
        indent: インデント幅。
        ensure_ascii: json.dump の ensure_ascii。
        make_backup: True の場合、既存ファイルを上書きする前に
            ".bak" へベストエフォートでコピーする（失敗しても無視）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    tmp_path = path.with_suffix(path.suffix + f".{uuid4().hex}.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent, ensure_ascii=ensure_ascii)
            f.flush()
            os.fsync(f.fileno())

        if make_backup and path.exists():
            backup_path = path.with_suffix(path.suffix + ".bak")
            try:
                shutil.copy2(path, backup_path)
            except Exception as e:
                # バックアップ失敗は致命的ではないため握りつぶす
                logger.debug("バックアップ作成に失敗しました: %s", e)

        # 同一ディレクトリ内での置換のため原子的に確定する
        os.replace(tmp_path, path)
    except Exception:
        # 失敗時は一時ファイルを後始末してから再送出
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def read_json_or_default(path: Path, default: Any) -> Any:
    """JSONファイルを読み込む。存在しない/壊れている場合は既定値を返す。

    ファイルが存在しない場合はそのまま default を返す。存在するが
    JSONとして不正な場合は、原因調査のためにファイルを ".corrupt" へ
    退避（隔離）してから default を返す。これにより、次回以降も同じ
    壊れたファイルを読み込んで同じ失敗を繰り返すことを防ぐ。

    Args:
        path: 読み込み対象のパス。
        default: 読み込めない場合に返す既定値。

    Returns:
        読み込んだJSONの値、または default。
    """
    if not path.exists():
        return default

    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("JSONファイルが壊れています。隔離して既定値を使用します: %s (%s)", path, e)
        corrupt_path = path.with_suffix(path.suffix + ".corrupt")
        try:
            shutil.move(str(path), str(corrupt_path))
        except Exception as move_err:
            logger.debug("壊れたファイルの隔離に失敗しました: %s", move_err)
        return default
