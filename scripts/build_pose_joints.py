#!/usr/bin/env python3
"""The hip a run pose is cut at, so its legs can swing.

`public/data/poses.json` names one side-on render per character per state, and
one drawing cannot run: a still frame that bobs is a dog being carried past the
scenery. The wiki has no usable cycle either — its two run GIFs are close-ups
cut across the shins, so their feet are outside their own artwork (the finding
is written up in the `poseFrame` comment in sprites.js). So the cycle is made
rather than found: the render is cut at the hip and the lower band swings, which
is the rig's own trick applied to a side-on drawing instead of a standing one.

That is only safe because a side-on hip is a real joint. The rig's is not — its
source is a *front-facing* standing render, where a band below the hip is both
legs, the gap between them and, for Muffin, her tail; swinging that slid a grey
slab out sideways and is most of what "the characters look messed up" was
(#164, #206). The difference is the artwork, so it is the artwork that has to be
measured, which is what this file is.

  python3 scripts/build_pose_joints.py           # write public/data/pose-joints.json
  python3 scripts/build_pose_joints.py --sheet   # + the swing, drawn, to look at
  python3 scripts/build_pose_joints.py --check   # the joints still sit on the artwork

`HIPS` is hand-authored, exactly like build_rigs.py's `OVERRIDES` and for the
same reason: there are four of them, each in a different pose, and a rule that
gets all four right off a silhouette is a bigger guess than four measurements.
The pivot underneath is derived, because it follows from the hip.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
POSES_JSON = APP / "public" / "data" / "poses.json"
JOINTS = APP / "public" / "data" / "pose-joints.json"
SHEET = APP / "shots" / "_debug" / "pose-swing.png"

ALPHA = 40  # a pixel counts as opaque above this
MIN_RUN = 3  # ignore hairline runs (antialiasing, a stray whisker)

# Hand-measured off scripts/build_pose_joints.py --sheet, one per run render, as
# a fraction of image height. Each one is the lowest line that still has both
# legs joined behind it and the arms in front of it — a cut any higher takes an
# elbow with the legs and the paw swings, which is the "messed up" look coming
# back on a different limb.
HIPS: dict[str, float] = {
    "bluey": 0.73,
    "bingo": 0.78,
    "bandit": 0.79,
    "chilli": 0.77,
}

# Only a state whose artwork is a stride is cut. A jump and a cheer are drawn
# mid-air with the legs already where the artist put them, and there is no
# stride phase under them to swing to.
SWUNG = ("run",)

# The bounds --check holds the numbers to. A hip outside this range is not a hip
# on a cartoon dog; a band below it that is over a third of the drawing is a
# torso.
HIP_RANGE = (0.60, 0.90)
BELOW_MAX = 0.35  # today: 0.09 (bandit) to 0.18 (bingo)
BELOW_MIN = 0.05
JOINED = 0.70  # the widest run at the hip, as a share of that row's opaque pixels


def here(path: Path) -> str:
    """A path to print: inside the app, said the short way. A test points the
    output at a temporary file, and a message is not worth crashing over."""
    try:
        return str(path.relative_to(APP))
    except ValueError:
        return str(path)


def runs(px, w: int, y: int) -> list[tuple[int, int]]:
    """The opaque runs across row `y`, as [start, end) pairs."""
    out: list[tuple[int, int]] = []
    start = None
    for x in range(w):
        opaque = px[x, y][3] > ALPHA
        if opaque and start is None:
            start = x
        elif not opaque and start is not None:
            if x - start >= MIN_RUN:
                out.append((start, x))
            start = None
    if start is not None and w - start >= MIN_RUN:
        out.append((start, w))
    return out


def measure(path: Path, hip: float) -> dict:
    """The joint for one render, plus what --check reads back."""
    from PIL import Image

    im = Image.open(path).convert("RGBA")
    w, h = im.size
    px = im.load()
    row = min(h - 1, int(h * hip))
    at_hip = runs(px, w, row)
    widest = max(at_hip, key=lambda r: r[1] - r[0]) if at_hip else None
    opaque = sum(b - a for a, b in at_hip)
    below = total = 0
    for y in range(h):
        for x in range(w):
            if px[x, y][3] > ALPHA:
                total += 1
                if y >= row:
                    below += 1
    return {
        "hip": hip,
        # the middle of the widest run at the hip: the leg mass swings about its
        # own centre, so a stride does not slide the body sideways
        "pivot": round(((widest[0] + widest[1]) / 2) / w, 4) if widest else 0.5,
        "width": w,
        "runs": at_hip,
        "joined": (widest[1] - widest[0]) / opaque if widest and opaque else 0,
        "below": below / total if total else 0,
    }


def frames_to_cut(poses: dict) -> list[tuple[str, str, str]]:
    """(character, state, relative path) for every frame that gets a hip."""
    out = []
    for cid, by_state in sorted(poses.get("frames", {}).items()):
        for state in SWUNG:
            for rel in by_state.get(state, []):
                out.append((cid, state, rel))
    return out


def build() -> dict:
    poses = json.loads(POSES_JSON.read_text())
    joints = {}
    for cid, _state, rel in frames_to_cut(poses):
        if cid not in HIPS:
            continue
        m = measure(APP / "public" / rel, HIPS[cid])
        joints[rel] = {"hip": m["hip"], "pivot": m["pivot"]}
    return {
        "note": ("Where each side-on pose render is cut so its legs can swing, as "
                 "fractions of the image. Written by scripts/build_pose_joints.py; "
                 "read by public/js/sprites.js. A frame with no entry here is drawn "
                 "whole, exactly as every pose was before this file existed."),
        "joints": joints,
    }


def check() -> list[str]:
    """Every stored joint still sits on the artwork it was measured from."""
    problems: list[str] = []
    if not JOINTS.exists():
        return [f"no {here(JOINTS)} — run build_pose_joints.py"]
    stored = json.loads(JOINTS.read_text()).get("joints", {})
    poses = json.loads(POSES_JSON.read_text())
    wanted = frames_to_cut(poses)

    for cid, state, rel in wanted:
        if rel not in stored:
            problems.append(f"{cid} {state}: {rel} has no hip, so its legs cannot swing")
    for rel in sorted(stored):
        if rel not in {r for _, _, r in wanted}:
            problems.append(f"{rel}: has a hip but poses.json never draws it as a stride")
            continue
        j = stored[rel]
        art = APP / "public" / rel
        if not art.exists():
            problems.append(f"{rel}: has a hip but the file is not on disk")
            continue
        m = measure(art, j["hip"])
        lo, hi = HIP_RANGE
        if not lo <= j["hip"] <= hi:
            problems.append(f"{rel}: hip {j['hip']} is outside {HIP_RANGE}")
        if not m["runs"]:
            problems.append(f"{rel}: the hip line crosses no artwork at all")
            continue
        if not any(a <= j["pivot"] * m["width"] < b for a, b in m["runs"]):
            problems.append(f"{rel}: the pivot is off the body — hip row runs {m['runs']}")
        if m["joined"] < JOINED:
            problems.append(
                f"{rel}: only {m['joined']:.0%} of the hip row is one run, so a single "
                f"band below it would cut a leg off mid-air (want {JOINED:.0%})")
        if not BELOW_MIN <= m["below"] <= BELOW_MAX:
            problems.append(
                f"{rel}: {m['below']:.0%} of the drawing is below the hip — that is not "
                f"a pair of legs (want {BELOW_MIN:.0%}-{BELOW_MAX:.0%})")
    return problems


def sheet() -> Path:
    """The swing itself, five frames a stride apart, for the eye to judge.

    The renderer is sprites.js and this is PIL, so this is a drawing of the
    same cut and not the thing that ships — enough to choose a hip on, which is
    the job. The picture that proves what ships is the e2e motion test.
    """
    from PIL import Image

    poses = json.loads(POSES_JSON.read_text())
    stored = build()["joints"]
    rows = []
    angles = (-12.6, -6.3, 0, 6.3, 12.6)  # LEG_SWING in sprites.js, in degrees
    for _cid, _state, rel in frames_to_cut(poses):
        if rel not in stored:
            continue
        im = Image.open(APP / "public" / rel).convert("RGBA")
        rows.append([swung(im, stored[rel], d) for d in angles])
    if not rows:
        return SHEET
    cw = max(r[0].width for r in rows)
    ch = max(r[0].height for r in rows)
    out = Image.new("RGBA", (cw * len(angles), ch * len(rows)), (255, 255, 255, 255))
    for y, row in enumerate(rows):
        for x, frame in enumerate(row):
            out.alpha_composite(frame, (x * cw, y * ch))
    SHEET.parent.mkdir(parents=True, exist_ok=True)
    out.save(SHEET)
    return SHEET


def swung(im, joint: dict, degrees: float):
    """One frame of the swing: the band below the hip turned about the pivot."""
    from PIL import Image

    w, h = im.size
    hip = int(h * joint["hip"])
    seam = int(h * 0.05)  # SEAM in sprites.js
    torso = im.crop((0, 0, w, hip + seam))
    legs = Image.new("RGBA", (w * 3, h * 3), (0, 0, 0, 0))
    legs.paste(im.crop((0, hip - seam, w, h)), (w, h + hip - seam))
    legs = legs.rotate(-degrees, center=(w + joint["pivot"] * w, h + hip),
                       resample=Image.BICUBIC)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    # nothing the swung band paints above the hip line, same as the clip in
    # drawPose: the belly it carries is there to fill the wedge, not to fly
    out.alpha_composite(legs.crop((w, h + hip, w * 2, h * 2)), (0, hip))
    out.alpha_composite(torso, (0, 0))  # covers the seam, exactly as the rig's torso does
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="verify the stored joints")
    ap.add_argument("--sheet", action="store_true", help="draw the swing")
    args = ap.parse_args()

    if args.check:
        problems = check()
        for p in problems:
            print(f"  {p}")
        print(f"{'FAIL' if problems else 'OK'}: {len(problems)} problem(s) in "
              f"{here(JOINTS)}")
        return 1 if problems else 0

    data = build()
    JOINTS.write_text(json.dumps(data, indent=2) + "\n")
    print(f"wrote {here(JOINTS)}: {len(data['joints'])} joint(s)")
    for rel, j in sorted(data["joints"].items()):
        print(f"  {rel}: hip {j['hip']} pivot {j['pivot']}")
    if args.sheet:
        print(f"wrote {sheet().relative_to(APP)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
