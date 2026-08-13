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
import re
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


def moved_lid(rig: dict, dx: float, dy: float) -> dict:
    """The same rig with its lid patch slid across the artwork."""
    box = rig["lid"]
    w, h = box[2] - box[0], box[3] - box[1]
    return {**rig, "lid": [box[0] + dx * w, box[1] + dy * h,
                           box[2] + dx * w, box[3] + dy * h]}


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


# ------------------------------------------------ the lid patch, likewise (#140)
# The eyelid is drawn by averaging this patch to one colour, so the patch has to
# *be* one colour of face. Two of the 23 stored ones were not, and neither the
# geometry rules nor the eye rules could see it: a patch half off the side of a
# head is still above the neck, clear of the eyes and on the image.

def test_a_lid_patch_slid_off_the_head_is_a_problem(rigs):
    """Bluey's lid, two box-widths out past his ear, on empty canvas.

    Flatness alone would call this the best patch on the sheet — transparent
    background has no colour variation at all — and the blink would be painted
    in whatever RGBA(0,0,0,0) averages to. The alpha rule is what rejects it.
    """
    mod, rr = rigs
    from PIL import Image
    im = Image.open(mod.ASSETS / "bluey.png").convert("RGBA")
    rig = moved_lid(rr["bluey"], 2, 0)

    spread, thin, area = mod.patch_stats(im, rig["lid"])
    assert spread == 0 and thin == area, f"wanted flat and off-body, got {spread=} {thin=} {area=}"

    problem, = mod.lid_problems("bluey", rig, im)
    assert "off the character" in problem, problem


def test_a_lid_patch_on_a_marking_is_not_plain_face(rigs):
    """Bingo's lid, one box-width along, onto the edge of her ear.

    Fully opaque, so the alpha rule above has nothing to say: what rejects it is
    that it is two colours, and the average of two colours is a third one that is
    on the character nowhere.
    """
    mod, rr = rigs
    from PIL import Image
    im = Image.open(mod.ASSETS / "bingo.png").convert("RGBA")
    rig = moved_lid(rr["bingo"], 1, 0)

    spread, thin, _ = mod.patch_stats(im, rig["lid"])
    assert not thin, "this patch is off the head, so it proves the wrong rule"
    assert spread > mod.LID_SPREAD

    problem, = mod.lid_problems("bingo", rig, im)
    assert "straddles" in problem, problem


def bingo_old_lid() -> list[float]:
    """Bingo's patch before #140 moved it, read out of the comment that records it.

    The coordinates live in `build_rigs.py` next to the box that replaced them.
    Typing them again here would make this test agree with my copy of the
    history rather than with the file's.
    """
    src = (APP / "scripts" / "build_rigs.py").read_text()
    m = re.search(r"# was (\[[^\]]+\]), on the outline under her right eye", src)
    assert m, "build_rigs.py no longer records the box this test is about"
    return json.loads(m.group(1))


def test_the_spread_is_summed_over_channels_not_taken_per_channel(rigs):
    """The real defect only fails because the three channels are added up.

    431 of the 462 pixels in Bingo's old patch are her coat; the rest are the
    dark outline under her eye. Coat and outline differ by 25, 40 and 38 — so
    with a per-channel maximum against the same threshold, the patch that
    motivated this whole check would have passed, and only the sum, 103, catches
    it. That is the arithmetic this test pins.
    """
    mod, rr = rigs
    from PIL import Image
    lid = bingo_old_lid()
    im = Image.open(mod.ASSETS / "bingo.png").convert("RGBA")
    w, h = im.size
    px = im.load()
    vals = [px[i, j]
            for j in range(round(lid[1] * h), round(lid[3] * h))
            for i in range(round(lid[0] * w), round(lid[2] * w))]
    per = [max(v[c] for v in vals) - min(v[c] for v in vals) for c in range(3)]
    assert max(per) < mod.LID_SPREAD, f"worst channel is {max(per)}; the sum is not what catches it"

    problem, = mod.lid_problems("bingo", {**rr["bingo"], "lid": lid}, im)
    assert "straddles" in problem, problem


def test_a_patch_too_small_to_average_says_so(rigs):
    """The degenerate case, which otherwise reads as the flattest patch of all.

    A one-pixel patch has a colour spread of zero and no see-through pixels, so
    both rules above wave it through — and the lid it draws is invisible. This
    is the branch that stops "nothing to disagree about" being mistaken for
    agreement.
    """
    mod, rr = rigs
    from PIL import Image
    im = Image.open(mod.ASSETS / "bluey.png").convert("RGBA")
    lid = rr["bluey"]["lid"]
    rig = {**rr["bluey"], "lid": [lid[0], lid[1], lid[0] + 0.002, lid[1] + 0.002]}

    spread, thin, area = mod.patch_stats(im, rig["lid"])
    assert not spread and not thin, "the other two rules must have nothing to say here"

    problem, = mod.lid_problems("bluey", rig, im)
    assert "too small to average" in problem, problem


def test_every_stored_lid_patch_is_flat_with_room_to_spare(rigs):
    """The threshold is a floor under the artwork, not a line fitted to it.

    All 23 patches are opaque, and the least flat of them — Jean-Luc's cheek —
    spans 6 against a limit of 60. Anything that shows up here is either a patch
    creeping onto a marking or a threshold that has been dragged down to meet
    one.
    """
    mod, rr = rigs
    from PIL import Image
    tight = []
    for cid, r in sorted(rr.items()):
        if not r.get("lid"):
            continue
        im = Image.open(mod.ASSETS / f"{cid}.png").convert("RGBA")
        spread, thin, area = mod.patch_stats(im, r["lid"])
        if thin or spread * 4 > mod.LID_SPREAD:
            tight.append(f"{cid}: spans {spread} of colour, {thin} of {area} pixels see-through")
    assert not tight, "lid patches near the limit rather than clear of it:\n" + "\n".join(tight)


def test_a_bad_lid_makes_the_command_itself_exit_non_zero(rigs, tmp_path, monkeypatch):
    """`check()` has to actually call the lid half.

    Every test above hands `lid_problems` its arguments directly, so all of them
    pass just as well with the call deleted out of `check()` — and `check()`'s
    exit code is the only thing ship and the self-check test ever read. The eye
    half has the same test for the same reason.
    """
    mod, rr = rigs
    doc = json.loads(mod.RIGS.read_text())
    doc["rigs"]["bluey"] = moved_lid(rr["bluey"], 2, 0)
    bad = tmp_path / "rigs.json"
    bad.write_text(json.dumps(doc))
    monkeypatch.setattr(mod, "RIGS", bad)
    assert mod.check() == 1


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
    assert "0 face box(es) read back" in out, out
    assert "has face boxes but no sprite" in out, out
