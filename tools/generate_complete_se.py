"""完成通知音 (app/assets/complete.wav) を生成する。

外部アセットに依存せず、純粋なサイン波の合成で
柔らかい2音の "ポーン" チャイムを作る。
このスクリプトは手動実行用 (生成物は git にコミットする)。
"""
from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

SAMPLE_RATE = 44100


def _tone(freq: float, duration_sec: float, amplitude: float, decay: float) -> list[float]:
    n = int(SAMPLE_RATE * duration_sec)
    out = []
    for i in range(n):
        t = i / SAMPLE_RATE
        env = math.exp(-decay * t)
        out.append(amplitude * env * math.sin(2 * math.pi * freq * t))
    return out


def main() -> None:
    out_path = Path(__file__).parent.parent / "app" / "assets" / "complete.wav"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    note1 = _tone(880.0, 0.22, 0.5, decay=6.0)      # A5
    gap = [0.0] * int(SAMPLE_RATE * 0.03)
    note2 = _tone(1318.51, 0.32, 0.5, decay=5.0)    # E6

    samples = note1 + gap + note2

    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        frames = b"".join(struct.pack("<h", max(-32767, min(32767, int(s * 32767)))) for s in samples)
        wf.writeframes(frames)

    print(f"Generated: {out_path} ({out_path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
