import os
import sys
from pathlib import Path


def _check_update_applied() -> None:
    """アップデート適用済みマーカーを検出して環境変数にセットする。

    PS1 のすべての起動方法が失敗した場合のフォールバック。
    マーカーが見つかったら削除し、セットアップ完了後に通知を表示させる。
    """
    if not getattr(sys, "frozen", False):
        return
    marker = Path(sys.executable).parent / "_update_applied.txt"
    if marker.exists():
        try:
            marker.unlink(missing_ok=True)
        except OSError:
            pass
        os.environ["MAF_UPDATE_APPLIED"] = "1"


_check_update_applied()

from makeaifactory.app import run_app  # noqa: E402

if __name__ == "__main__":
    sys.exit(run_app())
