#!/usr/bin/env python3
"""Numbers for a rendered sound, so "harsh" stops being taste.

Pure standard library on purpose: the test suite imports this, and a suite that
needs numpy installed is a suite that goes red on a fresh box.

The measures are chosen against the complaint ("robotic and pretty bad"):

  attack_ms    how long the sound takes to reach its peak. A gain that jumps
               there in a couple of milliseconds *clicks*, and a click is the
               single most chiptune-sounding thing a synth does.
  centroid_hz  the brightness of the sound — the energy-weighted mean frequency.
               A square/triangle wave at 800Hz carries strong odd harmonics far
               above it; the same note through a lowpass does not.
  hf_ratio     share of the energy above 4kHz, i.e. the fizz.
  crest        peak/rms. A synth cue with no dynamics inside it sits near 1.4
               (a raw tone); something with a shaped body sits higher.
"""
from __future__ import annotations

import cmath
import math
import struct
import wave
from pathlib import Path


def read_wav(path: str | Path) -> tuple[list[float], int]:
    """A mono float list in -1..1, and the sample rate."""
    with wave.open(str(path), "rb") as w:
        rate = w.getframerate()
        n = w.getnframes()
        width = w.getsampwidth()
        chans = w.getnchannels()
        raw = w.readframes(n)
    if width != 2:
        raise ValueError(f"{path}: expected 16-bit, got {width * 8}-bit")
    vals = struct.unpack(f"<{len(raw) // 2}h", raw)
    if chans > 1:  # average the channels; everything here is mono anyway
        vals = [sum(vals[i:i + chans]) / chans for i in range(0, len(vals), chans)]
    return [v / 32768.0 for v in vals], rate


def _fft(x: list[complex]) -> list[complex]:
    n = len(x)
    if n == 1:
        return x
    even = _fft(x[0::2])
    odd = _fft(x[1::2])
    out = [0j] * n
    for k in range(n // 2):
        t = cmath.exp(-2j * math.pi * k / n) * odd[k]
        out[k] = even[k] + t
        out[k + n // 2] = even[k] - t
    return out


def spectrum(frame: list[float], rate: int) -> list[tuple[float, float]]:
    """(frequency, magnitude) for one Hann-windowed frame, positive half only."""
    n = 1
    while n * 2 <= len(frame):
        n *= 2
    frame = frame[:n]
    win = [0.5 - 0.5 * math.cos(2 * math.pi * i / (n - 1)) for i in range(n)]
    mags = _fft([complex(s * w, 0) for s, w in zip(frame, win)])
    return [(k * rate / n, abs(mags[k])) for k in range(n // 2)]


def measure(samples: list[float], rate: int) -> dict:
    peak = max((abs(s) for s in samples), default=0.0)
    energy = sum(s * s for s in samples)
    rms = math.sqrt(energy / len(samples)) if samples else 0.0

    # attack: from the first sample past the noise floor to the peak
    attack_ms = 0.0
    if peak > 0:
        floor = peak * 0.05
        start = next((i for i, s in enumerate(samples) if abs(s) >= floor), 0)
        top = max(range(len(samples)), key=lambda i: abs(samples[i]))
        attack_ms = max(0.0, (top - start) * 1000.0 / rate)

    # brightness, averaged over the frames that actually have sound in them
    step = 2048
    cents, hfs, weights = [], [], []
    for i in range(0, max(1, len(samples) - step), step):
        frame = samples[i:i + step]
        if len(frame) < step:
            break
        e = sum(s * s for s in frame)
        if e < energy * 0.005:
            continue
        spec = spectrum(frame, rate)
        tot = sum(m for _, m in spec)
        if tot <= 0:
            continue
        cents.append(sum(f * m for f, m in spec) / tot)
        hfs.append(sum(m for f, m in spec if f >= 4000) / tot)
        weights.append(e)
    wsum = sum(weights) or 1.0
    centroid = sum(c * w for c, w in zip(cents, weights)) / wsum if cents else 0.0
    hf = sum(h * w for h, w in zip(hfs, weights)) / wsum if hfs else 0.0

    return {
        "seconds": round(len(samples) / rate, 3),
        "peak": round(peak, 4),
        "rms": round(rms, 5),
        "crest": round(peak / rms, 2) if rms > 0 else 0.0,
        "attack_ms": round(attack_ms, 2),
        "centroid_hz": round(centroid, 1),
        "hf_ratio": round(hf, 4),
        "clipped": sum(1 for s in samples if abs(s) >= 0.999),
    }


def measure_file(path: str | Path) -> dict:
    samples, rate = read_wav(path)
    return measure(samples, rate)
