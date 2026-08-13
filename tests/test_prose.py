"""The licensing prose, checked by the suite instead of by remembering.

`scripts/fetch_assets.py --check` proves every hand-written copy of the notice
(README, boot splash, the main.js fallback) still matches the one in
data/asset-credits.json, and that the README carries no claim that stopped being
true when the artwork shipped. It was a hand-run command, which is another way
of spelling "runs the day I write it": this file is what makes it a check.

It reads the working tree, not the deployed site, so it means the same thing
under --base-url — the tree is what produced the deploy.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent
SCRIPTS = [
    ("fetch_assets.py", "the artwork is credited and the notice has not drifted"),
    ("build_rigs.py", "the rigs still cut every render where they say they do"),
]


@pytest.mark.parametrize("script,what", SCRIPTS, ids=[s for s, _ in SCRIPTS])
def test_the_asset_scripts_self_check(script, what):
    proc = subprocess.run(
        [sys.executable, str(APP / "scripts" / script), "--check"],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, f"{script} --check ({what}) failed:\n{proc.stdout}{proc.stderr}"


# ------------------------------------- and that check can still fail (#156)
# `--check` passing above is only worth something if a wrong box would make it
# fail. The pixel half of it is the part with no other safety net: the geometry
# rules are also asserted by the e2e suite, but nothing else in this repo can
# tell an eye box on an eye from one on a snout — the blink test builds its
# region of interest out of the very box it is checking.

@pytest.fixture(scope="module")
def rigs():
    """build_rigs as a module, plus the rigs it wrote — loaded by path.

    `scripts/` is not a package and not on the path; importing by name would
    depend on which directory pytest was started from.
    """
    spec = importlib.util.spec_from_file_location("build_rigs", APP / "scripts" / "build_rigs.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, json.loads(mod.RIGS.read_text())["rigs"]


def moved(rig: dict, i: int, dx: float, dy: float) -> dict:
    """The same rig with one eye box slid across the artwork."""
    box = rig["eyes"][i]
    w, h = box[2] - box[0], box[3] - box[1]
    return {**rig, "eyes": [[box[0] + dx * w, box[1] + dy * h,
                             box[2] + dx * w, box[3] + dy * h]]}


def test_a_box_slid_onto_the_muzzle_is_not_eye_white(rigs):
    """Bluey's left eye, dropped just over one box height onto his snout."""
    mod, rr = rigs
    from PIL import Image
    im = Image.open(mod.ASSETS / "bluey.png").convert("RGBA")
    problem, = mod.eye_problems("bluey", moved(rr["bluey"], 0, 0, 1.2), im)
    assert "eye0" in problem and "near-white" in problem, problem


def test_a_white_patch_with_no_pupil_in_it_is_not_an_eye(rigs):
    """The other half: plenty white, no ink. Bingo's muzzle is 80% white."""
    mod, rr = rigs
    from PIL import Image
    im = Image.open(mod.ASSETS / "bingo.png").convert("RGBA")
    problem, = mod.eye_problems("bingo", moved(rr["bingo"], 0, 0, 2), im)
    assert "no pupil" in problem, problem


def test_ink_scattered_along_an_outline_is_not_a_pupil(rigs):
    """The pupil is the biggest *connected* blot, and that word does work.

    `eye_pixels` could have counted every dark pixel in the box, and the two
    tests above would not know the difference. But a box straddling a character's
    outline collects plenty of ink in a thin line, and a shifted box does that
    all over this artwork: sweeping every stored box a few box-widths in each
    direction turns up 144 positions that hold enough total ink to clear the
    floor without one blot of it being a pupil. Bingo's left eye, up and to the
    right onto his brow line, is one of them.
    """
    mod, rr = rigs
    from PIL import Image
    im = Image.open(mod.ASSETS / "bingo.png").convert("RGBA")
    rig = moved(rr["bingo"], 0, 0.75, -0.5)
    box = rig["eyes"][0]

    # Not vacuous: by the module's own is_dark, this box is over twice as dark
    # as the floor asks for, so what rejects it is connectivity and not amount.
    w, h = im.size
    px = im.load()
    dark = sum(mod.is_dark(px[x, y])
               for y in range(round(box[1] * h), round(box[3] * h))
               for x in range(round(box[0] * w), round(box[2] * w)))
    area = round((box[2] - box[0]) * w) * round((box[3] - box[1]) * h)
    assert dark / area > mod.EYE_PUPIL * 2, f"only {dark / area:.1%} ink; nothing to connect"

    problem, = mod.eye_problems("bingo", rig, im)
    assert "no pupil" in problem, problem


def test_every_stored_box_passes_with_room_to_spare(rigs):
    """The thresholds are floors under the artwork, not fitted to it.

    A check tuned until the current data just passes fails on the next
    character for no reason anyone can act on. Bluey's own left eye is the
    tightest box there is — 38% white against a 30% floor, 1.28x — because his
    eye whites carry a big pupil and a heavy outline; everyone else is 1.5x or
    better. If this fails, the floor moved towards the data.
    """
    mod, rr = rigs
    from PIL import Image
    tight = []
    for cid, r in sorted(rr.items()):
        if not r.get("eyes"):
            continue
        im = Image.open(mod.ASSETS / f"{cid}.png").convert("RGBA")
        for i, box in enumerate(r["eyes"]):
            white, pupil, _ = mod.eye_pixels(im, box)
            if white < mod.EYE_WHITE * 1.2 or pupil < mod.EYE_PUPIL * 1.2:
                tight.append(f"{cid} eye{i}: white {white:.2f}, pupil {pupil:.2f}")
    assert not tight, "boxes sitting on the threshold rather than clear of it:\n" + "\n".join(tight)


def test_a_bad_box_makes_the_command_itself_exit_non_zero(rigs, tmp_path, monkeypatch):
    """The exit code is what `test_the_asset_scripts_self_check` and ship read."""
    mod, rr = rigs
    doc = json.loads(mod.RIGS.read_text())
    doc["rigs"]["bluey"] = moved(rr["bluey"], 0, 0, 1.2)
    bad = tmp_path / "rigs.json"
    bad.write_text(json.dumps(doc))
    monkeypatch.setattr(mod, "RIGS", bad)
    assert mod.check() == 1


def test_a_box_with_no_artwork_to_read_it_on_is_a_problem(rigs, tmp_path, monkeypatch, capsys):
    """Not-checked must not read as checked-and-clean.

    Every other test here hands `eye_problems` an image. The branch that runs
    when the sprite is missing is the one that decides what silence means, and
    the honest answer is "these boxes were not looked at" — a fetch that half
    failed would otherwise make `--check` quieter than it was before, which is
    the shape of a passing run.
    """
    mod, _ = rigs
    monkeypatch.setattr(mod, "ASSETS", tmp_path)          # every sprite now missing
    assert mod.check() == 1
    out = capsys.readouterr().out
    assert "0 eye box(es) read back" in out, out
    assert "has eye boxes but no sprite" in out, out
