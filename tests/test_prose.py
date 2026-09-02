"""The licensing prose, checked by the suite instead of by remembering.

`scripts/fetch_assets.py --check` proves every hand-written copy of the notice
(README, boot splash, the main.js fallback) still matches the one in
data/asset-credits.json, and that the README carries no claim that stopped being
true when the artwork shipped. It was a hand-run command, which is another way
of spelling "runs the day I write it": this file is what makes it a check.

It reads the working tree, not the deployed site, so it means the same thing
under --base-url — the tree is what produced the deploy.
"""

import collections
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(APP / "scripts"))
from js_source import code_only, function_body, object_block, object_literal  # noqa: E402
from render_audio import THEMES  # noqa: E402

SCRIPTS = [
    ("fetch_assets.py", "the artwork is credited and the notice has not drifted"),
    ("build_rigs.py", "the rigs still cut every render where they say they do"),
    ("build_pose_joints.py", "every stride render still has a hip on a hip"),
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


# ------------------------------------------ and the joints can still fail (#226)
# `--check` used to ask of neck and hip only that they were in order and inside
# the unit interval, which is true of almost any pair of numbers. #220 replaced
# Muffin's base render — different pose, different aspect ratio — and her stored
# 0.50/0.78, measured on the old drawing, rode through a build and a ship in
# silence. A joint at the wrong height does not throw: it shears the character
# at the waist.
#
# The read-back splits by where a number came from. A derived rig is `derive()`'s
# answer to its own render, so it is asked again. A hand-measured one cannot be:
# `test_the_hand_measured_joints_could_not_have_been_derived` below is the
# measurement that says so, and the stamp is what stands in for it.

# The render Muffin's 0.50/0.78 were measured on, replaced by #220:
#   git show 940a6e8~1:apps/bluey-game/public/assets/characters/muffin.png | sha256sum
OLD_MUFFIN = "f888506a8aba"


def rewritten(mod, monkeypatch, tmp_path, cid: str, **changes) -> None:
    """Point `check()` at rigs.json with one character's entry edited.

    Through monkeypatch, because the module is loaded once for the file: a
    `RIGS` left pointing at a tmp_path that pytest has since deleted would fail
    the next test in the file rather than this one.
    """
    doc = json.loads(mod.RIGS.read_text())
    doc["rigs"][cid].update(changes)
    bad = tmp_path / "rigs.json"
    bad.write_text(json.dumps(doc))
    monkeypatch.setattr(mod, "RIGS", bad)


def whether_the_joints_were_read_back_at_all(mod, cid: str) -> str:
    """The read-back never called, or called with the stamp comparison dead (#841).

    `check()` answering 0 says only that nothing went red, which is the same silence
    whether `joint_problems` was skipped over or ran and had nothing to object to.
    Asked directly — same rig, same drawing, one call further in — it separates them:
    a cut caller leaves the function itself still able to see the mismatch.
    """
    from PIL import Image

    r = json.loads(mod.RIGS.read_text())["rigs"][cid]
    f = mod.ASSETS / f"{cid}.png"
    said = mod.joint_problems(cid, r, f, Image.open(f).convert("RGBA"))
    if said:
        return "joint_problems still says so asked directly, so its caller is what stopped"
    return "joint_problems says nothing asked directly, so the comparison itself is gone"


def test_the_pre_220_muffin_joints_are_rejected_by_the_current_render(
        rigs, tmp_path, monkeypatch, capsys):
    """The acceptance case, with the real pair of drawings.

    Muffin's joints as they were before #220, against Muffin as she is now. The
    numbers themselves are unremarkable — 0.05 < 0.50 < 0.78 < 1.0, her head is
    45% of the ink above 0.50, her legs 19% of it below 0.78, and both sit inside
    every bound the corpus can support — so what has to notice is that they were
    measured on a different picture.
    """
    mod, _ = rigs
    assert OLD_MUFFIN != mod.sprite_stamp(mod.ASSETS / "muffin.png"), \
        "the two renders would have to differ for this test to be about anything"
    rewritten(mod, monkeypatch, tmp_path, "muffin", neck=0.50, hip=0.78, measuredOff=OLD_MUFFIN)

    got = mod.check()
    out = capsys.readouterr().out
    assert got == 1, (
        "a rig measured off a different muffin.png was not reported; "
        + whether_the_joints_were_read_back_at_all(mod, "muffin"))
    problems = [l for l in out.splitlines() if "PROBLEM" in l]
    assert len(problems) == 1, out                      # nobody else goes red
    assert "muffin" in problems[0] and "neck=0.5 hip=0.78" in problems[0], problems[0]
    assert "measured off a different muffin.png" in problems[0], problems[0]
    assert OLD_MUFFIN in problems[0] and "1bd2113e716b" in problems[0], problems[0]


def test_a_hand_measured_rig_that_names_no_artwork_is_a_problem(
        rigs, tmp_path, monkeypatch, capsys):
    """Absent must not read as checked-and-clean.

    Every rig in this repo was in this state until #226: nine hand-measured
    numbers with nothing recording which drawing anybody measured them on. The
    honest answer is that they have never been checked, not that they are fine.
    """
    mod, _ = rigs
    rewritten(mod, monkeypatch, tmp_path, "bluey", measuredOff=None)

    assert mod.check() == 1
    problem, = [l for l in capsys.readouterr().out.splitlines() if "PROBLEM" in l]
    assert "bluey" in problem and "name no artwork" in problem, problem
    assert 'measuredOff="b646f3838be9"' in problem, problem   # the value to paste


def test_a_derived_joint_is_re_derived_from_the_render(rigs, tmp_path, monkeypatch, capsys):
    """The other half: 16 of the 25 have no stamp because they need none.

    Their numbers are what `derive()` says about the render, so a render swapped
    in without a rebuild is the whole distance between the two. Socks' hip moved
    by 0.05 here — a fifth of the way up her belly.
    """
    mod, rr = rigs
    assert rr["socks"]["derived"], "this test is about a derived rig"
    rewritten(mod, monkeypatch, tmp_path, "socks", hip=round(rr["socks"]["hip"] - 0.05, 4))

    assert mod.check() == 1
    problems = [l for l in capsys.readouterr().out.splitlines() if "PROBLEM" in l]
    assert any("socks" in p and "rigs.json is older than socks.png" in p
               for p in problems), problems


def test_a_leg_pivot_is_read_back_off_the_row_it_came_from(rigs, tmp_path, monkeypatch, capsys):
    """The count was checked before; the positions were not.

    Two pivots on a two-legged dog is true of a rig whose legs are both in the
    wrong place, and the band below the hip turns about them.
    """
    mod, rr = rigs
    moved_legs = [rr["bluey"]["legPivots"][0] - 0.08, rr["bluey"]["legPivots"][1]]
    rewritten(mod, monkeypatch, tmp_path, "bluey", legPivots=moved_legs)

    assert mod.check() == 1
    problems = [l for l in capsys.readouterr().out.splitlines() if "PROBLEM" in l]
    assert any("bluey" in p and "leg pivot 0" in p and "off the leg" in p
               for p in problems), problems


def test_a_hip_below_the_leg_split_holds_no_leg(rigs, tmp_path, monkeypatch, capsys):
    """The one silhouette landmark that survives the corpus.

    It is asked of everybody, hand-measured or not: whatever the neck is doing,
    a hip below the crotch cuts the character mid-thigh and swings half a leg.
    """
    mod, rr = rigs
    rewritten(mod, monkeypatch, tmp_path, "muffin", hip=0.90)   # her split tops out at 0.814

    assert mod.check() == 1
    problems = [l for l in capsys.readouterr().out.splitlines() if "PROBLEM" in l]
    assert any("muffin" in p and "below the top of the leg split" in p
               for p in problems), problems


def test_a_silhouette_that_never_splits_says_so_rather_than_placing_a_hip(rigs):
    """A third answer, not "the split is at the feet".

    Every character in the repo today does split, so this branch is reached with
    a drawing rather than found in the corpus: a solid rectangle, the shape of a
    toy with a single base. `derive()` clamps its way to a hip regardless — the
    check must not read that clamp back as a landmark it verified.
    """
    mod, rr = rigs
    from PIL import Image
    im = Image.new("RGBA", (100, 200), (0, 0, 0, 255))
    rig = {**rr["bluey"], "derived": True, "legPivots": [0.5], "hip": 0.8, "neck": 0.4}

    problems = mod.joint_problems("slab", rig, mod.ASSETS / "bluey.png", im)
    assert any("never splits into two legs" in p for p in problems), problems


def test_the_hand_measured_joints_could_not_have_been_derived(rigs):
    """Why the nine carry a stamp instead of a read-back.

    This is the measurement the design rests on: `derive()`'s neck is not
    approximately these numbers, it is wrong by up to 0.163 — a cut through
    Bluey's forehead. A tolerance loose enough to accept that is loose enough to
    accept Muffin's stale 0.50, so there is nothing to read back and the
    question becomes which drawing was measured. If this ever fails, the
    silhouette rule has become good enough for these characters and the stamps
    can be replaced by the same comparison the other 16 get.
    """
    mod, rr = rigs
    from PIL import Image
    close = []
    for cid in mod.OVERRIDES:
        d = mod.derive(Image.open(mod.ASSETS / f"{cid}.png").convert("RGBA"))
        gap = max(abs(rr[cid][j] - d[j]) for j in ("neck", "hip"))
        if gap <= mod.JOINT_TOL:
            close.append(f"{cid}: derived within {gap:.4f} of the hand-measured joints")
    assert not close, "these no longer need hand measuring:\n" + "\n".join(close)


def test_every_stored_joint_reads_back_with_room_to_spare(rigs):
    """The tolerances are slack, not a line fitted to the data.

    Today every derived joint and every one of the 47 leg pivots reads back to
    0.0000 against a 0.01 tolerance, so anything showing up here is a tolerance
    that has been dragged out to meet a number that moved.
    """
    mod, rr = rigs
    from PIL import Image
    tight, checked = [], 0
    for cid, r in sorted(rr.items()):
        im = Image.open(mod.ASSETS / f"{cid}.png").convert("RGBA")
        d = mod.derive(im)
        for i, (a, b) in enumerate(zip(r["legPivots"], d["legPivots"])):
            checked += 1
            if abs(a - b) > mod.LEG_TOL / 2:
                tight.append(f"{cid}: leg pivot {i} reads back {abs(a - b):.4f} away")
        if r.get("derived"):
            for j in ("neck", "hip"):
                if abs(r[j] - d[j]) > mod.JOINT_TOL / 2:
                    tight.append(f"{cid}: {j} reads back {abs(r[j] - d[j]):.4f} away")
    assert checked >= 25, f"only {checked} pivots read back"
    assert not tight, "joints near the tolerance rather than clear of it:\n" + "\n".join(tight)


def test_every_stamp_is_the_render_that_is_on_disk(rigs):
    """The nine stamps are hand-pasted, so this is the one that catches a typo.

    `--check` says the same thing, but it says it about rigs.json; this says it
    about `OVERRIDES`, which is where a re-measurement is actually written.
    """
    mod, _ = rigs
    wrong = [f"{cid}: OVERRIDES says {o['measuredOff']}, "
             f"{cid}.png is {mod.sprite_stamp(mod.ASSETS / f'{cid}.png')}"
             for cid, o in mod.OVERRIDES.items()
             if o.get("measuredOff") != mod.sprite_stamp(mod.ASSETS / f"{cid}.png")]
    assert not wrong, "\n".join(wrong)
    assert len(mod.OVERRIDES) == 9 and all("measuredOff" in o for o in mod.OVERRIDES.values())


def test_a_rig_with_no_sprite_says_its_joints_were_not_read_back(rigs, tmp_path, monkeypatch,
                                                                 capsys):
    """A half-failed fetch must not make `--check` quieter than it was."""
    mod, _ = rigs
    monkeypatch.setattr(mod, "ASSETS", tmp_path)          # every sprite now missing
    assert mod.check() == 1
    out = capsys.readouterr().out
    assert "0 set(s) of joints" in out, out
    assert "no sprite, so its joints were not read back" in out, out


# ------------------------------------ and the pose joints can still fail (#212)
# A stride render is cut at the hip and the band below it swings, so the hip is
# load-bearing: put it through the belly and the dog tears in half, put it below
# the knees and it wags a foot. The e2e suite proves the swing happens and stays
# under the hip; only this can say the hip is on a hip, because it is the only
# thing here that reads the artwork.

@pytest.fixture(scope="module")
def joints():
    """build_pose_joints as a module, plus the joints it wrote — loaded by path."""
    spec = importlib.util.spec_from_file_location(
        "build_pose_joints", APP / "scripts" / "build_pose_joints.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, json.loads(mod.JOINTS.read_text())["joints"]


def with_joints(mod, monkeypatch, tmp_path, edit) -> list[str]:
    """`--check` run against the shipped joints with one of them edited."""
    doc = json.loads(mod.JOINTS.read_text())
    doc["joints"] = edit(dict(doc["joints"]))
    bad = tmp_path / "pose-joints.json"
    bad.write_text(json.dumps(doc))
    monkeypatch.setattr(mod, "JOINTS", bad)
    return mod.check()


@pytest.mark.parametrize("hip,why", [(0.30, "up in the chest"), (0.95, "down at the ankles")])
def test_a_hip_that_is_not_on_a_hip_is_a_problem(joints, tmp_path, monkeypatch, hip, why):
    mod, stored = joints
    rel = "assets/poses/bluey-run-0.png"
    problems = with_joints(mod, monkeypatch, tmp_path,
                           lambda j: {**j, rel: {**j[rel], "hip": hip}})
    assert problems, f"a hip {why} ({hip}) passed --check"


def test_a_pivot_off_the_body_is_a_problem(joints, tmp_path, monkeypatch):
    """The band turns about this point. Off the drawing, it swings the legs on
    the end of an invisible arm."""
    mod, _ = joints
    rel = "assets/poses/bluey-run-0.png"
    problems = with_joints(mod, monkeypatch, tmp_path,
                           lambda j: {**j, rel: {**j[rel], "pivot": 0.02}})
    assert any("pivot is off the body" in p for p in problems), problems


def test_a_stride_render_with_no_hip_at_all_is_a_problem(joints, tmp_path, monkeypatch):
    """It would still draw — whole, and still. A run that quietly stops cycling
    is exactly the failure #212 was filed about, so silence is not an option."""
    mod, _ = joints
    rel = "assets/poses/bluey-run-0.png"
    problems = with_joints(mod, monkeypatch, tmp_path,
                           lambda j: {k: v for k, v in j.items() if k != rel})
    assert any(rel in p and "legs cannot swing" in p for p in problems), problems


def test_a_hip_on_a_render_nothing_draws_is_a_problem(joints, tmp_path, monkeypatch):
    """The other direction: data left behind after a frame is replaced."""
    mod, _ = joints
    problems = with_joints(mod, monkeypatch, tmp_path,
                           lambda j: {**j, "assets/poses/gone-run-0.png": {"hip": 0.75, "pivot": 0.5}})
    assert any("gone-run-0.png" in p for p in problems), problems


def test_a_bad_joint_makes_the_command_itself_exit_non_zero(joints, tmp_path, monkeypatch):
    """The exit code is what `test_the_asset_scripts_self_check` and ship read."""
    mod, _ = joints
    rel = "assets/poses/bluey-run-0.png"
    doc = json.loads(mod.JOINTS.read_text())
    doc["joints"][rel] = {**doc["joints"][rel], "hip": 0.30}
    bad = tmp_path / "pose-joints.json"
    bad.write_text(json.dumps(doc))
    monkeypatch.setattr(mod, "JOINTS", bad)
    monkeypatch.setattr(sys, "argv", ["build_pose_joints.py", "--check"])
    assert mod.main() == 1


def test_every_stored_joint_passes_with_room_to_spare(joints):
    """The bounds --check holds are only worth something if the shipped numbers
    are not sitting on them. Measured today: 9%-18% of the drawing below the
    hip against a 35% ceiling, and 89%-100% of the hip row in one run."""
    mod, stored = joints
    for rel, j in sorted(stored.items()):
        m = mod.measure(mod.APP / "public" / rel, j["hip"])
        assert m["below"] < mod.BELOW_MAX * 0.7, f"{rel}: {m['below']:.0%} below the hip"
        assert m["below"] > mod.BELOW_MIN * 1.5, f"{rel}: only {m['below']:.0%} below the hip"
        assert m["joined"] > mod.JOINED + 0.15, f"{rel}: hip row is {m['joined']:.0%} one run"


# --------------------------------------------- the game has one name (#164)
# It was renamed from "For Real Life!" to "Ana Bingo!", and the name had eight
# hand-written copies across five files — a <title>, a meta description, an
# <h1>, a package description, a README heading, a server log line and two
# docstrings. Nothing would have failed if one had been missed, and a half-done
# rename reads as a different site in the tab than in the menu.

@pytest.fixture(scope="module")
def fetch_assets():
    """fetch_assets as a module, loaded by path for the same reason as `rigs`."""
    spec = importlib.util.spec_from_file_location(
        "fetch_assets", APP / "scripts" / "fetch_assets.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def a_readme(tmp_path, body: str, mod):
    """A stand-in README with a correct heading, plus whatever `body` says."""
    p = tmp_path / "README.md"
    p.write_text(f"# {mod.GAME_NAME}\n\n{body}\n")
    return p


def test_a_copy_of_the_old_name_left_behind_is_a_problem(fetch_assets, tmp_path, monkeypatch):
    mod = fetch_assets
    monkeypatch.setattr(mod, "README", a_readme(tmp_path, "Welcome to For Real Life!", mod))
    problem, = [p for p in mod.name_problems() if "old name" in p]
    assert "README.md:3" in problem, problem


def test_the_scan_reads_the_name_as_a_person_would(fetch_assets, tmp_path, monkeypatch):
    """The <h1> writes it "For Real&nbsp;Life!", which no plain substring finds.

    This is the copy that mattered — the menu heading is the biggest words on
    the screen — and it is the one written in a way the obvious scan misses.
    """
    mod = fetch_assets
    monkeypatch.setattr(mod, "README",
                        a_readme(tmp_path, "<h1>For Real&nbsp;Life!</h1>", mod))
    assert [p for p in mod.name_problems() if "old name" in p]


def test_a_sentence_may_say_the_old_name_if_it_says_why(fetch_assets, tmp_path, monkeypatch):
    """A phrase scan always ends up banning its own explanation.

    The README has to name "For Real Life!" once, to explain why the URL still
    does. Rewording that sentence until the scan is happy would be the check
    editing the docs; opting out with a stated reason is the check being told.
    """
    mod = fetch_assets
    line = f"It was For Real Life! <!-- {mod.OLD_NAME_OK} the URL still carries it -->"
    monkeypatch.setattr(mod, "README", a_readme(tmp_path, line, mod))
    assert not [p for p in mod.name_problems() if "old name" in p]


def test_the_opt_out_must_carry_a_reason(fetch_assets, tmp_path, monkeypatch):
    """...or it is a way to silence the check without saying anything."""
    mod = fetch_assets
    line = f"It was For Real Life! <!-- {mod.OLD_NAME_OK} -->".replace("-->", "")
    monkeypatch.setattr(mod, "README", a_readme(tmp_path, line, mod))
    problem, = [p for p in mod.name_problems() if "no reason" in p]
    assert "README.md:3" in problem, problem


def test_a_copy_that_disagrees_with_GAME_NAME_is_a_problem(fetch_assets, tmp_path, monkeypatch):
    """The other half: not a leftover, just wrong.

    A name scan can only find the name it knows about. Renaming the game a
    second time — or a typo in one copy — leaves no trace of "For Real Life!"
    anywhere, so the leftover scan above stays silent and every copy still has
    to be compared to the one that authored it.
    """
    mod = fetch_assets
    monkeypatch.setattr(mod, "README", a_readme(tmp_path, "nothing to see", mod).with_suffix(".x"))
    (tmp_path / "README.x").write_text("# Anna Bingo\n")
    problem, = [p for p in mod.name_problems() if "heading" in p]
    assert mod.GAME_NAME in problem and "Anna Bingo" in problem, problem


# ---------------------------- a playable character with no artwork for a state
# (#206) Muffin is on the character select screen, and the only drawing of her
# is the standing render, so every state the game can put her in was drawn by
# the rig — the sliced look the whole pose change was meant to end. Nothing
# noticed, because the pose tests are parametrised over what poses.json *has*:
# a character with nothing in it is a character with nothing to check.
#
# `coverage_problems` asks the other question — for every playable character and
# every state `poseFor` can be handed, is there a drawing? A gap is allowed, and
# some are unavoidable (there is no picture of Muffin off the ground anywhere),
# but it has to be written down with a reason in RIG_OK. These tests are what
# make that table load-bearing rather than decorative.

def posed(fetch_assets, tmp_path, monkeypatch, frames: dict):
    """Point the coverage check at a made-up poses.json."""
    p = tmp_path / "poses.json"
    p.write_text(json.dumps({"frames": frames}))
    monkeypatch.setattr(fetch_assets, "POSES_JSON", p)


def test_a_playable_character_with_no_art_for_a_state_is_a_problem(fetch_assets, monkeypatch):
    """The issue itself: drop Muffin's run out of the table and it comes back."""
    mod = fetch_assets
    monkeypatch.setattr(mod, "RIG_OK", {k: v for k, v in mod.RIG_OK.items()
                                        if k != ("muffin", "run")})
    problem, = [p for p in mod.coverage_problems() if p.startswith("muffin is playable")]
    assert "'run'" in problem and "RIG_OK" in problem, problem


def test_the_gap_is_reported_per_state_not_per_character(fetch_assets, monkeypatch):
    """Muffin has four gaps and they are four separate decisions — one of them
    could be closed tomorrow by one render. A single "muffin has no art" line
    would go on being true after the run landed."""
    mod = fetch_assets
    monkeypatch.setattr(mod, "RIG_OK", {})
    muffin = [p for p in mod.coverage_problems() if p.startswith("muffin is playable")]
    assert {"'run'", "'jump'", "'float'", "'cheer'", "'idle'"} == {
        p.split("for ")[1].split(",")[0] for p in muffin}


def test_a_declaration_the_artwork_has_made_untrue_is_a_problem(fetch_assets, tmp_path,
                                                                monkeypatch):
    """The direction that rots quietly. Someone finds a running Muffin, fetches
    it, and RIG_OK goes on saying the rig draws her — a comment explaining a
    limitation that no longer exists, which is how a table like this stops being
    read at all."""
    mod = fetch_assets
    posed(fetch_assets, tmp_path, monkeypatch,
          {"muffin": {"run": ["assets/poses/muffin-run-0.png"]}})
    problem, = [p for p in mod.coverage_problems() if p.startswith("muffin:run")]
    assert "drop the line" in problem, problem


def test_an_entry_that_gives_no_reason_is_a_problem(fetch_assets, monkeypatch):
    """An empty string is how a gap gets waved through in a hurry."""
    mod = fetch_assets
    monkeypatch.setattr(mod, "RIG_OK", {**mod.RIG_OK, ("muffin", "run"): "  "})
    problem, = [p for p in mod.coverage_problems() if "no reason" in p]
    assert problem.startswith("muffin:run"), problem


def test_an_entry_for_a_state_nothing_draws_is_a_problem(fetch_assets, monkeypatch):
    """States are `poseFor`'s cases, read out of sprites.js. Rename one and the
    line excusing it silently starts excusing nothing, leaving the real gap
    unreported — the same failure as a typo in the character id."""
    mod = fetch_assets
    monkeypatch.setattr(mod, "RIG_OK", {**mod.RIG_OK, ("muffin", "hover"): "no such state"})
    assert [p for p in mod.coverage_problems() if "'hover'" in p and "nothing draws" in p]


def test_an_entry_for_someone_who_cannot_be_played_is_a_problem(fetch_assets, monkeypatch):
    """Bandit is playable today. If he becomes scenery, his three lines are
    excusing gaps nobody can see, and they would outlive the reason they name."""
    mod = fetch_assets
    monkeypatch.setattr(mod, "RIG_OK", {**mod.RIG_OK, ("socks", "run"): "not on the roster"})
    assert [p for p in mod.coverage_problems() if "socks" in p and "not a playable" in p]


def test_the_blanket_idle_line_dies_when_it_stops_being_true(fetch_assets, tmp_path,
                                                             monkeypatch):
    """`("*", "idle")` covers everyone, so it cannot be caught by the per-pair
    check above — nothing would ever report it as untrue. Give every playable
    character an idle drawing and the line has to go.

    And *only* then: it is one line standing in for five entries, so it is out
    of date when the last of them is drawn, not the first. Asserting the death
    on its own passes an `any()`, which would tell whoever draws the first idle
    Bluey to delete the line still excusing the other four."""
    mod = fetch_assets
    playable = [c["id"] for c in mod.load_chars() if c.get("playable")]
    idle = {cid: {"idle": [f"assets/poses/{cid}-idle-0.png"]} for cid in playable}

    def blanket():
        return [p for p in mod.coverage_problems() if "'*'" in p and "'idle'" in p]

    for cid in playable[:-1]:  # all but one, so the line is still doing work
        posed(fetch_assets, tmp_path, monkeypatch, {c: idle[c] for c in playable if c != cid})
        assert not blanket(), f"the '*' line was called out of date while {cid} still needs it"

    posed(fetch_assets, tmp_path, monkeypatch, idle)
    assert blanket()


def test_a_state_drawn_by_another_states_art_counts_as_covered(fetch_assets, monkeypatch):
    """float has no artwork of its own anywhere and never will: it is the jump
    held down, drawn by the jump render — or, for a character with no jump of
    her own, by whatever that falls to in turn (POSE_FALLBACK in sprites.js).
    Nobody with any artwork at all therefore has a float gap, or a RIG_OK line
    for one. Take the fallback away and they all do — which is what pins this to
    the JS rather than to a second opinion about it kept here.

    Who that is comes from the shipped artwork rather than a list typed here, so
    the day someone is drawn for the first time she joins the expectation
    instead of turning this red."""
    mod = fetch_assets
    assert not [p for p in mod.coverage_problems() if "float" in p]
    frames = json.loads(mod.POSES_JSON.read_text())["frames"]
    drawn = {c["id"] for c in mod.load_chars() if c.get("playable")} & set(frames)
    monkeypatch.setattr(mod, "pose_fallbacks", dict)
    floats = [p for p in mod.coverage_problems() if "'float'" in p]
    assert {p.split()[0] for p in floats} == drawn, floats


def test_the_command_itself_exits_non_zero_on_an_undeclared_gap(fetch_assets, monkeypatch):
    """The signal ship.py and test_the_asset_scripts_self_check actually read."""
    mod = fetch_assets
    monkeypatch.setattr(mod, "RIG_OK", {})
    assert mod.check() == 1


# ------------------------------------ a shot run replaces its own shots (#164)
# `--prefix live` and `--prefix live-` have to name the same files. Given the
# first, shots.py used to write `live00-menu.png` next to the `live-00-menu.png`
# an earlier run left — a second, parallel set that no rerun ever overwrites and
# nothing tracks. It took a while to work out that the strays were mine.

@pytest.fixture(scope="module")
def shots():
    """shots.py as a module, loaded by path for the same reason as `rigs`."""
    spec = importlib.util.spec_from_file_location("shots", APP / "scripts" / "shots.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("given", ["live", "live-", "live_", "live."])
def test_a_prefix_ends_in_a_separator(shots, given):
    assert shots.separated(given)[-1] in "-_."
    assert shots.separated(given).startswith("live")
    assert len(shots.separated(given)) == 5


def test_no_prefix_stays_empty(shots):
    """The default names the shots the README points at: `00-menu.png`."""
    assert shots.separated("") == ""


def test_both_spellings_of_a_prefix_name_one_file(shots):
    assert shots.separated("live") == shots.separated("live-")


def test_the_walk_is_given_the_separated_prefix(shots):
    """Normalising and then not using it would be the same bug, silently.

    `separated` is called once, in main(); the two walk() calls have to be
    handed its result rather than the raw argparse value.
    """
    src = (APP / "scripts" / "shots.py").read_text()
    body = src[src.index("def main()"):]
    assert body.count("a.prefix") == 1, "the raw --prefix is used past main()"
    assert "separated(a.prefix)" in body
    assert re.search(r"walk\(page, url, prefix\)", body)
    assert re.search(r'walk\(mob, url, prefix \+ "mobile-"', body)


# ------------------------------------ the small copy of the artwork (#138)
# Every character ships twice: the PNG `normalise` wrote, and a WebP of the same
# picture that nearly every browser is the one actually sent. Which makes the
# WebP the file with no natural check on it — the e2e suite can load a stale,
# wrong or half-written one and see a character, the rigs are measured off the
# PNG, and a --check that only ever looked at PNGs would go on passing while the
# picture on the screen came from somewhere else.

@pytest.fixture
def one_character(fetch_assets, tmp_path, monkeypatch):
    """A public/ tree of its own with one character encoded both ways.

    Built rather than borrowed: these tests break the pair on purpose, and the
    real 25 are what the rest of the suite is asserting on.
    """
    from PIL import Image

    mod = fetch_assets
    out = tmp_path / "public" / "assets" / "characters"
    out.mkdir(parents=True)
    png = out / "bluey.png"
    im = Image.new("RGBA", (16, 16), (30, 120, 200, 255))
    for x in range(8):
        im.putpixel((x, 0), (0, 0, 0, 0))      # a silhouette to lose
    im.save(png, "PNG")
    monkeypatch.setattr(mod, "APP", tmp_path)
    return mod, png, {"assets": {"bluey": {"file": "assets/characters/bluey.png",
                                           **mod.encode_webp(png)}}}


def test_a_matching_pair_is_no_problem(one_character):
    """The healthy case, first: a check that cannot pass is not a check."""
    mod, _, credits = one_character
    assert mod.webp_problems(credits) == []


def test_a_character_with_no_small_copy_is_a_problem(one_character):
    mod, _, credits = one_character
    del credits["assets"]["bluey"]["webp"]
    problem, = mod.webp_problems(credits)
    assert "--webp" in problem, problem


def test_a_small_copy_that_is_not_on_disk_is_a_problem(one_character):
    mod, png, credits = one_character
    png.with_suffix(".webp").unlink()
    problem, = mod.webp_problems(credits)
    assert "missing on disk" in problem, problem


def test_a_small_copy_of_the_wrong_size_is_a_problem(one_character):
    """The credited byte count is what the gallery's transfer budget is spent
    in, and what the served-file test compares against."""
    mod, _, credits = one_character
    credits["assets"]["bluey"]["webp_bytes"] += 1
    problem, = mod.webp_problems(credits)
    assert "credited as" in problem, problem


def test_a_small_copy_made_from_an_older_png_is_a_problem(one_character):
    """The one that would really happen: a re-fetched character rewrites the
    PNG, the WebP beside it still shows the picture from before, and it is the
    WebP nearly everyone is served. Nothing else in the repo compares them.
    """
    from PIL import Image

    mod, png, credits = one_character
    Image.new("RGBA", (16, 16), (200, 30, 30, 255)).save(png, "PNG")
    problem, = mod.webp_problems(credits)
    assert "stale" in problem, problem


def test_a_small_copy_that_lost_the_silhouette_is_a_problem(one_character):
    """Every rig fraction, every cutout score and every blink box was measured
    off the PNG's alpha channel. WebP keeps alpha losslessly — that is why q90
    is safe at all — so this asserts the property rather than trusting it: a
    quality, a mode or an encoder that flattened the cut-out would move joints
    on twenty-five characters and nothing would say so.
    """
    from PIL import Image

    mod, png, credits = one_character
    small = png.with_suffix(".webp")
    Image.new("RGBA", (16, 16), (30, 120, 200, 255)).save(  # no transparent pixels
        small, "WEBP", quality=mod.WEBP_QUALITY, method=mod.WEBP_METHOD)
    entry = credits["assets"]["bluey"]
    entry["webp_bytes"] = small.stat().st_size            # so only the alpha is wrong
    problem, = mod.webp_problems(credits)
    assert "alpha" in problem, problem


# ------------------------------------ ...and of the pose frames (#238)
# The nine action renders got the second encoding a release later, and they are
# the ones a phone pulls at boot, for the cast about to race. Everything above
# walks `credits["assets"]`; these are the same guarantees over `credits["poses"]`,
# which is a separate block with separate entries — a checker that only ever knew
# about characters would report "alpha checked against each png" while 860KB of
# pose PNG shipped unencoded, which is exactly the state #238 was filed in.

@pytest.fixture
def one_pose(fetch_assets, tmp_path, monkeypatch):
    """The same tree, with a pose frame instead of a character render."""
    from PIL import Image

    mod = fetch_assets
    out = tmp_path / "public" / "assets" / "poses"
    out.mkdir(parents=True)
    png = out / "bluey-run-0.png"
    im = Image.new("RGBA", (16, 16), (30, 120, 200, 255))
    for x in range(8):
        im.putpixel((x, 0), (0, 0, 0, 0))      # the hip in pose-joints.json is
    im.save(png, "PNG")                        # a fraction of this silhouette
    monkeypatch.setattr(mod, "APP", tmp_path)
    return mod, png, {"poses": {"bluey:run:0": {"file": "assets/poses/bluey-run-0.png",
                                                **mod.encode_webp(png)}}}


def test_a_pose_frame_is_encoded_next_to_itself(one_pose):
    """Not into the characters directory, which is where the path used to be
    built from: a pose credited as `assets/characters/bluey-run-0.webp` 404s for
    every browser that prefers it, and the fallback is five failed tries away."""
    mod, png, credits = one_pose
    entry = credits["poses"]["bluey:run:0"]
    assert entry["webp"] == "assets/poses/bluey-run-0.webp", entry["webp"]
    assert png.with_suffix(".webp").exists()
    assert mod.webp_problems(credits) == [], "the healthy pair is already a problem"


def test_a_pose_frame_with_a_stale_small_copy_is_a_problem(one_pose):
    """A re-fetched or re-cropped pose rewrites the PNG — `bingo-jump-0` and
    `chilli-run-0` both were — and the WebP beside it goes on showing the frame
    from before to nearly every browser, with the joints still measured off the
    PNG nobody is sent."""
    from PIL import Image

    mod, png, credits = one_pose
    Image.new("RGBA", (16, 16), (200, 30, 30, 255)).save(png, "PNG")
    problem, = mod.webp_problems(credits)
    assert "stale" in problem, problem
    assert "bluey:run:0" in problem, problem


def test_a_pose_frame_that_lost_the_silhouette_is_a_problem(one_pose):
    """The pose equivalent of the rig argument, and the reason the alpha is
    compared per frame rather than assumed from 'WebP stores alpha losslessly':
    `pose-joints.json` cuts each run render at a hip measured off this channel,
    so an encoder that touched it would swing the legs from the wrong place."""
    from PIL import Image

    mod, png, credits = one_pose
    small = png.with_suffix(".webp")
    Image.new("RGBA", (16, 16), (30, 120, 200, 255)).save(  # no transparent pixels
        small, "WEBP", quality=mod.WEBP_QUALITY, method=mod.WEBP_METHOD)
    entry = credits["poses"]["bluey:run:0"]
    entry["webp_bytes"] = small.stat().st_size            # so only the alpha is wrong
    problem, = mod.webp_problems(credits)
    assert "alpha" in problem, problem


def test_the_check_counts_the_pairs_it_compared_not_the_files_on_disk(one_pose):
    """What `check()` prints is a report of what the loop did.

    `compared` is appended to at the end of the comparison, so a frame that fell
    out at any guard before it is absent from the count as well as named in a
    problem — otherwise the line says nine frames were checked while one of them
    was never opened.
    """
    mod, png, credits = one_pose
    compared = []
    assert mod.webp_problems(credits, compared) == []
    assert [(b, label) for b, label, _ in compared] == [("poses", "bluey:run:0")]
    assert compared[0][2] == png.with_suffix(".webp").stat().st_size

    png.with_suffix(".webp").unlink()
    compared = []
    assert mod.webp_problems(credits, compared), "a missing webp is not a problem"
    assert compared == [], "counted a pair it never opened"


def test_a_stale_small_copy_makes_the_command_itself_exit_non_zero(
        fetch_assets, tmp_path, monkeypatch, capsys):
    """`check()` has to actually call it, on the real 25.

    Every test above hands `webp_problems` its argument directly, so all of them
    pass just as well with the call deleted out of `check()` — and the exit code
    is the only thing `test_the_asset_scripts_self_check` and ship ever read.
    """
    mod = fetch_assets
    doc = json.loads(mod.CREDITS.read_text())
    doc["assets"]["bluey"]["webp_bytes"] += 1
    bad = tmp_path / "asset-credits.json"
    bad.write_text(json.dumps(doc))
    monkeypatch.setattr(mod, "CREDITS", bad)
    assert mod.check() == 1
    assert "bluey" in capsys.readouterr().out


def test_the_check_says_it_looked_at_the_small_copies(fetch_assets, capsys):
    """Not-checked must not read as checked-and-clean: a run that says nothing
    about the WebPs is indistinguishable from one that never looked.

    Both kinds of artwork, and the counts are asserted against the credits
    rather than against 25 and 9 typed here — the point is that the line reports
    every pair the checker walked, so a block it silently stopped walking shows
    up as a number that no longer matches (#238).
    """
    mod = fetch_assets
    credits = json.loads(mod.CREDITS.read_text())
    assert mod.check() == 0
    out = capsys.readouterr().out
    assert re.search(rf"\+ {len(credits['assets'])} webp, \d+KB, alpha checked "
                     r"against each png", out), out
    assert re.search(rf"\d+ pose frames of which {len(credits['poses'])} ship a webp "
                     r"too \(\d+KB, alpha checked the same way\)", out), out


def test_a_readme_that_misstates_what_the_artwork_costs_is_a_problem(
        fetch_assets, tmp_path, monkeypatch):
    """The two numbers in the layout line are the whole reason for shipping two
    encodings, and nothing else in the repo would ever correct them: they were
    typed once, off a measurement, and every later character makes them wronger.

    Every directory that ships two encodings, one line at a time (#238): the
    pose frames' line arrived long after the characters' one, and a check that
    only ever proved the first would have let the second in unmeasured. The
    line being broken is taken out of the real README, not retyped here.
    """
    mod = fetch_assets
    real = mod.README.read_text()
    credits = json.loads(mod.CREDITS.read_text())
    fake = tmp_path / "README.md"
    monkeypatch.setattr(mod, "README", fake)
    checked = []
    for directory, pattern in mod.README_SIZES:
        line = pattern.search(real).group(0)
        broken = re.sub(r"PNG \(~[\d.]+ MB\)", "PNG (~9.9 MB)", line)
        assert broken != line, f"nothing to break in {line!r}"
        fake.write_text(real.replace(line, broken))
        problem, = [p for p in mod.prose_problems(credits, 25) if "MB" in p]
        assert "9.9" in problem and "pngs" in problem, problem
        assert str(directory.relative_to(mod.APP)) in problem, problem
        checked.append(directory.name)
    assert checked == ["characters", "poses"], checked


def test_a_layout_line_that_stopped_naming_the_two_encodings_is_a_problem(
        fetch_assets, tmp_path, monkeypatch):
    """Not-stated must not read as correct. A README rewritten around the line
    would otherwise take the measurement with it and leave a passing check.

    Once per directory, and the problem has to name which line went missing:
    two silences that read the same are one silence (#238)."""
    mod = fetch_assets
    real = mod.README.read_text()
    credits = json.loads(mod.CREDITS.read_text())
    fake = tmp_path / "README.md"
    monkeypatch.setattr(mod, "README", fake)
    checked = []
    for directory, pattern in mod.README_SIZES:
        line = pattern.search(real).group(0)
        fake.write_text(real.replace(line, line.split("PNG")[0] + "the artwork"))
        where = str(directory.relative_to(mod.APP))
        assert [p for p in mod.prose_problems(credits, 25)
                if "no longer says" in p and where in p], where
        checked.append(directory.name)
    assert checked == ["characters", "poses"], checked


# --------------------------------- reading the JS as code and not as prose (#233)
#
# Four checks in this app derive a fact from JavaScript with a regex — the
# animation states, the fallback table, which states `frameMotion` swings, where
# the engine makes particles — and #231 was one of them answering about a
# comment. `scripts/js_source.py` is the one place that blanks comments and
# bounds a region now; these are its own tests, plus the one that keeps the
# four drills honest.

SRC_WITH_PROSE = '''\
const LINKS = ["https://example.com/a", 'not // a comment'];
// case "gone": this line is prose
const KEEP = { a: "b" };   /* and so is this: c: "d" */
/*
 * case "alsogone":
 */
const TAIL = "a \\" quote, // still a string";
'''


def test_a_comment_is_blanked_and_a_string_is_left_alone():
    """Both comment styles, and the two ways a `//` is not a comment: inside a
    string (main.js is a list of URLs) and after an escaped quote."""
    out = code_only(SRC_WITH_PROSE)
    assert "https://example.com/a" in out, "a URL in a string is not a comment"
    assert "'not // a comment'" in out
    assert '"a \\" quote, // still a string"' in out, "an escaped quote ended the string early"
    assert 'const KEEP = { a: "b" };' in out
    assert "gone" not in out and "prose" not in out and 'c: "d"' not in out


def test_the_blanking_keeps_the_shape_of_the_file():
    """Blanked, not deleted: a caller can search this and slice the original, and
    a line number still means what it said."""
    out = code_only(SRC_WITH_PROSE)
    assert len(out) == len(SRC_WITH_PROSE)
    assert out.splitlines(keepends=True).__len__() == \
        SRC_WITH_PROSE.splitlines(keepends=True).__len__()
    for i, (was, now) in enumerate(zip(SRC_WITH_PROSE.splitlines(), out.splitlines())):
        assert len(was) == len(now), f"line {i + 1} changed length"


def test_a_function_body_stops_at_its_own_closing_brace():
    """The bug this bounding is for: `states()` split the file at
    `function poseFor(` and kept everything after it — 777 lines of a 999-line
    file, `frameMotion`'s switch included."""
    src = (APP / "public" / "js" / "sprites.js").read_text()
    body = function_body(src, "poseFor")
    assert body.startswith("function poseFor(")
    assert "function frameMotion(" not in body, "the read ran past poseFor's own brace"
    assert len(body) < len(src) / 2, (
        f"poseFor's body is {len(body)} of {len(src)} characters, which is not a "
        "function — it is most of the file")


def test_a_nested_table_is_read_whole_and_not_to_its_first_inner_brace():
    """`object_block` exists because the non-greedy `\\{(.*?)\\};` that reads a
    flat table stops at the first `}` — on `audio.js`'s themes, whose entries
    are themselves objects, that is one entry of eleven, and a check over it
    would pass by only ever looking at `menu`."""
    src = (APP / "public" / "js" / "audio.js").read_text()
    block = object_block(src, "themes")
    assert block.count("motif:") == len(THEMES), (
        f"read {block.count('motif:')} motifs, not {len(THEMES)} — one per theme the "
        "chapters ask for, plus the menu's")
    assert THEMES[-1] in block, "the read stopped before the last theme"
    assert "playTheme" not in block.replace("themes[name]", ""), "the read ran past the table"
    quoted = object_block('const t = { a: { b: "{" }, c: { d: 1 } };\n', "t")
    assert "c:" in quoted, f"a brace inside a string opened a level that never closed: {quoted!r}"
    with pytest.raises(ValueError, match="THEMES"):
        object_block("const other = { a: 1 };\n", "THEMES")
    with pytest.raises(ValueError, match="never closed"):
        object_block("const t = { a: { b: 1 };\n", "t")


def test_a_region_that_is_not_there_is_reported_rather_than_read_as_empty():
    """An absent subject and an empty one must not print the same. Each of these
    silently returned nothing before, which reads as "there are no states" /
    "nothing falls back" — the answers that pass every check downstream."""
    with pytest.raises(ValueError, match="poseFor"):
        function_body("const x = 1;\n", "poseFor")
    with pytest.raises(ValueError, match="nested"):
        function_body("  function poseFor(a) {\n    return 1;\n  }\n", "poseFor")
    with pytest.raises(ValueError, match="POSE_FALLBACK"):
        object_literal("const OTHER = { a: \"b\" };\n", "POSE_FALLBACK")
    with pytest.raises(ValueError, match="no entries at all"):
        object_literal("const T = { /* a: \"b\" */ };\n", "T")


# Each of the four sites: what it derives now, and the naive parse it replaced.
# The point is not that the helper works — the tests above are that — but that
# each *caller* uses it, on a source that still says something the naive parse
# would fall for. Both halves have to hold for the drill to mean anything: a
# site that goes back to reading prose fails here, and so does a source that
# stopped containing any (at which point the mutation entry for it would score
# CAUGHT for some other reason).
def naive_states(src):
    return set(re.findall(r'case "(\w+)":', src.split("function poseFor(", 1)[-1]))


def naive_fallback(src):
    inside = re.search(r"const POSE_FALLBACK = \{(.*?)\};", src, re.S).group(1)
    return dict(re.findall(r'(\w+):\s*"(\w+)"', inside))


def naive_still_swung(src):
    cases = re.split(r'case "(\w+)":',
                     re.search(r"function frameMotion\(.*?\n\}", src, re.S).group(0))
    return tuple(s for s, body in zip(cases[1::2], cases[2::2]) if "swing: 0" in body)


def naive_particle_sites(src):
    """The site's own regex, on source whose comments are still in it.

    The factory list is taken from `test_game.FACTORIES` rather than written out
    here: this one drifted, and hard-coding three of the five was enough to make
    the drill below pass on a difference that had nothing to do with prose (#447).
    A twin that names its own subjects stops being a twin the moment the site
    learns a new one.
    """
    factories = game_and_scripts()[0].FACTORIES
    return sorted(re.findall(r"this\.(" + "|".join(factories) + r")\(", src))


SPRITES = APP / "public" / "js" / "sprites.js"
GAME = APP / "public" / "js" / "game.js"


def game_and_scripts():
    """(test_game, fetch_assets) as modules, for the collections they derive.

    Imported rather than restated: what this checks is the very value the tests
    over there are parametrised from and the value the coverage check counts.
    Module-level work only — no browser, no server; pytest has imported
    test_game already whenever the whole suite is running.
    """
    sys.path.insert(0, str(APP / "tests"))
    import test_game
    spec = importlib.util.spec_from_file_location(
        "fetch_assets_for_parses", APP / "scripts" / "fetch_assets.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return test_game, mod


PARSED_SITES = [
    ("the animation states (fetch_assets.states)", SPRITES, naive_states,
     lambda: {s for s in game_and_scripts()[1].states() if s != "idle"}),
    ("the fallback table (fetch_assets.pose_fallbacks)", SPRITES, naive_fallback,
     lambda: game_and_scripts()[1].pose_fallbacks()),
    ("the states frameMotion leaves alone (test_game.STILL_SWUNG)", SPRITES,
     naive_still_swung, lambda: game_and_scripts()[0].STILL_SWUNG),
    ("the particle call sites (test_game.PARTICLE_SITES)", GAME,
     lambda src: collections.Counter(naive_particle_sites(src)),
     lambda: game_and_scripts()[0].PARTICLE_SITES),
]


def twin_drift(naive, got, src: str) -> str:
    """Why `naive` has stopped being the site's own question, or "" if it has not.

    The drill below compares what a site derives against what a comment-reading
    parse of the same source would derive, and takes the difference as evidence
    the site ignores prose. That only follows if the two differ in *exactly* one
    way — the blanking. So: feed the naive parse source whose comments are already
    blanked, and it must land on the site's answer. #447 is what it looks like when
    it does not. The particle twin named three of the engine's five factories, the
    two it never named made the answers differ on their own, and `got != fooled`
    therefore held whatever the site did with comments — the site's mutation
    SURVIVED for months against a drill that could not fail.

    One function, called with the real sites here and with deliberately wrong ones
    in the drill next to it, so the check and its own proof ask the same question.
    """
    blanked = naive(code_only(src))
    if blanked == got:
        return ""
    return (f"with the comments blanked first, the naive parse says {blanked} where the "
            f"site says {got}. The two are no longer asking the same question, so the "
            "difference checked before this is coming from somewhere other than prose "
            "and the drill passes for free. Bring the twin back into line with the site "
            "— it should differ from it in exactly one way, the blanking.")


def test_a_twin_that_stopped_asking_the_sites_question_is_caught():
    """The #447 shape in miniature: a twin that names fewer subjects than the site.

    Both parses are given the same source, and the site's answer is what its own
    regex says with the comments gone. The twin below is narrowed the way the real
    one was — it looks for one of the two names — so it differs from the site for a
    reason that has nothing to do with the comment, which is exactly the difference
    the drill would otherwise read as proof.
    """
    src = 'this.puff();\n// a cloud bounce makes no this.puff()\nthis.gustMotes();\n'
    site = collections.Counter(re.findall(r"this\.(puff|gustMotes)\(", code_only(src)))
    faithful = lambda s: collections.Counter(re.findall(r"this\.(puff|gustMotes)\(", s))
    narrowed = lambda s: collections.Counter(re.findall(r"this\.(puff)\(", s))

    assert twin_drift(faithful, site, src) == "", (
        "a twin that asks the site's question of comment-free source and gets the "
        "site's answer was called drifted, so every site here would fail for nothing")
    complaint = twin_drift(narrowed, site, src)
    assert "gustMotes" in complaint, (
        f"a twin that stopped naming one of the site's subjects went unremarked "
        f"({complaint!r}) — this is #447, and it is the case where the drill above "
        "still passes while the site it guards is free to read prose again")


@pytest.mark.parametrize("what,path,naive,live", PARSED_SITES,
                         ids=[s[0].split(" (")[0] for s in PARSED_SITES])
def test_a_site_that_reads_the_javascript_reads_its_code_and_not_its_prose(
        what, path, naive, live):
    """Every one of the four reads a file whose comments talk *about* the thing
    it looks for: the typo warning in `poseFor`, the fallback `POSE_FALLBACK`
    deliberately does not have, the reason the cheer's legs stay put (#231), the
    dust a cloud bounce deliberately does not kick up.

    So the naive answer and the real one differ, and that difference is the only
    input a "this parse ignores prose" claim has. `states()` drops `idle` first:
    that one is read off the `default:` arm rather than off a case, and is not
    what either parse is being asked about here.
    """
    got, fooled = live(), naive(path.read_text())
    assert got != fooled, (
        f"{what}: what this site derives is now exactly what a parse that reads "
        f"{path.name}'s comments would derive ({fooled}). Either the site went back to "
        "reading prose — the #231 defect, and the mutation entry for it can no longer "
        "fail — or the comment that told the two apart is gone. Put back a comment that "
        "names what the code is not (see #233), or delete the site's mutation with the "
        "drill.")
    # Second, and second on purpose: a site that reads prose again fails the line
    # above with the #231 wording, and only a twin that has drifted reaches this one.
    drift = twin_drift(naive, got, path.read_text())
    assert not drift, f"{what}: {drift}"


# ------------------------- a screen shows itself before it reads itself (#301)
#
# `showOverlay` hushes on the way in, because a queued utterance outlives the
# screen that asked for it. That makes the order the four reading screens happen
# to be written in load-bearing: a screen that called `readAloud` first would
# silence itself, and the only sign would be a card that never speaks. It is a
# habit across four functions and nothing held it, so it is a rule here.
#
# Both lists, checked against each other (#320): the screens are *declared*
# below, because deciding that a screen reads itself is a decision and it should
# be written down where the reason is — and they are also *found* in the source,
# because a declaration alone only guards the direction it names. A fifth screen
# that learns to read and never arrives here would otherwise be checked by
# nothing at all, which is the failure this rule exists for, silently uncovered.

MAIN = APP / "public" / "js" / "main.js"
READING_SCREENS = ("storyCard", "results", "bio", "characterSelect")


def screens_that_read(src: str) -> set[str]:
    """Every top-level function in `src` whose body calls `readAloud`.

    Comments are blanked first (`function_body` does it), so the JSDoc above
    `readAloud` that talks about who calls it is not read as a caller — the #233
    defect, one file over. `readAloud` itself is left out: it is the thing being
    called, not a screen.
    """
    code = code_only(src)
    names = [m.group(1) for m in re.finditer(r"^function (\w+)\(", code, re.M)]
    return {n for n in names
            if n != "readAloud" and "readAloud(" in function_body(src, n)}


def reading_screen_problems(src: str, declared=READING_SCREENS) -> list[str]:
    """Everything wrong with how `src`'s screens read themselves, as sentences.

    One function so that the check and its own drill ask the same question of
    different sources: the real one is main.js, and the synthetic ones below are
    each written to be wrong in exactly one way.
    """
    found = screens_that_read(src)
    problems = [
        f"{name}() calls readAloud and is not in READING_SCREENS — a screen that "
        f"reads itself is a decision, and nobody made it here. If it should read "
        f"itself, declare it; the order rule then applies to it too (#320)"
        for name in sorted(found - set(declared))
    ] + [
        f"{name}() is declared as a screen that reads itself and calls no readAloud "
        f"— either it went silent (#293) or it was renamed, and this rule is now "
        f"about {len(declared) - 1} screens"
        for name in sorted(set(declared) - found)
    ]
    for name in declared:
        if name not in found:
            continue
        body = function_body(src, name)
        shows, reads = body.find("showOverlay("), body.find("readAloud(")
        if shows < 0:
            problems.append(f"{name}() no longer calls showOverlay")
        elif shows > reads:
            problems.append(
                f"{name}() calls readAloud before showOverlay, and showOverlay "
                f"hushes (#301) — the screen cancels the read it just started, and "
                f"the only symptom is a screen that says nothing")
    return problems


def test_a_screen_shows_itself_before_it_reads_itself():
    """The rule, over the game's own source."""
    problems = reading_screen_problems(MAIN.read_text())
    assert not problems, "\n".join(problems)
    assert screens_that_read(MAIN.read_text()) == set(READING_SCREENS)


# One source per way of being wrong. `showOverlay`/`readAloud` are the only
# calls that matter here, so these are the smallest files that can hold the
# question — and each is checked to produce exactly the one complaint, so a
# message that fires on everything cannot pass as a message that fires on this.
FOUR_SCREENS = """\
function storyCard(i) {
  showOverlay(`<h2>Chapter</h2>`);
  readAloud(["Chapter one."]);
}
function readAloud(lines, { speak = true } = {}) {
  placeReadButton();
}
"""
UNDECLARED = FOUR_SCREENS + """\
function creditsCard() {
  showOverlay(`<h2>Credits</h2>`);
  readAloud(["Credits."]);
}
"""
WENT_SILENT = """\
function storyCard(i) {
  showOverlay(`<h2>Chapter</h2>`);
}
function readAloud(lines) {
  placeReadButton();
}
"""
READS_FIRST = """\
function storyCard(i) {
  readAloud(["Chapter one."]);
  showOverlay(`<h2>Chapter</h2>`);
}
function readAloud(lines) {
  placeReadButton();
}
"""
TALKS_IN_A_COMMENT = """\
function storyCard(i) {
  showOverlay(`<h2>Chapter</h2>`);
  readAloud(["Chapter one."]);
}
// the gallery does not readAloud( anything: a bio is what answers the tap
function gallery() {
  showOverlay(`<div class="gallery"></div>`);
}
function readAloud(lines) {
  placeReadButton();
}
"""


@pytest.mark.parametrize("src,says", [
    (UNDECLARED, "creditsCard() calls readAloud and is not in READING_SCREENS"),
    (WENT_SILENT, "storyCard() is declared as a screen that reads itself and calls no"),
    (READS_FIRST, "storyCard() calls readAloud before showOverlay"),
], ids=["a fifth screen nobody declared", "a declared screen went silent",
        "a screen that reads before it shows"])
def test_the_reading_screen_rule_can_fail(src, says):
    """Each way past this check, taken.

    Without these the rule is three assertions nothing has ever seen fail — and
    the one it was written for (an undeclared fifth screen) is exactly the one a
    hand-maintained list cannot produce on its own.
    """
    problems = reading_screen_problems(src, declared=("storyCard",))
    assert len(problems) == 1, problems
    assert problems[0].startswith(says), problems[0]


def test_the_rule_reads_the_calls_and_not_the_prose():
    """A comment naming `readAloud(` is not a screen that reads itself."""
    assert reading_screen_problems(FOUR_SCREENS, declared=("storyCard",)) == []
    assert screens_that_read(TALKS_IN_A_COMMENT) == {"storyCard"}, (
        "the gallery's comment about not reading was read as a call")
