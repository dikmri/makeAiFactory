from __future__ import annotations

import logging
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ..constants import VRAM_MINIMUM_GB, VRAM_RECOMMENDED_GB
from ..domain.errors import SystemUnsupportedError
from ..i18n import tr

logger = logging.getLogger(__name__)


@dataclass
class GpuInfo:
    name: str
    vram_mb: int
    driver_version: str

    @property
    def vram_gb(self) -> float:
        return self.vram_mb / 1024


@dataclass
class SystemInfo:
    os_name: str = ""
    os_version: str = ""
    cpu: str = ""
    ram_gb: float = 0.0
    gpus: list[GpuInfo] = field(default_factory=list)
    disk_free_gb: float = 0.0
    nvidia_smi_available: bool = False

    @property
    def has_nvidia_gpu(self) -> bool:
        return bool(self.gpus)

    @property
    def primary_gpu(self) -> GpuInfo | None:
        return self.gpus[0] if self.gpus else None


def _run(cmd: list[str], timeout: int = 10) -> str:
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        return result.stdout.strip()
    except Exception as e:
        logger.debug("コマンド実行失敗 %s: %s", cmd, e)
        return ""


def _probe_gpu_via_nvidia_smi() -> list[GpuInfo]:
    smi = shutil.which("nvidia-smi")
    if not smi:
        return []
    out = _run([smi, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"])
    if not out:
        return []
    gpus = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue
        name = parts[0]
        vram_str = parts[1]
        driver = parts[2]
        match = re.search(r"(\d+)", vram_str)
        vram_mb = int(match.group(1)) if match else 0
        gpus.append(GpuInfo(name=name, vram_mb=vram_mb, driver_version=driver))
    return gpus


def _probe_gpu_via_powershell() -> list[GpuInfo]:
    out = _run([
        "powershell", "-NoProfile", "-Command",
        "Get-WmiObject Win32_VideoController | Select-Object Name,AdapterRAM,DriverVersion | ConvertTo-Json"
    ], timeout=15)
    if not out:
        return []
    import json
    try:
        data = json.loads(out)
        if isinstance(data, dict):
            data = [data]
        gpus = []
        for item in data:
            name = item.get("Name", "")
            if "NVIDIA" not in name:
                continue
            ram_bytes = item.get("AdapterRAM", 0) or 0
            driver = item.get("DriverVersion", "")
            gpus.append(GpuInfo(name=name, vram_mb=int(ram_bytes / 1024 / 1024), driver_version=driver))
        return gpus
    except Exception:
        return []


def _disk_free_gb(path: Path) -> float:
    try:
        stat = shutil.disk_usage(path)
        return stat.free / (1024 ** 3)
    except Exception:
        return 0.0


def _ram_gb() -> float:
    try:
        out = _run(["powershell", "-NoProfile", "-Command",
                    "(Get-WmiObject Win32_ComputerSystem).TotalPhysicalMemory"])
        return int(out) / (1024 ** 3)
    except Exception:
        return 0.0


def probe_system(runtime_root: Path) -> SystemInfo:
    info = SystemInfo()
    info.os_name = platform.system()
    info.os_version = platform.version()
    info.cpu = platform.processor()

    info.ram_gb = _ram_gb()

    gpus = _probe_gpu_via_nvidia_smi()
    if gpus:
        info.nvidia_smi_available = True
    else:
        gpus = _probe_gpu_via_powershell()

    info.gpus = gpus
    info.disk_free_gb = _disk_free_gb(runtime_root.parent)

    logger.info(
        "システム情報: OS=%s | CPU=%s | RAM=%.1fGB | GPU=%s | VRAM=%.1fGB | Disk空き=%.1fGB",
        info.os_version,
        info.cpu or "不明",
        info.ram_gb,
        info.primary_gpu.name if info.primary_gpu else "なし",
        info.primary_gpu.vram_gb if info.primary_gpu else 0,
        info.disk_free_gb,
    )
    return info


def validate_system(info: SystemInfo) -> None:
    if not info.has_nvidia_gpu:
        raise SystemUnsupportedError(tr(
            "makeAiFactoryを実行できません。\n\n"
            "原因:\nNVIDIA GPUが見つかりません。\n\n"
            "makeAiFactoryは現在、Windows + NVIDIA GPU環境のみ対応しています。"
        ))

    gpu = info.primary_gpu
    assert gpu is not None
    if gpu.vram_gb < VRAM_MINIMUM_GB:
        logger.warning(
            "VRAM %.1f GB は最低要件 %d GB 未満です。生成に失敗する可能性があります。",
            gpu.vram_gb, VRAM_MINIMUM_GB,
        )
    elif gpu.vram_gb < VRAM_RECOMMENDED_GB:
        logger.warning(
            "VRAM %.1f GB は推奨 %d GB 未満です。「設定 > VRAMモード」で低VRAMモードまたは超省VRAMモードの使用を推奨します。",
            gpu.vram_gb, VRAM_RECOMMENDED_GB,
        )
