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
