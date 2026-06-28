"""nvidia-smi 出力からのCUDAバージョン検出テスト。

ドライバー世代でヘッダ表記が変わる (旧: "CUDA Version", 新: "CUDA UMD Version")
ため、両方をパースできることを保証する。
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from makeaifactory.runtime.comfy_installer import (
    _parse_smi_cuda_version,
    _select_best_variant,
)

# 実際の nvidia-smi 出力ヘッダ (610.62 / Blackwell RTX 5060 Ti)
_NEW_DRIVER_OUTPUT = """\
Sat Jun 27 18:49:35 2026
+-----------------------------------------------------------------------------------------+
| NVIDIA-SMI 610.62                 KMD Version: 610.62        CUDA UMD Version: 13.3     |
+-----------------------------------------+------------------------+----------------------+
"""

_OLD_DRIVER_OUTPUT = """\
+-----------------------------------------------------------------------------+
| NVIDIA-SMI 550.54.15    Driver Version: 550.54.15    CUDA Version: 12.4      |
+-------------------------------+----------------------+----------------------+
"""


def test_parse_new_driver_format():
    assert _parse_smi_cuda_version(_NEW_DRIVER_OUTPUT) == (13, 3)


def test_parse_old_driver_format():
    assert _parse_smi_cuda_version(_OLD_DRIVER_OUTPUT) == (12, 4)


def test_parse_unrecognized_returns_none():
    assert _parse_smi_cuda_version("no cuda here") is None


def test_new_driver_selects_manifest_variant():
    """新ドライバー(CUDA 13.3)検出時、manifest優先の cu128 が選ばれる
    (Blackwell は cu128/torch2.8 で動作する)。"""
    detected = _parse_smi_cuda_version(_NEW_DRIVER_OUTPUT)
    variant, torch_ver, *_ = _select_best_variant(
        detected, "cu128", "2.8.0", "0.23.0", "2.8.0",
        "https://download.pytorch.org/whl/cu128",
    )
    assert variant == "cu128"
    assert torch_ver == "2.8.0"
