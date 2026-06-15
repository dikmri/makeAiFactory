"""nvidia-smi を使った非同期 VRAM 使用量モニター。"""
from __future__ import annotations

import asyncio
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
