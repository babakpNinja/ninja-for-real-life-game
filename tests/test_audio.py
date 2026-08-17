"""What the game actually sounds like (#305).

Every other audio test in this suite asserts what was *asked for*: a cue method
was called, mute was set, an utterance was queued. None of them could hear
anything, so "robotic and pretty bad" — Babak, twice — was invisible to a green
suite. These render the real `audio.js` through an `OfflineAudioContext` and
measure the samples that come out.

The three numbers, and why each is the complaint:

  attack_ms    a gain that reaches its peak in 4ms is a *click*. That click, on
               every cue, is most of what "cheap" sounds like. The old cues
               measured 4-5ms; a played note is tens.
  hf_ratio     share of the energy above 4kHz. Fizz. The old `bop`, `dig` and
               `splash` put a third of themselves up there.
  centroid_hz  overall brightness. Not bounded on its own — a kids' game should
               be bright — but a *ceiling* keeps a future cue from being shrill.

The bounds are deliberately loose: they are there to catch a regression back to
bleeps, not to pin the mix. `scripts/render_audio.py` prints the same table for
a human wanting to compare.
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP / "scripts"))

from audio_metrics import measure_file, read_wav  # noqa: E402
from js_source import object_block  # noqa: E402
from render_audio import SFX, THEMES, render  # noqa: E402


@pytest.fixture(scope="module")
def renders(tmp_path_factory, make_page, base_url):
    """Every cue rendered once, and measured. Cached for the module."""
    out = tmp_path_factory.mktemp("renders")
    page = make_page({"width": 800, "height": 600})
    made = {}
    try:
        for cue in list(SFX) + [f"theme-{t}" for t in THEMES]:
            made[cue] = render(page, base_url, cue, out)
    finally:
        page.context.close()
    return {cue: (path, measure_file(path)) for cue, path in made.items()}


# The split is by what the cue *is*, not by what passes. A note is announced —
# a coin, a fanfare, a friend arriving — and announcing something with a 4ms
# transient is the toy-till sound. A contact (tripping, landing in water,
# digging, bopping a balloon) is a hit, and a hit that fades in is not a hit, so
# those keep their transient and are only held to the fizz and level bounds.
TONAL = ["jump", "collect", "ui", "recharged", "treasure", "cheer", "friend", "ability"]
IMPACTS = ["stumble", "splash", "dig", "bop"]


def test_every_cue_is_classified():
    """A new cue has to be called a note or a hit before it can be measured.

    Without this the click bound is opt-in: add `bark()` to `audio.js`, leave it
    out of `TONAL`, and the strictest test here never looks at it.
    """
    named = set(TONAL) | set(IMPACTS)
    assert not set(SFX) - named, f"cues nobody classified: {sorted(set(SFX) - named)}"
    assert not named - set(SFX), f"classified cues that no longer exist: {sorted(named - set(SFX))}"
    assert not set(TONAL) & set(IMPACTS), "a cue cannot be both"


def test_no_cue_is_a_click(renders):
    """A sound has to arrive, not appear.

    `stumble`, `splash` and `dig` are impacts and are allowed to be fast — a
    scuff that fades in is not a scuff. Everything else gets a real attack.
    """
    fast = {c: renders[c][1]["attack_ms"] for c in TONAL if renders[c][1]["attack_ms"] < 5}
    assert not fast, f"cues that snap on instead of arriving: {fast}"


def test_nothing_fizzes(renders):
    """Energy above 4kHz, where the harshness lives.

    Measured on the old synthesis: bop 0.310, dig 0.330, splash 0.296,
    ability 0.220 — a third of some cues was fizz.
    """
    fizzy = {c: m["hf_ratio"] for c, (_, m) in renders.items() if m["hf_ratio"] > 0.08}
    assert not fizzy, f"cues with more than 8% of their energy above 4kHz: {fizzy}"


def test_nothing_is_shrill_and_nothing_clips(renders):
    loud = {c: (m["peak"], m["clipped"]) for c, (_, m) in renders.items()
            if m["peak"] > 0.6 or m["clipped"]}
    assert not loud, f"cues at or past the ceiling: {loud}"
    shrill = {c: m["centroid_hz"] for c, (_, m) in renders.items() if m["centroid_hz"] > 1800}
    assert not shrill, f"cues whose energy sits too high to be friendly: {shrill}"


def test_every_cue_makes_a_sound(renders):
    """The bounds above are all upper bounds, and silence passes every one."""
    silent = {c: m["rms"] for c, (_, m) in renders.items() if m["rms"] < 0.002}
    assert not silent, f"cues that render to (near) nothing: {silent}"


def test_the_same_cue_twice_is_not_the_same_recording(make_page, base_url, tmp_path):
    """Two of the same cue in a row must differ.

    Identical repeats are the other half of "robotic": a sample player with one
    sample. `tone` draws each note a little off the written pitch and a little
    harder or softer, through `Sound.rand()` — so freezing that one method is
    what this has to fail on.

    `recharged` and not `jump` on purpose: `jump` mixes in a noise burst whose
    buffer is filled from `Math.random()` directly, so it would still render
    differently with every human decision frozen. This cue is two tones and
    nothing else.

    That distinction is why `room()` draws its impulse from a fixed seed. While
    the room was re-drawn per boot, two renders differed by 1.5e-3 with the
    performance frozen solid — this test passed on a mutation that made the
    whole game play identically, because it was measuring the reverb's dice and
    not the player's. Frozen now, the same cue renders bit-for-bit the same.
    """
    page = make_page({"width": 800, "height": 600})
    try:
        a, _ = read_wav(render(page, base_url, "recharged", tmp_path / "a"))
        b, _ = read_wav(render(page, base_url, "recharged", tmp_path / "b"))
    finally:
        page.context.close()
    assert len(a) == len(b)
    n = min(len(a), len(b))
    diff = sum(abs(a[i] - b[i]) for i in range(n)) / n
    assert diff > 1e-4, f"two renders of the same cue are all but identical (mean |diff| {diff:.2e})"


def onsets(samples, rate, floor=0.18):
    """Where notes start, in seconds: rectified, smoothed, rising past a floor."""
    win = max(1, rate // 200)                       # 5ms
    env, acc = [], 0.0
    for i, s in enumerate(samples):
        acc += abs(s)
        if (i + 1) % win == 0:
            env.append(acc / win)
            acc = 0.0
    top = max(env) if env else 0.0
    if top <= 0:
        return []
    out, armed = [], True
    for i, v in enumerate(env):
        if armed and v > top * floor and (i == 0 or v > env[i - 1] * 1.6):
            out.append(i * win / rate)
            armed = False
        elif v < top * floor * 0.6:
            armed = True
    return out


@pytest.mark.parametrize("theme", THEMES)
def test_the_music_does_not_play_on_a_perfect_grid(renders, theme):
    """Swing: the offbeats land late, so the gaps between notes alternate.

    A loop whose every gap is identical is a metronome, and that is what the old
    themes were — `setInterval` at a fixed tempo with every note on the tick.
    The test asks the weaker question the measurement can answer honestly: that
    the gaps are not all the same.

    What it does *not* pin: deleting the swing term alone survives this (a
    mutation proved it) — the drift on every note keeps the gaps uneven, so the
    honest reading of a pass here is "not on a grid", not "swung".

    The bound is 20ms because the lullaby is the tight case and was measured
    before it was set: over 8 full renders its spread ran 35-70ms (every other
    theme, whose melody notes are separable, ran 300-680ms). Sleepytime's notes
    are 1.5s sines that overlap, so the only onsets anything can see are its
    downbeat plucks — which carry the drift and no swing at all.
    """
    path, _ = renders[f"theme-{theme}"]
    samples, rate = read_wav(path)
    gaps = [round(b - a, 4) for a, b in zip(onsets(samples, rate), onsets(samples, rate)[1:])]
    assert len(gaps) >= 6, f"{theme}: only {len(gaps)} gaps found — nothing to say about the timing"
    spread = max(gaps) - min(gaps)
    assert spread > 0.02, (
        f"{theme}: every gap between notes is within {spread * 1000:.0f}ms of every other "
        f"({gaps[:8]}) — that is a machine playing, not a person")


def motifs() -> dict[str, str]:
    """Each theme's written melody, read out of the `themes` table in `audio.js`.

    Read per entry rather than with one pattern over the whole table: a
    table-wide `motif: \\[(.*?)\\]` would answer about `menu` and say nothing
    about any of the others.
    """
    src = (APP / "public/js/audio.js").read_text()
    out = {}
    for name, entry in re.findall(r"(\w+)\s*:\s*\{(.*?)\}", object_block(src, "themes"), re.S):
        m = re.search(r"motif:\s*\[(.*?)\]", entry, re.S)
        if m:
            out[name] = " ".join(m.group(1).split())
    return out


def test_every_theme_has_its_own_written_tune():
    """A tune per chapter, and no two the same — not one formula in ten hats.

    The old melody was `SCALE[(s * 3 + floor(s / 8)) % 5]`, which is why no
    chapter had a tune to recognise: the notes were computed from the step
    number. Each theme now writes its motif down, so this asks the two things
    that matters — every theme has one, and no two are the same sequence.
    """
    found = motifs()
    assert set(found) == set(THEMES), (
        f"themes without a written motif: {sorted(set(THEMES) - set(found))}; "
        f"motifs for something that is not a theme: {sorted(set(found) - set(THEMES))}")
    seen = {}
    for name, motif in found.items():
        assert motif.count(",") >= 8, f"{name}'s motif is only {motif.count(',') + 1} steps long"
        if motif in seen:
            pytest.fail(f"{name} and {seen[motif]} are the same tune: {motif}")
        seen[motif] = name


def ring_out_ms(samples, rate) -> int:
    """How long a sound keeps sounding after it has stopped being loud.

    From the last point the envelope is above a tenth of the peak to the last
    point it is above 0.5% of it. A cue's own release makes this tens of
    milliseconds; a room makes it hundreds.
    """
    win = max(1, rate // 200)                       # 5ms
    env = [sum(abs(x) for x in samples[i:i + win]) / win for i in range(0, len(samples) - win, win)]
    top = max(env, default=0.0)
    if top <= 0:
        return 0
    loud = max(i for i, v in enumerate(env) if v > top * 0.1)
    quiet = max(i for i, v in enumerate(env) if v > top * 0.005)
    return round((quiet - loud) * win / rate * 1000)


def test_every_cue_is_in_a_room(renders):
    """The other half of "robotic": a sound that stops dead (#305).

    Every voice sends a share of itself to a convolver, so a cue decays into a
    room instead of ending at its own release. Measured both ways before the
    bound was set — the same twelve cues rendered with `send()` stubbed out ring
    for 35-165ms, and with the sends in place for 339-519ms — so 250ms sits in
    the gap and a mutation that unplugs the reverb fails here.
    """
    dead = {}
    for cue, (path, _) in renders.items():
        if cue.startswith("theme-"):
            continue                                 # a loop never stops to ring
        samples, rate = read_wav(path)
        ms = ring_out_ms(samples, rate)
        if ms < 250:
            dead[cue] = ms
    assert not dead, f"cues that stop dead instead of decaying into a room (ms): {dead}"


# --- the recorded readings (#357) --------------------------------------------
# The other half of "what the game sounds like": the story, the bios and the
# picker are read by a voice recorded at build time rather than by the device's
# robot. The clips are committed, so the thing that rots is the *prose* — a
# chapter retitled or a fun fact reworded leaves the manifest pointing at a
# recording of the old words, and nothing at runtime can tell. `--check` asks
# the question against the same files the game reads; running it here is what
# turns "somebody remembered" into a red suite.


def test_the_recordings_still_cover_the_prose_the_game_speaks():
    out = subprocess.run(
        [sys.executable, str(APP / "scripts" / "render_voices.py"), "--check"],
        capture_output=True, text=True)
    assert out.returncode == 0, (
        "the recordings and the game's words have drifted apart — run "
        "`python scripts/render_voices.py` to record what is missing:\n"
        + out.stdout + out.stderr)


def test_a_line_recorded_by_the_wrong_character_is_drift_too(capsys):
    """Recasting is the one kind of drift a file listing cannot show (#361).

    Every other answer `--check` gives is about a file: missing, gone, silent,
    orphaned. A line moved from one voice to another leaves the old clip on disk
    saying the right words in the wrong mouth, and the check above would call
    that green forever. Asked of `check()` directly rather than through a
    hand-edited manifest on disk, because the manifest in the repo is the one the
    deploy ships.
    """
    spec = importlib.util.spec_from_file_location(
        "bluey_render_voices_check", APP / "scripts" / "render_voices.py")
    rv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rv)

    manifest = json.loads((APP / "public/data/voices.json").read_text())
    wanted = [(v["role"], t) for t, v in manifest["lines"].items()]
    assert rv.check(manifest, wanted) == 0, "the manifest as shipped is not clean"

    role, text = next((r, t) for r, t in wanted if r != "narrator")
    recast = [(("narrator" if r == role else r), t) for r, t in wanted]
    assert rv.check(manifest, recast) == 1, (
        f"{text!r} moved from {role} to the narrator and --check called it fine")
    assert "wrong voice" in capsys.readouterr().out
