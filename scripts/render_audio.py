#!/usr/bin/env python3
"""Render the game's own sounds to WAV files, offline, and measure them.

Nothing in the suite could hear the game before this: every audio test asserted
what was *asked for* (a method was called, mute was set), never what came out.
This drives the real `audio.js` through an `OfflineAudioContext`, so the samples
are the ones a player gets, rendered faster than real time and with no audio
device involved.

  python3 scripts/render_audio.py                  # every cue -> audio-renders/
  python3 scripts/render_audio.py --cue jump       # just one
  python3 scripts/render_audio.py --json           # metrics only, no files

The music cues are the awkward part: `playTheme` schedules itself with
`setInterval`, which never fires while an offline context renders. So the timer
is captured instead of run, and each tick is called by hand with the bar's start
time added to every `when` — the same notes, laid out on the offline timeline.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import json
import re
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP / "scripts"))

from audio_metrics import measure_file  # noqa: E402
from shots import server  # noqa: E402

OUT = APP / "audio-renders"

# cue -> (how to fire it, how many seconds to render)
SFX = {
    "jump": ("s.jump()", 1.0),
    "collect": ("s.collect(0)", 1.0),
    "bop": ("s.bop()", 1.0),
    "stumble": ("s.stumble()", 1.0),
    "splash": ("s.splash()", 1.2),
    "dig": ("s.dig()", 1.0),
    "treasure": ("s.treasure()", 1.6),
    "cheer": ("s.cheer()", 2.0),
    "ui": ("s.ui()", 0.6),
    "friend": ("s.friend()", 1.4),
    "ability": ("s.ability()", 1.2),
    "recharged": ("s.recharged()", 0.8),
}
# The music the game has to have: the menu's, plus whatever tune each chapter
# asks for. Read from chapters.js — the side that *asks* — and not from the
# `themes` table in audio.js, which is the side that answers: a list taken from
# the answers could never say a chapter was left without a tune of its own, and
# a new chapter would silently render the menu loop again (#351).
THEMES = ["menu"] + re.findall(r'^\s*theme: "(\w+)",',
                               (APP / "public/js/chapters.js").read_text(), re.M)
THEME_SECONDS = 16.0  # long enough that the slow lullaby has bars to compare

RENDER = """
async ([call, seconds, theme]) => {
  const { Sound } = await import('/js/audio.js');
  const rate = 44100;
  const ctx = new OfflineAudioContext(1, Math.ceil(rate * seconds), rate);
  const s = new Sound();
  // unlock() builds the graph off `new AudioContext`; hand it the offline one
  // instead and let it wire the same master/music/sfx gains into it.
  const RealAC = window.AudioContext;
  window.AudioContext = function () { return ctx; };
  s.unlock();
  window.AudioContext = RealAC;

  if (theme) {
    // capture the loop instead of running it: an interval never fires while an
    // offline context renders, so each tick is placed on the timeline by hand
    const realInterval = window.setInterval;
    let tick = null, tempo = 0;
    window.setInterval = (fn, ms) => { tick = fn; tempo = ms; return 0; };
    // Every voice schedules from `this.ctx.currentTime + when`, and an offline
    // context's clock does not move while it renders — so the bar's start time
    // is added to the clock itself rather than to each call. Wrapping the
    // methods instead would need this script to know all their signatures, and
    // it would stop being true the next time one of them gains an argument.
    const at = { t: 0 };
    s.ctx = new Proxy(ctx, {
      get(target, prop) {
        if (prop === 'currentTime') return target.currentTime + at.t;
        const v = target[prop];
        return typeof v === 'function' ? v.bind(target) : v;
      },
    });
    s.playTheme(theme);           // fires tick once itself, at t = 0
    window.setInterval = realInterval;
    for (let i = 1; tick && i * tempo / 1000 < seconds; i++) {
      at.t = i * tempo / 1000;
      tick();
    }
  } else {
    eval(call);
  }

  const buf = await ctx.startRendering();
  const pcm = buf.getChannelData(0);
  const bytes = new DataView(new ArrayBuffer(44 + pcm.length * 2));
  const str = (off, t) => { for (let i = 0; i < t.length; i++) bytes.setUint8(off + i, t.charCodeAt(i)); };
  str(0, 'RIFF'); bytes.setUint32(4, 36 + pcm.length * 2, true); str(8, 'WAVEfmt ');
  bytes.setUint32(16, 16, true); bytes.setUint16(20, 1, true); bytes.setUint16(22, 1, true);
  bytes.setUint32(24, rate, true); bytes.setUint32(28, rate * 2, true);
  bytes.setUint16(32, 2, true); bytes.setUint16(34, 16, true);
  str(36, 'data'); bytes.setUint32(40, pcm.length * 2, true);
  for (let i = 0; i < pcm.length; i++) {
    const v = Math.max(-1, Math.min(1, pcm[i]));
    bytes.setInt16(44 + i * 2, v < 0 ? v * 0x8000 : v * 0x7FFF, true);
  }
  let bin = '';
  const raw = new Uint8Array(bytes.buffer);
  for (let i = 0; i < raw.length; i += 8192) bin += String.fromCharCode(...raw.subarray(i, i + 8192));
  return btoa(bin);
}
"""


def render(page, url: str, cue: str, out_dir: Path) -> Path:
    """One cue rendered to `out_dir/<cue>.wav`; returns the path."""
    if page.url.split("?")[0].rstrip("/") != url.rstrip("/"):
        page.goto(url, wait_until="domcontentloaded")
    theme = cue[6:] if cue.startswith("theme-") else ""
    call, seconds = ("", THEME_SECONDS) if theme else SFX[cue]
    b64 = page.evaluate(RENDER, [call, seconds, theme])
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{cue}.wav"
    path.write_bytes(base64.b64decode(b64))
    return path


def cue_names(only: str | None) -> list[str]:
    every = list(SFX) + [f"theme-{t}" for t in THEMES]
    if not only:
        return every
    if only not in every:
        raise SystemExit(f"unknown cue {only!r}; have: {', '.join(every)}")
    return [only]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cue", help="render only this one")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--json", action="store_true", help="print the metrics as JSON")
    ap.add_argument("--base-url", help="render against a deployed site instead")
    a = ap.parse_args()
    from playwright.sync_api import sync_playwright

    out_dir = Path(a.out)
    rows = {}
    ctx = contextlib.nullcontext(a.base_url) if a.base_url else server()
    with ctx as url, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        for cue in cue_names(a.cue):
            path = render(page, url, cue, out_dir)
            rows[cue] = measure_file(path)
        browser.close()

    if a.json:
        print(json.dumps(rows, indent=2))
        return 0
    head = ("cue", "sec", "peak", "rms", "crest", "attack ms", "centroid", "hf", "clip")
    print("%-16s %5s %6s %7s %6s %10s %9s %7s %5s" % head)
    for cue, m in rows.items():
        print("%-16s %5.2f %6.3f %7.4f %6.2f %10.2f %9.0f %7.3f %5d" % (
            cue, m["seconds"], m["peak"], m["rms"], m["crest"],
            m["attack_ms"], m["centroid_hz"], m["hf_ratio"], m["clipped"]))
    print(f"\nwav -> {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
