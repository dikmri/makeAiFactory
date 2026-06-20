from __future__ import annotations

import io
import logging
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import httpx

from ..domain.errors import SetupError
from ..i18n import tr
from .downloader import download_file
from .uv_manager import UvManager

logger = logging.getLogger(__name__)

# (最低CUDA major, minor, variant名, torch_version, torchvision_version, torchaudio_version, index_url)
_CUDA_VARIANT_TABLE: list[tuple[int, int, str, str, str, str, str]] = [
    (12, 8, "cu128", "2.8.0", "0.23.0", "2.8.0", "https://download.pytorch.org/whl/cu128"),
    (12, 4, "cu124", "2.6.0", "0.21.0", "2.6.0", "https://download.pytorch.org/whl/cu124"),
    (12, 1, "cu121", "2.5.1", "0.20.1", "2.5.1", "https://download.pytorch.org/whl/cu121"),
    (11, 8, "cu118", "2.5.1", "0.20.1", "2.5.1", "https://download.pytorch.org/whl/cu118"),
]


def _detect_driver_cuda_version() -> tuple[int, int] | None:
    """nvidia-smiからドライバーがサポートするCUDAバージョンを検出する。"""
    smi = shutil.which("nvidia-smi")
    if not smi:
        return None
    try:
        result = subprocess.run(
            [smi], capture_output=True, text=True, timeout=15, **_no_window_flags()
        )
        m = re.search(r"CUDA Version:\s*(\d+)\.(\d+)", result.stdout)
        if m:
            return int(m.group(1)), int(m.group(2))
    except Exception as e:
        logger.debug("nvidia-smi実行失敗: %s", e)
    return None


def _select_best_variant(
    detected_cuda: tuple[int, int] | None,
    preferred_variant: str,
    preferred_torch_ver: str,
    preferred_torchvision_ver: str,
    preferred_torchaudio_ver: str,
    preferred_index_url: str,
) -> tuple[str, str, str, str, str]:
    """検出されたCUDAに基づき最適な (variant, torch_ver, torchvision_ver, torchaudio_ver, index_url) を返す。

    マニフェストで指定された優先バリアントに対応するCUDA版が検出された場合はそれを使用し、
    不足している場合のみ互換性のある旧バリアントへフォールバックする。
    """
    if detected_cuda is None:
        logger.warning("nvidia-smiが見つかりません。マニフェスト指定のバリアントを使用します: %s", preferred_variant)
        return preferred_variant, preferred_torch_ver, preferred_torchvision_ver, preferred_torchaudio_ver, preferred_index_url

    # 優先バリアントが必要とするCUDAバージョンを解析 (例: "cu128" → (12, 8))
    m = re.match(r"cu(\d\d)(\d)$", preferred_variant)
    if m:
        required = (int(m.group(1)), int(m.group(2)))
        if detected_cuda >= required:
            logger.info(
                "CUDA %d.%d 検出 → マニフェスト指定 %s を使用",
                detected_cuda[0], detected_cuda[1], preferred_variant,
            )
            return preferred_variant, preferred_torch_ver, preferred_torchvision_ver, preferred_torchaudio_ver, preferred_index_url

    # フォールバック: 検出されたCUDAに対応した最高バリアントを選択
    for min_maj, min_min, variant, torch_ver, tv_ver, ta_ver, index_url in _CUDA_VARIANT_TABLE:
        if detected_cuda >= (min_maj, min_min):
            logger.info(
                "CUDA %d.%d 検出 → %s にフォールバック (優先は %s だが非対応)",
                detected_cuda[0], detected_cuda[1], variant, preferred_variant,
            )
            return variant, torch_ver, tv_ver, ta_ver, index_url

    # CUDA 11.8未満の非常に古い環境
    logger.warning("CUDA %d.%d は未サポート。cu118 にフォールバック", *detected_cuda)
    return "cu118", "2.5.1", "0.20.1", "2.5.1", "https://download.pytorch.org/whl/cu118"


def _no_window_flags() -> dict:
    if sys.platform == "win32":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        return {"creationflags": subprocess.CREATE_NO_WINDOW, "startupinfo": si}
    return {}


async def install_comfyui(
    comfyui_dir: Path,
    zip_url: str,
    commit: str,
    venv_path: Path,
    uv: UvManager,
    downloads_dir: Path,
) -> None:
    if (comfyui_dir / "main.py").exists():
        logger.info("ComfyUIは既に存在します: %s", comfyui_dir)
        return

    logger.info("ComfyUIをDLします (commit=%s)", commit[:8])
    zip_path = downloads_dir / f"comfyui-{commit[:8]}.zip"
    await download_file(zip_url, zip_path)

    logger.info("ComfyUIを展開します")
    comfyui_dir.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        members = zf.namelist()
        prefix = members[0].split("/")[0] + "/"
        for member in members:
            target = comfyui_dir / member[len(prefix):]
            if member.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, target.open("wb") as dst:
                    dst.write(src.read())

    requirements = comfyui_dir / "requirements.txt"
    if requirements.exists():
        logger.info("ComfyUI requirements をインストールします")
        uv.pip_install_requirements(venv_path, requirements)

    logger.info("ComfyUIのインストール完了")


async def install_torch(
    venv_path: Path,
    uv: UvManager,
    torch_version: str,
    torchvision_version: str,
    torchaudio_version: str,
    cuda_variant: str,
    index_url: str,
) -> None:
    # ドライバーのCUDAバージョンを自動検出し、最適なバリアントを選択
    detected_cuda = _detect_driver_cuda_version()
    final_variant, final_torch_ver, final_tv_ver, final_ta_ver, final_index_url = _select_best_variant(
        detected_cuda, cuda_variant, torch_version, torchvision_version, torchaudio_version, index_url
    )

    python = venv_path / "Scripts" / "python.exe"
    # importせずにメタデータのみ読む（破損DLLでも安全）。改行区切りで出力して厳密比較。
    check_cmd = [
        str(python), "-c",
        "from importlib.metadata import version; "
        "print(version('torch')); print(version('torchaudio'))"
    ]
    result = subprocess.run(
        check_cmd, capture_output=True, text=True, **_no_window_flags()
    )
    if result.returncode == 0:
        lines = result.stdout.strip().splitlines()
        if len(lines) >= 2:
            installed_torch = lines[0].strip()
            installed_ta = lines[1].strip()
            expected_torch = f"{final_torch_ver}+{final_variant}"
            expected_ta = f"{final_ta_ver}+{final_variant}"
            if installed_torch == expected_torch and installed_ta == expected_ta:
                logger.info(
                    "PyTorchは既にインストール済み: torch=%s torchaudio=%s",
                    installed_torch, installed_ta,
                )
                return
            logger.info(
                "PyTorchバージョン不一致: torch %s→%s, torchaudio %s→%s",
                installed_torch, expected_torch, installed_ta, expected_ta,
            )

    logger.info(
        "PyTorchをインストールします (CUDA=%s, torch=%s, torchvision=%s, torchaudio=%s)",
        final_variant, final_torch_ver, final_tv_ver, final_ta_ver,
    )
    packages = [
        f"torch=={final_torch_ver}+{final_variant}",
        f"torchvision=={final_tv_ver}+{final_variant}",
        f"torchaudio=={final_ta_ver}+{final_variant}",
    ]
    uv.pip_install(venv_path, packages, index_url=final_index_url)

    verify_cmd = [
        str(python), "-c",
        f"import torch; assert torch.cuda.is_available(), {tr('CUDA利用不可')!r}; "
        "print('CUDA OK:', torch.cuda.get_device_name(0))"
    ]
    result = subprocess.run(
        verify_cmd, capture_output=True, text=True, **_no_window_flags()
    )
    if result.returncode != 0:
        raise SetupError(
            tr("PyTorch CUDA検証失敗。\nNVIDIAドライバが古い可能性があります。\n\n{stderr}").format(
                stderr=result.stderr[-500:])
        )
    logger.info("PyTorch検証OK: %s", result.stdout.strip())


def _verify_sage_attention(python: Path) -> bool:
    verify_cmd = [
        str(python), "-c",
        "import torch, sageattention; assert torch.cuda.is_available(); print('OK')",
    ]
    result = subprocess.run(verify_cmd, capture_output=True, text=True, **_no_window_flags())
    if result.returncode != 0:
        logger.warning(
            "SageAttention import検証失敗。sage_attention=disabledへフォールバックします。\n%s",
            result.stderr[-500:],
        )
        return False
    logger.info("SageAttention検証OK")
    return True


async def install_sage_attention(
    venv_path: Path,
    uv: UvManager,
    triton_version: str,
    wheel_url: str,
    wheel_sha256: str,
    downloads_dir: Path,
) -> bool:
    """SageAttention（高速Attention実装）をインストールする。

    生成速度の最適化のみが目的のオプショナル機能のため、失敗してもSetupErrorは投げない。
    戻り値Falseの場合、呼び出し側はワークフローのsage_attentionをdisabledのままにする
    （未対応GPU/インストール失敗時でも生成自体はクラッシュさせず継続できるようにするため）。
    """
    if not triton_version or not wheel_url:
        logger.info("SageAttention未設定（マニフェストに記載なし）。スキップします。")
        return False

    python = venv_path / "Scripts" / "python.exe"
    check_cmd = [
        str(python), "-c",
        "from importlib.metadata import version; print(version('sageattention'))",
    ]
    result = subprocess.run(check_cmd, capture_output=True, text=True, **_no_window_flags())
    if result.returncode == 0:
        logger.info("SageAttentionは既にインストール済み: %s", result.stdout.strip())
        return _verify_sage_attention(python)

    try:
        logger.info("triton-windows をインストールします (%s)", triton_version)
        uv.pip_install(venv_path, [f"triton-windows=={triton_version}"])

        from urllib.parse import unquote
        wheel_filename = unquote(wheel_url.rsplit("/", 1)[-1])
        wheel_path = downloads_dir / wheel_filename
        await download_file(wheel_url, wheel_path, sha256=wheel_sha256)

        logger.info("sageattention wheel をインストールします: %s", wheel_filename)
        uv.pip_install(venv_path, [str(wheel_path)])
    except Exception as e:
        logger.warning("SageAttentionインストール失敗（生成速度の最適化のみ無効化されます）: %s", e)
        return False

    return _verify_sage_attention(python)
