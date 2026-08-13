#!/usr/bin/env python3
"""Render a strip of animation frames for one character, to be looked at.

The rig is numbers in a JSON file; the only way to know a tail wags rather than
tearing a hole in a hip is to draw it. This boots the game's own server, calls
the real `drawCharacter` at a series of times, and writes the frames side by
side into shots/_debug/.

  python3 scripts/rig_frames.py bluey                  # run cycle
  python3 scripts/rig_frames.py bluey --state cheer
  python3 scripts/rig_frames.py bluey --blink          # times a blink instead
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shots import APP, server  # noqa: E402

OUT = APP / "shots" / "_debug"

BLINK_AT = """
async (id) => {
  // ask the module when this character blinks rather than keeping a copy of its
  // offset here, which would drift the day the offset changes
  const s = await import('/js/sprites.js');
  for (let u = 0; u < 8; u += 0.005) if (s.blinkAmount(id, u) > 0.01) return Math.max(0, u - 0.02);
  return 0;
}
"""

DRAW = """
async ([id, state, times, size]) => {
  const s = await import('/js/sprites.js');
  await s.loadArt();
  s.preload([id]);
  for (let i = 0; i < 60 && !s.artState().loaded.includes(id); i++) {
    await new Promise((r) => setTimeout(r, 100));
  }
  const c = document.createElement('canvas');
  c.width = size * times.length;
  c.height = size + 20;
  document.body.style.margin = '0';
  document.body.innerHTML = '';
  document.body.appendChild(c);
  const ctx = c.getContext('2d');
  ctx.fillStyle = '#eef4fa';
  ctx.fillRect(0, 0, c.width, c.height);
  times.forEach((t, i) => {
    ctx.save();
    ctx.strokeStyle = '#c8d6e4';
    ctx.strokeRect(i * size, 0, size, c.height);
    ctx.restore();
    s.drawCharacter(ctx, id, i * size + size / 2, size + 4, size * 0.88, null, t, state, 1);
  });
  return s.artState().drawn[id];
}
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("character")
    ap.add_argument("--state", default="run")
    ap.add_argument("--frames", type=int, default=8)
    ap.add_argument("--size", type=int, default=220)
    ap.add_argument("--blink", action="store_true", help="sample across one blink")
    a = ap.parse_args()
    from playwright.sync_api import sync_playwright

    # a run cycle is ~0.57s at the game's cadence; a blink is 0.16s and happens
    # at a character-specific offset, so it has to be searched for rather than
    # sampled from zero.
    span = 0.16 if a.blink else 0.57

    OUT.mkdir(parents=True, exist_ok=True)
    with server() as url, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": a.size * a.frames, "height": a.size + 20})
        page.goto(url, wait_until="domcontentloaded")
        base = page.evaluate(BLINK_AT, a.character) if a.blink else 0.0
        times = [round(base + span * i / (a.frames - 1), 4) for i in range(a.frames)]
        how = page.evaluate(DRAW, [a.character, a.state, times, a.size])
        if how == "fallback":
            print(f"  PROBLEM {a.character} drew as the fallback dog, not from its artwork")
            return 1
        name = f"frames-{a.character}-{'blink' if a.blink else a.state}.png"
        page.screenshot(path=str(OUT / name))
        browser.close()
    # "rig" is the standing render cut into bands; "pose" is the artist's own
    # drawing of this state. Which one it was is the first thing to know when
    # looking at a strip that has come out wrong.
    print(f"  frames -> {(OUT / name).relative_to(APP)}  t={times[0]}..{times[-1]}  drawn as {how}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
