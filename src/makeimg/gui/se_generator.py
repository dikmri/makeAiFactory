from __future__ import annotations

import math
import random
import struct
import wave
import tempfile
from pathlib import Path


def _write_wav(path: Path, samples: list[float], sample_rate: int = 44100) -> Path:
    buf = b""
    for s in samples:
        clamped = max(-1.0, min(1.0, s))
        val = int(clamped * 32767)
        buf += struct.pack("<h", val)
    with wave.open(str(path), "w") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sample_rate)
        f.writeframes(buf)
    return path


def generate_tick_sound(path: Path | None = None, sample_rate: int = 44100) -> Path:
    if path is None:
        path = Path(tempfile.mktemp(suffix=".wav"))
    duration = 0.06
    freq = 1800
    n = int(sample_rate * duration)
    samples = []
    for i in range(n):
        t = i / sample_rate
        env = math.exp(-t * 60)
        samples.append(env * 0.5 * math.sin(2 * math.pi * freq * t))
    return _write_wav(path, samples, sample_rate)


def generate_swoosh_sound(path: Path | None = None, sample_rate: int = 44100) -> Path:
    if path is None:
        path = Path(tempfile.mktemp(suffix=".wav"))
    duration = 0.7
    n = int(sample_rate * duration)
    samples = []
    rng = random.Random(42)
    for i in range(n):
        t = i / sample_rate
        progress = t / duration
        freq = 400 + 3000 * progress
        rise_env = min(1.0, progress * 5)
        fall_env = max(0.0, 1.0 - (progress - 0.3) * 2.5)
        env = rise_env * fall_env
        noise = (rng.random() * 2 - 1) * 0.4
        tone = math.sin(2 * math.pi * freq * t)
        mixed = 0.6 * tone + 0.4 * noise
        samples.append(mixed * env * 0.5)
    return _write_wav(path, samples, sample_rate)


def generate_typekey_sound(path: Path | None = None, sample_rate: int = 44100) -> Path:
    if path is None:
        path = Path(tempfile.mktemp(suffix=".wav"))
    duration = 0.04
    n = int(sample_rate * duration)
    samples = []
    rng = random.Random(7)
    for i in range(n):
        t = i / sample_rate
        env = math.exp(-t * 150)
        noise = (rng.random() * 2 - 1) * env * 0.35
        tone = env * 0.25 * math.sin(2 * math.pi * 4000 * t)
        samples.append(noise + tone)
    return _write_wav(path, samples, sample_rate)


def generate_enter_sound(path: Path | None = None, sample_rate: int = 44100) -> Path:
    if path is None:
        path = Path(tempfile.mktemp(suffix=".wav"))
    duration = 0.25
    n = int(sample_rate * duration)
    samples = []
    for i in range(n):
        t = i / sample_rate
        if t < 0.01:
            env = t / 0.01 * 0.8
        elif t < 0.04:
            env = 0.8
        elif t < 0.12:
            env = 0.8 * math.exp(-(t - 0.04) * 15)
        else:
            env = 0.8 * math.exp(-0.08 * 15) * math.exp(-(t - 0.12) * 30)
        ring_freq = 2200 * math.exp(-t * 8)
        tone = 0.5 * math.sin(2 * math.pi * ring_freq * t)
        low = 0.3 * math.sin(2 * math.pi * 160 * t)
        samples.append((tone + low) * env * 0.5)
    return _write_wav(path, samples, sample_rate)


def generate_brace_open_sound(path: Path | None = None, sample_rate: int = 44100) -> Path:
    if path is None:
        path = Path(tempfile.mktemp(suffix=".wav"))
    duration = 0.08
    n = int(sample_rate * duration)
    samples = []
    for i in range(n):
        t = i / sample_rate
        env = math.exp(-t * 40)
        tone = 0.6 * math.sin(2 * math.pi * 1200 * t)
        chirp = 0.4 * math.sin(2 * math.pi * (2400 + 1600 * math.exp(-t * 50)) * t)
        samples.append((tone + chirp) * env * 0.4)
    return _write_wav(path, samples, sample_rate)


def generate_brace_close_sound(path: Path | None = None, sample_rate: int = 44100) -> Path:
    if path is None:
        path = Path(tempfile.mktemp(suffix=".wav"))
    duration = 0.07
    n = int(sample_rate * duration)
    samples = []
    for i in range(n):
        t = i / sample_rate
        env = math.exp(-t * 50)
        tone = 0.6 * math.sin(2 * math.pi * 1600 * t)
        chirp = 0.4 * math.sin(2 * math.pi * (2800 - 1200 * t / duration) * t)
        samples.append((tone + chirp) * env * 0.4)
    return _write_wav(path, samples, sample_rate)


def generate_pipe_sound(path: Path | None = None, sample_rate: int = 44100) -> Path:
    if path is None:
        path = Path(tempfile.mktemp(suffix=".wav"))
    duration = 0.1
    n = int(sample_rate * duration)
    samples = []
    for i in range(n):
        t = i / sample_rate
        env = math.exp(-t * 35)
        tone = 0.5 * math.sin(2 * math.pi * 800 * t)
        overtone = 0.3 * math.sin(2 * math.pi * 2400 * t)
        zing = 0.2 * math.sin(2 * math.pi * 5000 * t) * math.exp(-t * 60)
        samples.append((tone + overtone + zing) * env * 0.45)
    return _write_wav(path, samples, sample_rate)


def generate_comma_sound(path: Path | None = None, sample_rate: int = 44100) -> Path:
    if path is None:
        path = Path(tempfile.mktemp(suffix=".wav"))
    duration = 0.05
    n = int(sample_rate * duration)
    samples = []
    for i in range(n):
        t = i / sample_rate
        env = math.exp(-t * 80)
        tone = 0.7 * math.sin(2 * math.pi * 600 * t)
        click = 0.3 * math.sin(2 * math.pi * 3000 * t) * math.exp(-t * 200)
        samples.append((tone + click) * env * 0.35)
    return _write_wav(path, samples, sample_rate)


def generate_colon_sound(path: Path | None = None, sample_rate: int = 44100) -> Path:
    if path is None:
        path = Path(tempfile.mktemp(suffix=".wav"))
    duration = 0.06
    n = int(sample_rate * duration)
    samples = []
    rng = random.Random(77)
    for i in range(n):
        t = i / sample_rate
        env = math.exp(-t * 50)
        pop = 0.5 * math.sin(2 * math.pi * 1800 * t)
        noise = (rng.random() * 2 - 1) * 0.15 * math.exp(-t * 100)
        samples.append((pop + noise) * env * 0.4)
    return _write_wav(path, samples, sample_rate)


def generate_save_sound(path: Path | None = None, sample_rate: int = 44100) -> Path:
    if path is None:
        path = Path(tempfile.mktemp(suffix=".wav"))
    duration = 1.4
    n = int(sample_rate * duration)
    dry = [0.0] * n
    notes = [
        (0.00, 523.25, 0.35),
        (0.12, 659.25, 0.35),
        (0.24, 783.99, 0.45),
        (0.36, 1046.50, 0.55),
        (0.48, 1318.51, 0.40),
    ]
    for start, freq, vol in notes:
        s = int(start * sample_rate)
        for i in range(n - s):
            t = i / sample_rate
            env = math.exp(-t * 4.5) * vol
            tone = 0.6 * math.sin(2 * math.pi * freq * t)
            shimmer = 0.25 * math.sin(2 * math.pi * freq * 2.0 * t)
            overtone = 0.15 * math.sin(2 * math.pi * freq * 3.0 * t) * math.exp(-t * 8)
            dry[s + i] += (tone + shimmer + overtone) * env
    echo_delays = [
        (0.15, 0.45),
        (0.32, 0.30),
        (0.52, 0.18),
        (0.75, 0.09),
    ]
    samples = [0.0] * n
    for i in range(n):
        samples[i] = dry[i]
    for delay_sec, decay in echo_delays:
        d = int(delay_sec * sample_rate)
        for i in range(n - d):
            samples[i + d] += dry[i] * decay
    peak = max(abs(s) for s in samples) or 1.0
    scale = 0.7 / peak
    for i in range(n):
        samples[i] *= scale
    return _write_wav(path, samples, sample_rate)


_se_dir: Path | None = None
_se_files: dict[str, Path] = {}


def generate_paren_sound(path: Path | None = None, sample_rate: int = 44100) -> Path:
    if path is None:
        path = Path(tempfile.mktemp(suffix=".wav"))
    duration = 0.05
    n = int(sample_rate * duration)
    samples = []
    for i in range(n):
        t = i / sample_rate
        env = math.exp(-t * 80)
        tone = 0.5 * math.sin(2 * math.pi * 2200 * t)
        chime = 0.3 * math.sin(2 * math.pi * 3300 * t)
        pop = 0.2 * math.sin(2 * math.pi * 5500 * t) * math.exp(-t * 200)
        samples.append((tone + chime + pop) * env * 0.35)
    return _write_wav(path, samples, sample_rate)


def generate_backspace_sound(path: Path | None = None, sample_rate: int = 44100) -> Path:
    if path is None:
        path = Path(tempfile.mktemp(suffix=".wav"))
    duration = 0.05
    n = int(sample_rate * duration)
    samples = []
    for i in range(n):
        t = i / sample_rate
        env = math.exp(-t * 120)
        low = 0.6 * math.sin(2 * math.pi * 900 * t)
        click = 0.4 * math.sin(2 * math.pi * 2800 * t) * math.exp(-t * 200)
        samples.append((low + click) * env * 0.35)
    return _write_wav(path, samples, sample_rate)


def generate_delete_sound(path: Path | None = None, sample_rate: int = 44100) -> Path:
    if path is None:
        path = Path(tempfile.mktemp(suffix=".wav"))
    duration = 0.06
    n = int(sample_rate * duration)
    samples = []
    for i in range(n):
        t = i / sample_rate
        env = math.exp(-t * 100)
        mid = 0.5 * math.sin(2 * math.pi * 1100 * t)
        scratch = 0.3 * math.sin(2 * math.pi * 3500 * t) * math.exp(-t * 150)
        thud = 0.2 * math.sin(2 * math.pi * 400 * t) * math.exp(-t * 80)
        samples.append((mid + scratch + thud) * env * 0.35)
    return _write_wav(path, samples, sample_rate)


def generate_shift_sound(path: Path | None = None, sample_rate: int = 44100) -> Path:
    if path is None:
        path = Path(tempfile.mktemp(suffix=".wav"))
    duration = 0.04
    n = int(sample_rate * duration)
    samples = []
    for i in range(n):
        t = i / sample_rate
        env = math.exp(-t * 150)
        click = 0.5 * math.sin(2 * math.pi * 3500 * t) * math.exp(-t * 250)
        tone = 0.3 * math.sin(2 * math.pi * 1400 * t)
        samples.append((click + tone) * env * 0.35)
    return _write_wav(path, samples, sample_rate)


def generate_space_sound(path: Path | None = None, sample_rate: int = 44100) -> Path:
    if path is None:
        path = Path(tempfile.mktemp(suffix=".wav"))
    duration = 0.07
    n = int(sample_rate * duration)
    samples = []
    rng = random.Random(99)
    for i in range(n):
        t = i / sample_rate
        env = math.exp(-t * 70)
        thud = 0.5 * math.sin(2 * math.pi * 500 * t)
        puff = (rng.random() * 2 - 1) * 0.3 * math.exp(-t * 100)
        samples.append((thud + puff) * env * 0.4)
    return _write_wav(path, samples, sample_rate)


def generate_complete_sound(path: Path | None = None, sample_rate: int = 44100) -> Path:
    if path is None:
        path = Path(tempfile.mktemp(suffix=".wav"))
    duration = 1.2
    n = int(sample_rate * duration)
    samples = [0.0] * n
    sweeps = [
        (0.00, 0.38),
        (0.42, 0.38),
        (0.84, 0.38),
    ]
    for start, dur in sweeps:
        s = int(start * sample_rate)
        nd = int(dur * sample_rate)
        for i in range(nd):
            if s + i >= n:
                break
            t = i / sample_rate
            p = t / dur
            freq = 800 + 3200 * (1 - (1 - p) ** 2)
            env = min(1.0, p * 20) * math.exp(-max(0, (p - 0.3) * 6))
            tone = 0.6 * math.sin(2 * math.pi * freq * t)
            shimmer = 0.3 * math.sin(2 * math.pi * freq * 1.5 * t)
            bell = 0.1 * math.sin(2 * math.pi * freq * 3 * t) * math.exp(-t * 12)
            samples[s + i] += (tone + shimmer + bell) * env * 0.5
    peak = max(abs(s) for s in samples) or 1.0
    scale = 0.75 / peak
    for i in range(n):
        samples[i] *= scale
    return _write_wav(path, samples, sample_rate)


def generate_batch_done_sound(path: Path | None = None, sample_rate: int = 44100) -> Path:
    if path is None:
        path = Path(tempfile.mktemp(suffix=".wav"))
    duration = 0.4
    n = int(sample_rate * duration)
    samples = [0.0] * n
    nd = int(0.35 * sample_rate)
    for i in range(nd):
        t = i / sample_rate
        p = t / 0.35
        freq = 600 + 1800 * (1 - (1 - p) ** 2)
        env = min(1.0, p * 15) * math.exp(-max(0, (p - 0.4) * 5))
        tone = 0.7 * math.sin(2 * math.pi * freq * t)
        bell = 0.3 * math.sin(2 * math.pi * freq * 2 * t) * math.exp(-t * 8)
        samples[i] += (tone + bell) * env * 0.4
    return _write_wav(path, samples, sample_rate)


def ensure_se_files() -> dict[str, Path]:
    global _se_dir, _se_files
    if _se_files and all(p.exists() for p in _se_files.values()):
        return dict(_se_files)
    _se_dir = Path(tempfile.mkdtemp(prefix="makeimg_se_"))
    _se_files["tick"] = generate_tick_sound(_se_dir / "tick.wav")
    _se_files["swoosh"] = generate_swoosh_sound(_se_dir / "swoosh.wav")
    _se_files["typekey"] = generate_typekey_sound(_se_dir / "typekey.wav")
    _se_files["enter"] = generate_enter_sound(_se_dir / "enter.wav")
    _se_files["brace_open"] = generate_brace_open_sound(_se_dir / "brace_open.wav")
    _se_files["brace_close"] = generate_brace_close_sound(_se_dir / "brace_close.wav")
    _se_files["pipe"] = generate_pipe_sound(_se_dir / "pipe.wav")
    _se_files["comma"] = generate_comma_sound(_se_dir / "comma.wav")
    _se_files["colon"] = generate_colon_sound(_se_dir / "colon.wav")
    _se_files["paren"] = generate_paren_sound(_se_dir / "paren.wav")
    _se_files["backspace"] = generate_backspace_sound(_se_dir / "backspace.wav")
    _se_files["delete"] = generate_delete_sound(_se_dir / "delete.wav")
    _se_files["shift"] = generate_shift_sound(_se_dir / "shift.wav")
    _se_files["space"] = generate_space_sound(_se_dir / "space.wav")
    _se_files["complete"] = generate_complete_sound(_se_dir / "complete.wav")
    _se_files["batch_done"] = generate_batch_done_sound(_se_dir / "batch_done.wav")
    _se_files["save"] = generate_save_sound(_se_dir / "save.wav")
    return dict(_se_files)