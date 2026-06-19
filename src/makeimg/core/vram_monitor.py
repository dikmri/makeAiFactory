"""nvidia-smi / Windows API を使った非同期リソースモニター。"""
from __future__ import annotations

import asyncio
import ctypes
import logging
import shutil
import subprocess
import sys

logger = logging.getLogger(__name__)


class VramMonitor:
    """生成ジョブ実行中の VRAM 使用量を 1 秒間隔で計測する非同期コンテキストマネージャ。

    使い方::

        async with VramMonitor() as vram:
            await run_generation()
        print(vram.peak_gb, vram.avg_gb)
    """

    def __init__(self, interval_sec: float = 1.0):
        self._interval = interval_sec
        self._samples: list[float] = []
        self._task: asyncio.Task | None = None
        self._smi: str | None = shutil.which("nvidia-smi")

    async def __aenter__(self) -> "VramMonitor":
        self._samples = []
        if self._smi:
            self._task = asyncio.create_task(self._poll())
        return self

    async def __aexit__(self, *_) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _poll(self) -> None:
        kwargs: dict = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        assert self._smi is not None
        while True:
            try:
                proc = await asyncio.create_subprocess_exec(
                    self._smi,
                    "--query-gpu=memory.used",
                    "--format=csv,noheader,nounits",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    **kwargs,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=3.0)
                raw = stdout.decode().strip()
                if raw.lstrip("-").isdigit():
                    mb = int(raw)
                    if mb > 0:
                        self._samples.append(mb / 1024.0)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug("VRAM計測スキップ: %s", exc)

            await asyncio.sleep(self._interval)

    @property
    def peak_gb(self) -> float:
        return max(self._samples, default=0.0)

    @property
    def avg_gb(self) -> float:
        return sum(self._samples) / len(self._samples) if self._samples else 0.0

    @property
    def available(self) -> bool:
        """nvidia-smi が利用可能なら True。"""
        return self._smi is not None


# Windows MEMORYSTATUSEX 構造体 (GlobalMemoryStatusEx 用)
class _MEMSTATEX(ctypes.Structure):
    _fields_ = [
        ("dwLength",                ctypes.c_ulong),
        ("dwMemoryLoad",            ctypes.c_ulong),
        ("ullTotalPhys",            ctypes.c_ulonglong),
        ("ullAvailPhys",            ctypes.c_ulonglong),
        ("ullTotalPageFile",        ctypes.c_ulonglong),
        ("ullAvailPageFile",        ctypes.c_ulonglong),
        ("ullTotalVirtual",         ctypes.c_ulonglong),
        ("ullAvailVirtual",         ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def _available_ram_gb() -> float | None:
    """Windows API で現在の空き物理 RAM (GB) を返す。失敗時は None。"""
    if sys.platform != "win32":
        return None
    try:
        m = _MEMSTATEX()
        m.dwLength = ctypes.sizeof(_MEMSTATEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))  # type: ignore[attr-defined]
        return m.ullAvailPhys / (1024 ** 3)
    except Exception:
        return None


class RamMonitor:
    """生成ジョブ実行中のシステム RAM 使用量を 1 秒間隔で計測するコンテキストマネージャ。

    novram モードでは VRAM の代わりにシステム RAM が使われるため、
    RAM 使用量の計測が必要スペック判定に重要になる。

    Windows の GlobalMemoryStatusEx API を直接呼ぶためサブプロセス不要。

    使い方::

        async with RamMonitor(total_gb=64.0) as ram:
            await run_generation()
        print(ram.peak_used_gb, ram.avg_used_gb)
    """

    def __init__(self, total_gb: float = 0.0, interval_sec: float = 1.0):
        self._total_gb = total_gb
        self._interval = interval_sec
        self._free_samples: list[float] = []
        self._task: asyncio.Task | None = None

    async def __aenter__(self) -> "RamMonitor":
        self._free_samples = []
        self._task = asyncio.create_task(self._poll())
        return self

    async def __aexit__(self, *_) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _poll(self) -> None:
        while True:
            try:
                free = _available_ram_gb()
                if free is not None:
                    self._free_samples.append(free)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug("RAM計測スキップ: %s", exc)
            await asyncio.sleep(self._interval)

    @property
    def peak_used_gb(self) -> float:
        """生成中の RAM 使用量ピーク = 総RAM - 最小空きRAM。"""
        if not self._free_samples or self._total_gb <= 0:
            return 0.0
        return self._total_gb - min(self._free_samples)

    @property
    def avg_used_gb(self) -> float:
        """生成中の RAM 使用量平均。"""
        if not self._free_samples or self._total_gb <= 0:
            return 0.0
        return self._total_gb - (sum(self._free_samples) / len(self._free_samples))

    @property
    def available(self) -> bool:
        return sys.platform == "win32"
