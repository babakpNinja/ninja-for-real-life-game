#!/usr/bin/env python3
"""Derive a cut-out rig for every character sprite from its own silhouette.

The sprites are front-facing full-body renders, so the joints are findable:

  hip  — scanning up from the feet, the last row that is still split into two
         separate runs of opaque pixels is the top of the gap between the legs.
  neck — above the hip, the narrowest row between the torso's widest row and
         the head's widest row.

Everything is stored as a fraction of the image height/width so the rig is
resolution independent, then read by public/js/sprites.js.

  python3 scripts/build_rigs.py            # write public/data/rigs.json
  python3 scripts/build_rigs.py --sheet    # + an annotated contact sheet
  python3 scripts/build_rigs.py --check    # every rig still cuts its own render
  python3 scripts/build_rigs.py --grid bluey,bingo   # measuring grids for the boxes
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
CHARS = APP / "public" / "data" / "characters.json"
ASSETS = APP / "public" / "assets" / "characters"
RIGS = APP / "public" / "data" / "rigs.json"
# Verification output, not a deliverable: shots/ is what goes in the README and
# in Slack, so the annotated sheets live under an ignored subdirectory rather
# than sitting next to the screenshots waiting to be deleted by hand.
SHEET = APP / "shots" / "_debug" / "rig-contact-sheet.png"

ALPHA = 40  # a pixel counts as opaque above this
MIN_RUN = 3  # ignore hairline runs (antialiasing, a stray whisker)

# What "eye white" and "pupil" mean in a pixel, used by the finder that proposes
# a box and by the check that reads the stored one back. One definition: the
# check is only worth anything if it can disagree with the finder, and it cannot
# disagree about a threshold they do not share.
WHITE_MIN = 205   # every channel above this, i.e. near-white
DARK_MAX = 90     # every channel below this, i.e. ink
SOLID = 200       # alpha: ignore the antialiased rim of a shape

# How much of a stored eye box has to be eye, measured on the artwork it was
# taken from. The floor across the 46 boxes in rigs.json today is 38% white and
# a pupil covering 6% of the box; these sit well under that, because the
# question is "is this box on an eye at all" and not "is this box perfect". A
# box on Bluey's snout scores 9% white — the gap is the whole check.
EYE_WHITE = 0.30
EYE_PUPIL = 0.03

# A lid patch is averaged to one colour and the blink is painted in it, so the
# patch has to *be* one colour. LID_SPREAD is that, summed over the three
# channels: `find_lid` refuses to propose a patch above it, and the check asks
# the same of the stored ones. The data it is a floor under: two of the 23 sat
# above it (Bandit 538, straddling the edge of his mask and the background;
# Bingo 103, on her cheek outline) and the worst of the rest is 6.
LID_SPREAD = 60
LID_OPAQUE = 250  # alpha: a patch may not include the antialiased rim, let alone off-body

NECK_RANGE = (0.30, 0.58)  # a cartoon dog's chin, as a fraction of its height
HIP_RANGE = (0.66, 0.86)
NECK_DEFAULT, HIP_DEFAULT = 0.45, 0.76
LEG_SAMPLE = 0.94  # the row to measure leg positions on: below the belly, above the feet

# How far a stored number may sit from the one this file reads off the render
# before --check calls it a different drawing. Every derived rig in the repo
# reads back to 0.0000 today and every leg pivot to 0.0000, so this is slack
# against a re-encode, not a fitted line: the smallest relocation it has to
# catch is Muffin's hip moving 0.02 when #220 replaced her base render.
JOINT_TOL = 0.01
LEG_TOL = 0.01

# Hand-authored joints, measured off shots/_debug/rig-grid.png. Only the characters
# that get large motion need this: the four playable heroes and the five
# cameos, which cheer. Everyone else appears in the gallery at idle, where the
# rotations are a couple of degrees and a joint line a few percent off is not
# visible. Deriving a chin line from a silhouette is unreliable (ears and
# raised arms dominate the row widths), so those nine are measured, not guessed.
#
# `measuredOff` is the render they were measured on — the first 12 hex of its
# sha256, which `--check` prints when it does not match. A derived rig is
# re-derived from the artwork on every check and cannot drift away from it; a
# hand-measured one has nothing to compare against, which is the whole reason
# it is hand-measured, so the question `--check` asks instead is whether the
# drawing is still the one somebody looked at. #220 replaced Muffin's base
# render and her 0.50/0.78, measured on the old drawing, stayed silently wrong
# through a build and a ship (#226). Re-measure off `--grid <id>`, then paste
# the stamp the failure hands you; changing one without the other is the state
# this field exists to make loud.
OVERRIDES: dict[str, dict] = {
    "bluey": {"neck": 0.55, "hip": 0.76, "measuredOff": "b646f3838be9"},
    "bingo": {"neck": 0.57, "hip": 0.78, "measuredOff": "ba5bb1fde5cf"},
    "bandit": {"neck": 0.52, "hip": 0.78, "measuredOff": "2dd885e38962"},
    "chilli": {"neck": 0.53, "hip": 0.77, "measuredOff": "bcec1bcb4698"},
    "muffin": {"neck": 0.55, "hip": 0.80, "measuredOff": "1bd2113e716b"},
    "lucky": {"neck": 0.42, "hip": 0.75, "measuredOff": "100181349dd2"},
    "nana_chris": {"neck": 0.44, "hip": 0.79, "measuredOff": "a3c07577084b"},
    # Chattermax is a toy owl: no neck, and two little feet right at the bottom.
    "chattermax": {"neck": 0.34, "hip": 0.90, "measuredOff": "7bafde32e4ae"},
    # Snickers is a dachshund — long body, and his legs never separate.
    "snickers": {"neck": 0.36, "hip": 0.80, "measuredOff": "eb00b94a0d2f"},
}


# Optional extra parts, measured by eye off `--grid <id>`. A box is
# [x0, y0, x1, y1] as a fraction of the image; the pivot is the point it turns
# about — the root for a tail, the base for an ear. `eyes` is one box per eye,
# tight around the white: a single box spanning both wipes the muzzle between
# them too and the character blinks with a visor on. `lid` is not a part either
# — it is a patch of plain face beside the eyes whose average colour the eyelid
# is drawn in, because the fur immediately above an eye is as often a marking
# (Bandit's mask, Chilli's brow) as it is skin.
#
# There is no silhouette rule for a tail: it only exists in a render where the
# character happens to be standing side-on, and it is the same colour as the leg
# it crosses. Ears and eyes do have one, and `--suggest` runs it — an ear is a
# lobe the silhouette splits into above the head, an eye is a white blob with a
# pupil in it, above the neck, next to a matching one. Neither rule fires often:
# a dog with hanging ears, a hat or a fringe between the ears never splits, and a
# pair of eyes that touch comes back as one blob. Where a rule finds nothing the
# character says so in a comment rather than saying nothing, because "this one
# has no ears to box" and "nobody has looked at this one" are different states.
# Every part is optional — sprites.js draws a rig without them exactly as before.
#
# A box must contain *only* the part: it gets cut out of the body, so a box
# that clips an arm punches a hole in the arm. Where the part cannot be boxed
# cleanly it is left out (Bingo and Bandit are facing us and have no tail
# showing; Muffin's tail passes behind her own paw).
PARTS: dict[str, dict] = {
    "bluey": {
        "tail": {"box": [0.74, 0.72, 1.0, 0.99], "pivot": [0.75, 0.74]},
        "ears": [
            {"box": [0.27, 0.0, 0.45, 0.20], "pivot": [0.36, 0.20]},
            {"box": [0.51, 0.0, 0.69, 0.20], "pivot": [0.60, 0.20]},
        ],
        "eyes": [[0.25, 0.205, 0.42, 0.315], [0.50, 0.21, 0.71, 0.35]],
        "lid": [0.715, 0.24, 0.745, 0.30],
    },
    "bingo": {
        "ears": [
            {"box": [0.28, 0.0, 0.47, 0.17], "pivot": [0.38, 0.17]},
            {"box": [0.62, 0.0, 0.81, 0.17], "pivot": [0.71, 0.17]},
        ],
        "eyes": [[0.27, 0.195, 0.47, 0.41], [0.53, 0.20, 0.76, 0.41]],
        # was [0.64, 0.43, 0.70, 0.47], on the outline under her right eye (#140)
        "lid": [0.231, 0.383, 0.267, 0.418],
    },
    "bandit": {
        "ears": [
            {"box": [0.17, 0.0, 0.37, 0.17], "pivot": [0.27, 0.17]},
            {"box": [0.49, 0.0, 0.70, 0.17], "pivot": [0.60, 0.17]},
        ],
        "eyes": [[0.33, 0.21, 0.50, 0.39], [0.505, 0.215, 0.575, 0.31]],
        # was [0.10, 0.33, 0.14, 0.40], half of it off the side of his head (#140)
        "lid": [0.276, 0.211, 0.311, 0.246],
    },
    "chilli": {
        "tail": {"box": [0.66, 0.76, 1.0, 0.98], "pivot": [0.67, 0.80]},
        "ears": [
            {"box": [0.21, 0.0, 0.43, 0.16], "pivot": [0.32, 0.16]},
            {"box": [0.51, 0.0, 0.73, 0.16], "pivot": [0.62, 0.16]},
        ],
        "eyes": [[0.19, 0.20, 0.29, 0.30], [0.32, 0.20, 0.58, 0.41]],
        "lid": [0.62, 0.30, 0.68, 0.38],
    },
    # Re-measured off File:Muffin.png, which replaced the waving infobox render
    # as her base in #220. She faces three-quarters left in it, so her far eye
    # is the small one and her tail lies out to her right — behind her own hind
    # paw, so still not boxable as a part.
    "muffin": {
        "ears": [
            {"box": [0.03, 0.0, 0.306, 0.169], "pivot": [0.166, 0.169]},
            {"box": [0.306, 0.0, 0.545, 0.169], "pivot": [0.44, 0.169]},
        ],
        "eyes": [[0.056, 0.266, 0.214, 0.402], [0.263, 0.264, 0.497, 0.498]],
        "lid": [0.23, 0.25, 0.262, 0.282],
    },
    # Lucky's ears hang: they pivot at the top, not at the base, and his tail
    # sweeps out to his left.
    "lucky": {
        "tail": {"box": [0.02, 0.70, 0.37, 0.92], "pivot": [0.36, 0.76]},
        "ears": [
            {"box": [0.03, 0.03, 0.27, 0.36], "pivot": [0.25, 0.09]},
            {"box": [0.74, 0.03, 0.99, 0.36], "pivot": [0.76, 0.09]},
        ],
        "eyes": [[0.385, 0.115, 0.545, 0.28], [0.565, 0.12, 0.665, 0.245]],
        "lid": [0.32, 0.33, 0.37, 0.40],
    },
    # Below here the boxes were proposed by `--suggest` and then looked at, one
    # character at a time, on the contact sheet and in a rendered blink (#139).
    "socks": {
        "ears": [
            {"box": [0.083, 0.0, 0.346, 0.186], "pivot": [0.212, 0.186]},
            {"box": [0.346, 0.0, 0.629, 0.186], "pivot": [0.49, 0.186]},
        ],
        "eyes": [[0.088, 0.265, 0.263, 0.389], [0.307, 0.261, 0.532, 0.474]],
        "lid": [0.576, 0.261, 0.61, 0.297],
    },
    "stripe": {
        "ears": [
            {"box": [0.368, 0.0, 0.667, 0.15], "pivot": [0.514, 0.15]},
            {"box": [0.679, 0.0, 0.972, 0.15], "pivot": [0.807, 0.15]},
        ],
        "eyes": [[0.506, 0.193, 0.701, 0.359], [0.717, 0.193, 0.871, 0.295]],
        "lid": [0.443, 0.252, 0.478, 0.287],
    },
    "trixie": {
        "ears": [
            {"box": [0.384, 0.0, 0.632, 0.154], "pivot": [0.508, 0.154]},
            {"box": [0.632, 0.0, 0.923, 0.154], "pivot": [0.779, 0.154]},
        ],
        "eyes": [[0.497, 0.205, 0.719, 0.385], [0.745, 0.203, 0.894, 0.303]],
        "lid": [0.455, 0.238, 0.49, 0.273],
    },
    "nana_chris": {
        "ears": [
            {"box": [0.369, 0.0, 0.611, 0.146], "pivot": [0.487, 0.146]},
            {"box": [0.611, 0.0, 0.883, 0.146], "pivot": [0.75, 0.146]},
        ],
        "eyes": [[0.44, 0.201, 0.664, 0.305], [0.688, 0.199, 0.842, 0.311]],
        "lid": [0.393, 0.234, 0.426, 0.27],
    },
    "bob": {
        "ears": [
            {"box": [0.29, 0.0, 0.569, 0.145], "pivot": [0.429, 0.145]},
            {"box": [0.569, 0.0, 0.866, 0.145], "pivot": [0.719, 0.145]},
        ],
        "eyes": [[0.438, 0.207, 0.659, 0.359], [0.67, 0.197, 0.848, 0.309]],
        "lid": [0.37, 0.244, 0.406, 0.279],
    },
    "judo": {
        "eyes": [[0.46, 0.209, 0.664, 0.318], [0.773, 0.254, 0.847, 0.318]],
        "lid": [0.378, 0.209, 0.413, 0.244],
        # no ears (fluffy head, no split in the silhouette)
    },
    "honey": {
        "eyes": [[0.138, 0.204, 0.36, 0.298], [0.367, 0.133, 0.594, 0.298]],
        "lid": [0.731, 0.144, 0.767, 0.18],
        # no ears (they hang beside her head)
    },
    "coco": {
        "eyes": [[0.403, 0.252, 0.581, 0.383], [0.591, 0.24, 0.724, 0.326]],
        "lid": [0.351, 0.275, 0.386, 0.311],
        # no ears (buried in curls)
    },
    "snickers": {
        # no ears (under his cap), eyes (the two whites touch, so they come
        #   back as one blob and there is no second eye to pair it with)
    },
    "chloe": {
        "eyes": [[0.161, 0.094, 0.307, 0.199], [0.311, 0.104, 0.525, 0.291]],
        "lid": [0.531, 0.117, 0.565, 0.152],
        # no ears (they hang)
    },
    "mackenzie": {
        "ears": [
            {"box": [0.24, 0.0, 0.488, 0.176], "pivot": [0.361, 0.176]},
            {"box": [0.547, 0.0, 0.754, 0.176], "pivot": [0.654, 0.176]},
        ],
        "eyes": [[0.438, 0.26, 0.615, 0.42], [0.645, 0.258, 0.766, 0.34]],
        "lid": [0.213, 0.258, 0.249, 0.293],
    },
    "rusty": {
        "ears": [
            {"box": [0.348, 0.0, 0.608, 0.16], "pivot": [0.476, 0.16]},
            {"box": [0.608, 0.0, 0.861, 0.16], "pivot": [0.737, 0.16]},
        ],
        "eyes": [[0.494, 0.238, 0.69, 0.305], [0.722, 0.223, 0.861, 0.305]],
        "lid": [0.313, 0.223, 0.348, 0.258],
    },
    "indy": {
        "eyes": [[0.228, 0.139, 0.357, 0.24], [0.381, 0.148, 0.561, 0.298]],
        "lid": [0.18, 0.186, 0.214, 0.221],
        # no ears (under her hair)
    },
    "winton": {
        "eyes": [[0.357, 0.137, 0.625, 0.386], [0.664, 0.134, 0.857, 0.331]],
        "lid": [0.096, 0.134, 0.132, 0.169],
        # no ears (they hang)
    },
    "jean_luc": {
        "eyes": [[0.419, 0.098, 0.634, 0.283], [0.669, 0.1, 0.825, 0.236]],
        "lid": [0.197, 0.262, 0.231, 0.297],
        # no ears (they hang)
    },
    "calypso": {
        "eyes": [[0.142, 0.175, 0.274, 0.276], [0.3, 0.151, 0.502, 0.328]],
        "lid": [0.624, 0.151, 0.66, 0.187],
        # no ears (down, under her fringe)
    },
    "frisky": {
        "eyes": [[0.422, 0.227, 0.609, 0.393], [0.631, 0.217, 0.76, 0.316]],
        "lid": [0.157, 0.252, 0.191, 0.287],
        # no ears (under her fringe)
    },
    "rad": {
        "eyes": [[0.337, 0.191, 0.554, 0.367], [0.618, 0.188, 0.839, 0.32]],
        "lid": [0.895, 0.188, 0.93, 0.223],
        # no ears (pointed, but his fringe fills the gap between them)
    },
    "chattermax": {
        # no ears (he has none), eyes (they sit below the neck line — his head
        #   is most of him)
    },
}


def suggest(ids: list[str]) -> int:
    """Propose ear/eye/lid boxes for characters that have none, as PARTS source.

    Measuring nineteen characters by eye is a day of grid-reading, and the
    boxes that *can* be found by rule are exactly the ones a rule finds more
    accurately than I do: an eye is a white blob with a pupil in it, ears are
    the two runs at the very top of the silhouette before they merge into the
    head, and a lid patch is the flattest square of opaque face that touches
    neither. A tail is deliberately not suggested — there is no silhouette rule
    for one, and a wrong tail box punches a hole in whatever it clipped.

    The output is Python for PARTS, not a file the build reads: every box still
    lands in the diff where it can be argued with, and a suggestion that is
    wrong is edited or deleted by hand like the measured ones above it.
    """
    from PIL import Image

    for cid in ids:
        f = ASSETS / f"{cid}.png"
        if not f.exists():
            print(f"  # PROBLEM {cid}: no sprite")
            continue
        im = Image.open(f).convert("RGBA")
        rig = derive(im)
        rig.update(OVERRIDES.get(cid, {}))
        neck = rig.get("neck", NECK_DEFAULT)
        parts = {}
        ears = find_ears(im, neck)
        eyes = find_eyes(im, neck)
        lid = find_lid(im, neck, eyes) if eyes else None
        if ears:
            parts["ears"] = ears
        if eyes and lid:
            parts["eyes"], parts["lid"] = eyes, lid
        print(f'    "{cid}": {{')
        for k, v in parts.items():
            if k == "ears":
                print('        "ears": [')
                for ear in v:
                    print(f'            {{"box": {ear["box"]}, "pivot": {ear["pivot"]}}},')
                print("        ],")
            else:
                print(f'        "{k}": {json.dumps(v)},')
        # a placeholder, not the note: the rule knows it found nothing, only a
        # person looking at the picture knows why, and PARTS wants the why
        missing = [k for k in ("ears", "eyes") if k not in parts]
        if missing:
            print(f"        # no {', '.join(missing)} (say why here)")
        print("    },")
    return 0


def find_ears(im, neck: float) -> list[dict]:
    """The two runs at the top of the silhouette, down to where they merge.

    An upright ear is the only part of a dog that the alpha channel gives away:
    above the skull there is nothing between the ears, so the top rows hold two
    runs and the row where they become one is the base to pivot about. A
    character whose ears hang, or whose head is one dome, produces nothing here
    — which is the right answer, since a box that clips the head cuts a hole in
    it.
    """
    w, h = im.size
    rr = rows(im)
    first = next((y for y in range(h) if len(rr[y]) >= 2), None)
    if first is None:
        return []          # one dome, or one ear taller than the other all the way down
    base = first
    while base + 1 < h and len(rr[base + 1]) >= 2:
        base += 1
    first = next(y for y in range(h) if rr[y])   # the box starts at the taller ear's tip
    depth = (base - first) / h
    # A shallow split is a tuft of hair or a hat brim, not a pair of ears — the
    # six measured by hand all run 0.15–0.19 of the height. A pair that is
    # wildly lopsided is the same mistake seen from the side: one ear and the
    # top of the skull.
    if depth < 0.09 or base / h > neck * 0.8:
        return []
    left = min(min(r[0] for r in rr[y]) for y in range(first, base + 1))
    right = max(max(r[1] for r in rr[y]) for y in range(first, base + 1))
    lobes = sorted(rr[base], key=lambda r: r[1] - r[0], reverse=True)[:2]
    if len(lobes) < 2:
        return []
    wide, narrow = (max(b - a for a, b in lobes), min(b - a for a, b in lobes))
    if wide > 2.0 * narrow:
        return []
    lobes.sort()
    out = []
    for i, (a, b) in enumerate(lobes):
        x0 = left if i == 0 else max(a - 2, (lobes[0][1] + lobes[1][0]) // 2)
        x1 = right if i == 1 else min(b + 2, (lobes[0][1] + lobes[1][0]) // 2)
        out.append({"box": [rnd(x0 / w), round(first / h, 3), rnd(x1 / w), rnd((base + 1) / h)],
                    "pivot": [rnd((a + b) / 2 / w), rnd((base + 1) / h)]})
    return out


def find_eyes(im, neck: float) -> list[list[float]]:
    """The best pair of white-with-a-pupil blobs above the neck.

    Cartoon eyes are the whitest thing on a face and the only white that
    contains black — but on half these characters an eye touches the white
    muzzle, so the biggest white blob is *both eyes and the snout* and picking
    the largest two gets a hole punched through the face. Hence a pair search
    over eye-shaped candidates: a blob that fills its own bounding box (an eye
    is a rounded blot; an eye-plus-muzzle is an L), is roughly as tall as it is
    wide, and is small enough to be a feature rather than a region.
    """
    w, h = im.size
    px = im.load()
    white = {(x, y) for y in range(round(neck * h)) for x in range(w)
             if is_white(px[x, y])}
    cands = []
    for blob in components(white):
        x0 = min(p[0] for p in blob); x1 = max(p[0] for p in blob)
        y0 = min(p[1] for p in blob); y1 = max(p[1] for p in blob)
        bw, bh = x1 - x0 + 1, y1 - y0 + 1
        if len(blob) < 0.0004 * w * h or bw < 5 or bh < 5:
            continue
        if bw > 0.4 * w or bh > 0.3 * h:
            continue                              # a region of the body, not a feature
        if not 0.4 < bw / bh < 2.5:
            continue                              # a stripe of fur, not an eye
        dark = sum(is_dark(px[x, y])
                   for y in range(y0, y1 + 1) for x in range(x0, x1 + 1))
        if not dark:
            continue                              # white with no pupil in it is not an eye
        # An eye fills its box: white and pupil together are the whole blot. An
        # eye fused to the muzzle is an L, and the corner it leaves empty is
        # neither — which is the only thing separating the two by shape.
        if (len(blob) + dark) / (bw * bh) < 0.6:
            continue
        cands.append((len(blob), [x0 - 1, y0 - 1, x1 + 2, y1 + 2]))
    best = None
    for i, (na, a) in enumerate(cands):
        for nb, b in cands[i + 1:]:
            left, right = sorted((a, b))
            if left[2] > right[0]:
                continue                          # overlapping in x: the same eye twice
            overlap = min(left[3], right[3]) - max(left[1], right[1])
            if overlap < 0.4 * min(left[3] - left[1], right[3] - right[1]):
                continue                          # stacked, not side by side
            tall = sorted((left[3] - left[1], right[3] - right[1]))
            if tall[1] > 2.2 * tall[0] or right[0] - left[2] > 0.35 * w:
                continue                          # mismatched, or on opposite sides of a body
            if best is None or na + nb > best[0]:
                best = (na + nb, [left, right])
    if not best:
        return []
    # The 2px of padding can push the box a whisker past the chin line on a
    # character whose neck was derived high; trim rather than lose the eye,
    # since a box that crosses the neck is refused by check().
    chin = neck * h - 1
    return [[rnd(x0 / w), rnd(y0 / h), rnd(x1 / w), rnd(min(y1, chin) / h)]
            for x0, y0, x1, y1 in best[1]]


def find_lid(im, neck: float, eyes: list[list[float]]) -> list[float] | None:
    """The flattest opaque square of face *beside* the eyes.

    'Plain face' is measurable: the eyelid is drawn in this patch's average
    colour, so what matters is that the patch has only one colour in it. The
    search returns the lowest-variance candidate rather than the first
    acceptable one, because a marking's interior is flat too and only loses to
    the cheek beside it.

    Flatness alone is not enough, though: the flattest square on a face is
    often the muzzle between the eyes, and a lid drawn in muzzle white reads as
    an eye rolled back. So the search is confined to the temple either side of
    the eyes, at eye height — where the six hand-measured lids were taken from.
    """
    w, h = im.size
    px = im.load()
    bw, bh = max(4, round(0.035 * w)), max(4, round(0.035 * h))
    top, low = round(min(e[1] for e in eyes) * h), round(max(e[3] for e in eyes) * h)
    outer = (round(min(e[0] for e in eyes) * w), round(max(e[2] for e in eyes) * w))
    best, best_spread = None, None
    for y in range(top, min(low, round(neck * h) - bh), max(2, bh // 3)):
        for x in range(0, w - bw, max(2, bw // 3)):
            box = [x / w, y / h, (x + bw) / w, (y + bh) / h]
            if x + bw > outer[0] and x < outer[1]:
                continue                           # between or under the eyes, not beside them
            spread, thin, _ = patch_stats(im, box)
            if thin:
                continue                           # over an edge: half of it is background
            if best_spread is None or spread < best_spread:
                best, best_spread = box, spread
    if best is None or best_spread > LID_SPREAD:
        return None
    return [rnd(v) for v in best]


def patch_stats(im, box: list) -> tuple[int, int, int]:
    """A lid patch as three numbers: colour spread, see-through pixels, area.

    The spread is summed over the channels rather than taken per channel,
    because two flat colours can differ in one channel only — Bingo's cheek and
    her outline are the same hue — and a per-channel maximum would let that
    through at a third of the score.
    """
    w, h = im.size
    px = im.load()
    x0, y0 = max(0, round(box[0] * w)), max(0, round(box[1] * h))
    x1, y1 = min(w, round(box[2] * w)), min(h, round(box[3] * h))
    vals = [px[i, j] for j in range(y0, y1) for i in range(x0, x1)]
    if not vals:
        return 0, 0, 0
    spread = sum(max(v[c] for v in vals) - min(v[c] for v in vals) for c in range(3))
    return spread, sum(v[3] < LID_OPAQUE for v in vals), len(vals)


def lid_problems(cid: str, r: dict, im) -> list[str]:
    """Ask the artwork whether the stored lid patch is one colour of face.

    A blink is painted in this patch's *average* colour, so a patch straddling
    two things averages to a colour that is on the character nowhere and the dog
    blinks in a smudge. `box_problems` cannot see it: the patch is on the image,
    above the neck and clear of the eyes, which is all geometry knows.

    `find_lid` has refused to propose such a patch since it existed — the bug is
    that the six hand-measured ones predate it and were never asked. Bandit's was
    half off the side of his head (#140).
    """
    lid = r.get("lid")
    if not lid or len(lid) != 4 or not (0 <= lid[0] < lid[2] <= 1 and 0 <= lid[1] < lid[3] <= 1):
        return []                         # box_problems already said so; do not say it twice
    spread, thin, area = patch_stats(im, lid)
    if area < 9:
        return [f"{cid}: lid patch is {area}px on this image — too small to average"]
    if thin:
        return [f"{cid}: lid patch has {thin} of {area} pixels off the character "
                f"— it has slipped over the edge of the head, so the blink averages in background"]
    if spread > LID_SPREAD:
        return [f"{cid}: lid patch spans {spread} of colour (over {LID_SPREAD}) — it straddles "
                f"a marking, and the average of two colours is on this character nowhere"]
    return []


def is_white(p) -> bool:
    return p[3] > SOLID and min(p[:3]) > WHITE_MIN


def is_dark(p) -> bool:
    return p[3] > SOLID and max(p[:3]) < DARK_MAX


def components(points: set) -> list[list[tuple[int, int]]]:
    """4-connected blobs, iteratively — a recursive flood fill blows the stack."""
    seen, out = set(), []
    for p in points:
        if p in seen:
            continue
        blob, stack = [], [p]
        seen.add(p)
        while stack:
            x, y = stack.pop()
            blob.append((x, y))
            for n in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if n in points and n not in seen:
                    seen.add(n)
                    stack.append(n)
        out.append(blob)
    return out


def rnd(v: float) -> float:
    return round(min(max(v, 0.0), 1.0), 3)


def rows(im):
    """Per-row list of (start, end) runs of opaque pixels."""
    a = im.split()[3].load()
    w, h = im.size
    out = []
    for y in range(h):
        runs = []
        x = 0
        while x < w:
            if a[x, y] > ALPHA:
                x0 = x
                while x < w and a[x, y] > ALPHA:
                    x += 1
                if x - x0 >= MIN_RUN:
                    runs.append((x0, x))
            else:
                x += 1
        out.append(runs)
    return out


def width_of(runs) -> int:
    return sum(b - a for a, b in runs)


def clamp(v: float, lo: float, hi: float, default: float) -> tuple[float, bool]:
    if v is None or not math.isfinite(v):
        return default, True
    if v < lo or v > hi:
        return min(max(v, lo), hi), True
    return v, False


def leg_split(rr, top: int, bottom: int) -> int | None:
    """Walking up from the feet, the top row of the gap between the legs.

    `None` when the silhouette never splits at all — a toy with a single base,
    a dachshund drawn side-on. That is a third answer, not "the split is at the
    feet": nothing below such a hip is a leg, and `--check` says so rather than
    holding the number to a landmark that is not there.

    Raw, deliberately: `derive()` clamps this into the range a standing cartoon
    dog can occupy, and a clamped hip read back as a landmark would fail
    Chattermax, whose real split is at 0.926 and whose clamped one is 0.86.
    """
    hip = None
    for y in range(bottom, top, -1):
        if len(rr[y]) < 2:
            if hip is not None:
                break
            continue
        hip = y
    return hip


def derive(im) -> dict:
    """Joints and leg pivots from the silhouette.

    Only the leg pivots are trustworthy: at a row just above the feet the legs
    are two clean runs of opaque pixels. The neck and hip are derived here as a
    starting point and clamped to the range a standing cartoon dog can occupy —
    anything outside it means the derivation lost the plot, so the default is
    used and the character is reported as clamped.
    """
    w, h = im.size
    rr = rows(im)
    filled = [y for y in range(h) if rr[y]]
    if not filled:
        return {}
    top, bottom = filled[0], filled[-1]

    # --- hip: walking up from the feet, how far does the leg gap go? ---
    hip = leg_split(rr, top, bottom)
    hip_f, hip_clamped = clamp(None if hip is None else hip / h, *HIP_RANGE, HIP_DEFAULT)

    # --- neck: narrowest row between the head's widest row and the torso's ---
    hip_row = round(hip_f * h)
    head_zone = range(top, top + max(2, (hip_row - top) // 2))
    torso_zone = range(top + (hip_row - top) // 3, max(top + 3, hip_row))
    head_wide = max(head_zone, key=lambda y: width_of(rr[y]))
    torso_wide = max(torso_zone, key=lambda y: width_of(rr[y]))
    lo, hi = sorted((head_wide, torso_wide))
    span = [y for y in range(lo, hi + 1) if rr[y]]
    neck = min(span, key=lambda y: width_of(rr[y])) / h if span else None
    neck_f, neck_clamped = clamp(neck, *NECK_RANGE, NECK_DEFAULT)

    # --- leg pivots: the two widest runs on a row just above the feet ---
    sample = rr[min(round(LEG_SAMPLE * h), bottom)] or rr[hip_row]
    legs = sorted(sample, key=lambda r: r[1] - r[0], reverse=True)[:2]
    legs.sort()
    if not legs:
        legs = [(0, w)]
    pivots = [round((a + b) / 2 / w, 4) for a, b in legs]

    return {
        "hip": round(hip_f, 4),
        "neck": round(neck_f, 4),
        "legPivots": pivots,
        "legParts": len(pivots),
        "feet": round(bottom / h, 4),
        "derived": True,
        "clamped": [n for n, c in (("neck", neck_clamped), ("hip", hip_clamped)) if c],
    }


def build(sheet: bool) -> int:
    from PIL import Image

    chars = json.loads(CHARS.read_text())["characters"]
    out = {}
    problems = []
    for c in chars:
        f = ASSETS / f"{c['id']}.png"
        if not f.exists():
            problems.append(f"{c['id']}: no sprite")
            continue
        im = Image.open(f).convert("RGBA")
        rig = derive(im)
        if not rig:
            problems.append(f"{c['id']}: empty silhouette")
            continue
        if c["id"] in OVERRIDES:
            rig.update(OVERRIDES[c["id"]])
            rig["derived"] = False
            rig["clamped"] = []
        rig.update(PARTS.get(c["id"], {}))
        rig["legParts"] = len(rig["legPivots"])
        out[c["id"]] = rig
        how = "measured" if not rig["derived"] else ("clamped:" + ",".join(rig["clamped"]) if rig["clamped"] else "derived")
        print(
            f"  {c['id']:<12} neck={rig['neck']:.3f} hip={rig['hip']:.3f} "
            f"legs={rig['legParts']} pivots={rig['legPivots']} "
            f"extras={','.join(k for k in ('tail', 'ears', 'eyes') if k in rig) or '-'}  {how}"
        )

    RIGS.write_text(
        json.dumps(
            {
                "note": "Cut-out rig per character, derived by scripts/build_rigs.py "
                "from the sprite's alpha silhouette. Fractions of image size. "
                "Parts are drawn back-to-front (legs, torso, head) so each part "
                "covers the seam of the one below it. Optional tail/ears boxes are "
                "cut out of the band they sit in and turned about their pivot; the "
                "eyes box is where a blink is wiped.",
                "rigs": out,
            },
            indent=2,
        )
        + "\n"
    )
    if sheet:
        annotate(out)
    for p in problems:
        print(f"  PROBLEM {p}")
    return 1 if problems else 0


def annotate(rigs: dict) -> None:
    """Draw the derived joint lines over each sprite so they can be eyeballed."""
    from PIL import Image, ImageDraw

    cell, cols = 170, 7
    ids = list(rigs)
    rowsn = math.ceil(len(ids) / cols)
    sheet = Image.new("RGB", (cols * cell, rowsn * (cell + 18)), "white")
    d = ImageDraw.Draw(sheet)
    for i, cid in enumerate(ids):
        im = Image.open(ASSETS / f"{cid}.png").convert("RGBA")
        scale = (cell - 12) / im.height
        im = im.resize((max(1, round(im.width * scale)), cell - 12), Image.LANCZOS)
        ox = (i % cols) * cell + (cell - im.width) // 2
        oy = (i // cols) * (cell + 18)
        sheet.paste(Image.new("RGB", im.size, "white"), (ox, oy))
        sheet.paste(im, (ox, oy), im)
        r = rigs[cid]
        for frac, colour in ((r["neck"], (220, 30, 30)), (r["hip"], (30, 90, 220))):
            y = oy + round(frac * im.height)
            d.line([(ox, y), (ox + im.width, y)], fill=colour, width=2)
        for p in r["legPivots"]:
            x = ox + round(p * im.width)
            y = oy + round(r["hip"] * im.height)
            d.ellipse([x - 3, y - 3, x + 3, y + 3], fill=(30, 160, 60))
        boxes = [(r["tail"]["box"], r["tail"]["pivot"], (230, 120, 0))] if r.get("tail") else []
        boxes += [(e["box"], e["pivot"], (150, 40, 200)) for e in r.get("ears", [])]
        boxes += [(e, None, (0, 160, 170)) for e in r.get("eyes", [])]
        if r.get("lid"):
            boxes.append((r["lid"], None, (220, 0, 120)))
        for box, pv, colour in boxes:
            d.rectangle(
                [ox + round(box[0] * im.width), oy + round(box[1] * im.height),
                 ox + round(box[2] * im.width), oy + round(box[3] * im.height)],
                outline=colour,
            )
            if pv:
                x, y = ox + round(pv[0] * im.width), oy + round(pv[1] * im.height)
                d.ellipse([x - 3, y - 3, x + 3, y + 3], outline=colour, fill=colour)
        d.text((ox + 2, oy + cell - 14), cid, fill=(0, 0, 0))
    SHEET.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(SHEET)
    print(f"  sheet -> {SHEET.relative_to(APP)}  (red=neck, blue=hip, green=leg pivots, orange=tail, purple=ears, teal=eyes)")


def grid(ids: list[str], zoom: tuple[float, float] | None = None) -> int:
    """One sprite per file, big, under a labelled 5% grid.

    This is the measuring tool: the tail, ear and eye boxes below are read off
    these images by eye, because no silhouette rule finds an ear that is the
    same colour as the head it is attached to.
    """
    from PIL import Image, ImageDraw

    out = SHEET.parent
    out.mkdir(parents=True, exist_ok=True)
    for cid in ids:
        f = ASSETS / f"{cid}.png"
        if not f.exists():
            print(f"  PROBLEM {cid}: no sprite")
            return 1
        im = Image.open(f).convert("RGBA")
        y0, y1 = 0.0, 1.0
        if zoom:  # a head fills a fifth of the frame; eyes have to be measured close up
            y0, y1 = zoom
            im = im.crop((0, round(y0 * im.height), im.width, round(y1 * im.height)))
        scale = 700 / im.height
        im = im.resize((round(im.width * scale), 700), Image.LANCZOS)
        pad = 34
        canvas = Image.new("RGB", (im.width + pad, im.height + pad), "white")
        canvas.paste(im, (pad, 0), im)
        d = ImageDraw.Draw(canvas)
        for i in range(21):
            frac = i / 20
            label = y0 + frac * (y1 - y0)  # label in whole-image fractions
            y = round(frac * im.height)
            x = pad + round(frac * im.width)
            heavy = i % 4 == 0
            colour = (200, 40, 40) if heavy else (215, 220, 228)
            d.line([(pad, y), (canvas.width, y)], fill=colour)
            d.line([(x, 0), (x, im.height)], fill=colour)
            if heavy:
                d.text((2, max(0, y - 5)), f"{label:.3f}", fill=(120, 0, 0))
                d.text((x + 2, im.height + 4), f"{frac:.2f}", fill=(120, 0, 0))
        canvas.save(out / f"grid-{cid}.png")
        print(f"  grid -> {(out / f'grid-{cid}.png').relative_to(APP)}  ({im.width}x{im.height})")
    return 0


def sprite_stamp(path: Path) -> str:
    """Which drawing this is: the first 12 hex of the file's sha256.

    Short enough to sit on the same source line as the numbers it belongs to,
    and it only ever has to tell one render of one character from another.
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def joint_problems(cid: str, r: dict, sprite: Path, im) -> list[str]:
    """The joints, read back off the render they claim to cut.

    Two questions, because the numbers have two sources.

    A **derived** rig is `derive()`'s answer to this very image, so ask it again
    and compare: all 16 read back to 0.0000 today, and a render replaced without
    a rebuild is the whole distance between the two.

    A **hand-measured** one cannot be re-derived — the nine in `OVERRIDES` exist
    precisely because the silhouette rule is wrong for them, by up to 0.163 for
    Bluey — so the question becomes whether the artwork is still the one it was
    measured on. Measured over all 25: no silhouette landmark separates Muffin's
    stale 0.50 neck from her true 0.55 (her outline is a constant 212px from
    y=0.40 to 0.68, so there is no pinch to bound a chin with), and the widest
    torso row is a raised arm for 8 of the 25. The stamp is what is left, and it
    is the thing that actually catches #220's failure.

    The leg pivots and the hip's one real landmark are asked of everybody.
    """
    problems = []
    d = derive(im)
    if not d:
        return [f"{cid}: the sprite is empty, so its joints were not read back"]

    stored, found = r["legPivots"], d["legPivots"]
    if len(stored) != len(found):
        problems.append(
            f"{cid}: {len(stored)} leg pivot(s) stored, but row {LEG_SAMPLE} of this "
            f"render splits into {len(found)}")
    else:
        for i, (a, b) in enumerate(zip(stored, found)):
            if abs(a - b) > LEG_TOL:
                problems.append(
                    f"{cid}: leg pivot {i} is {a} but that leg sits at {b} on this "
                    f"render — the band would turn about a point off the leg")

    w, h = im.size
    rr = rows(im)
    filled = [y for y in range(h) if rr[y]]
    split = leg_split(rr, filled[0], filled[-1]) if filled else None
    if split is None:
        problems.append(
            f"{cid}: the silhouette never splits into two legs, so nothing here can say "
            f"whether hip {r['hip']} is on a hip")
    elif r["hip"] > split / h + JOINT_TOL:
        problems.append(
            f"{cid}: hip {r['hip']} is below the top of the leg split ({split / h:.4f}), "
            f"so the band that swings holds no whole leg")

    if r.get("derived"):
        for joint in ("neck", "hip"):
            if abs(r[joint] - d[joint]) > JOINT_TOL:
                problems.append(
                    f"{cid}: {joint} {r[joint]} was derived, but this render derives "
                    f"{d[joint]} — rigs.json is older than {sprite.name}")
        return problems

    stamp, actual = r.get("measuredOff"), sprite_stamp(sprite)
    if not stamp:
        problems.append(
            f"{cid}: neck={r['neck']} hip={r['hip']} are hand-measured and name no "
            f"artwork, so nothing has ever checked they belong to this render — measure "
            f'them off `--grid {cid}` and record measuredOff="{actual}"')
    elif stamp != actual:
        problems.append(
            f"{cid}: neck={r['neck']} hip={r['hip']} were measured off a different "
            f"{sprite.name} ({stamp}); this render is {actual} — re-measure the joints "
            f'off `--grid {cid}` and record measuredOff="{actual}"')
    return problems


def box_problems(cid: str, r: dict) -> list[str]:
    """Validate the optional tail/ear/eye boxes of one rig.

    Absent is always fine — that is what optional means. What is not fine is a
    box that runs off the image, a pivot outside the box it turns (the part
    would swing away from where it is attached), a tail above the neck, an eye
    below it, or a lid patch that samples its colour from an eye.
    """
    bad = []

    def shape(name, box):
        if len(box) != 4 or not all(isinstance(v, (int, float)) for v in box):
            bad.append(f"{cid}: {name} is not a box: {box}")
            return False
        x0, y0, x1, y1 = box
        if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
            bad.append(f"{cid}: {name} box off the image or inside out: {box}")
            return False
        return True

    def pivot_in(name, box, pv):
        if len(pv) != 2:
            bad.append(f"{cid}: {name} pivot is not a point: {pv}")
            return
        e = 0.02  # a pivot may sit on the edge, and a measured one lands near it
        if not (box[0] - e <= pv[0] <= box[2] + e and box[1] - e <= pv[1] <= box[3] + e):
            bad.append(f"{cid}: {name} pivot {pv} is outside its box {box}")

    tail = r.get("tail")
    if tail:
        if shape("tail", tail.get("box", [])):
            if tail["box"][1] < r["neck"]:
                bad.append(f"{cid}: tail box starts above the neck ({tail['box'][1]} < {r['neck']})")
            pivot_in("tail", tail["box"], tail.get("pivot", []))
    for i, ear in enumerate(r.get("ears", [])):
        if shape(f"ear{i}", ear.get("box", [])):
            if ear["box"][3] > r["neck"]:
                bad.append(f"{cid}: ear{i} box reaches below the neck ({ear['box'][3]} > {r['neck']})")
            pivot_in(f"ear{i}", ear["box"], ear.get("pivot", []))
    for i, eye in enumerate(r.get("eyes", [])):
        if shape(f"eye{i}", eye):
            if eye[3] > r["neck"]:
                bad.append(f"{cid}: eye{i} box reaches below the neck ({eye[3]} > {r['neck']})")
    if r.get("eyes") and not r.get("lid"):
        bad.append(f"{cid}: has eyes but no lid patch, so nothing would be drawn")
    if r.get("lid") and shape("lid", r["lid"]):
        if r["lid"][3] > r["neck"]:
            bad.append(f"{cid}: lid patch is below the neck ({r['lid'][3]} > {r['neck']}) — that is not face")
        if any(overlaps(r["lid"], eye) for eye in r.get("eyes", [])):
            bad.append(f"{cid}: lid patch overlaps an eye, so the lid colour is part eye white")
    return bad


def overlaps(a: list, b: list) -> bool:
    return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]


def eye_pixels(im, box: list) -> tuple[float, float, int]:
    """What is inside a box, as fractions of it: near-white, biggest ink blob.

    The pupil is the largest *connected* dark blob rather than all the dark
    pixels, because "dark somewhere in here" is also true of a box straddling an
    outline, and an eye is one blot of ink in a field of white.
    """
    w, h = im.size
    px = im.load()
    x0, y0 = max(0, round(box[0] * w)), max(0, round(box[1] * h))
    x1, y1 = min(w, round(box[2] * w)), min(h, round(box[3] * h))
    area = max(0, x1 - x0) * max(0, y1 - y0)
    if area < 9:
        return 0.0, 0.0, area
    white = {(x, y) for y in range(y0, y1) for x in range(x0, x1) if is_white(px[x, y])}
    dark = {(x, y) for y in range(y0, y1) for x in range(x0, x1) if is_dark(px[x, y])}
    pupil = max((len(b) for b in components(dark)), default=0)
    return len(white) / area, pupil / area, area


def eye_problems(cid: str, r: dict, im) -> list[str]:
    """Ask the artwork whether each stored eye box is on an eye.

    `box_problems` validates the *geometry* of these boxes — on the image, above
    the chin, not overlapping the lid patch — and a box measured onto a muzzle
    satisfies all of that. Nor can the e2e suite tell: its region of interest is
    built from the same box, so a blink wiped over a snout lands inside its own
    rectangle and passes. Until this existed, the only thing between a mistyped
    digit and a dog blinking on its cheek was me looking at a contact sheet, and
    17 of the 23 boxes were proposed by `--suggest` rather than measured (#139).

    This reads the *stored* numbers back off the artwork, so it also fires when
    an image is replaced by a different render of the same character and every
    box silently moves — which is the failure `--suggest` cannot warn about,
    because it is not run again.
    """
    problems = []
    for i, box in enumerate(r.get("eyes", [])):
        if len(box) != 4 or not (0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1):
            continue                      # box_problems already said so; do not say it twice
        white, pupil, area = eye_pixels(im, box)
        if area < 9:
            problems.append(f"{cid}: eye{i} box is {area}px on this image — too small to read")
            continue
        if white < EYE_WHITE:
            problems.append(
                f"{cid}: eye{i} box is {white:.0%} near-white, under {EYE_WHITE:.0%} — "
                f"that is not eye white, so the blink would be wiped over face")
        elif pupil < EYE_PUPIL:
            problems.append(
                f"{cid}: eye{i} box has no pupil in it (biggest ink blob {pupil:.0%} of "
                f"the box, under {EYE_PUPIL:.0%}) — white face, not an eye")
    return problems


def check() -> int:
    from PIL import Image

    if not RIGS.exists():
        print("no rigs.json")
        return 1
    rigs = json.loads(RIGS.read_text())["rigs"]
    chars = json.loads(CHARS.read_text())["characters"]
    problems = []
    read = joints = hand = 0
    for c in chars:
        r = rigs.get(c["id"])
        if not r:
            problems.append(f"{c['id']}: no rig")
            continue
        if not 0.05 < r["neck"] < r["hip"] < 1.0:
            problems.append(f"{c['id']}: joints out of order neck={r['neck']} hip={r['hip']}")
        if len(r["legPivots"]) != r["legParts"]:
            problems.append(f"{c['id']}: {r['legParts']} leg parts but {len(r['legPivots'])} pivots")
        problems += box_problems(c["id"], r)
        f = ASSETS / f"{c['id']}.png"
        if not f.exists():
            # not "clean": the numbers are there and nothing looked at them
            problems.append(f"{c['id']}: no sprite, so its joints were not read back")
            if r.get("eyes") or r.get("lid"):
                problems.append(f"{c['id']}: has face boxes but no sprite, so they were not checked")
            continue
        im = Image.open(f).convert("RGBA")
        problems += joint_problems(c["id"], r, f, im)
        joints += 1
        hand += not r.get("derived")
        if not r.get("eyes") and not r.get("lid"):
            continue
        problems += eye_problems(c["id"], r, im)
        problems += lid_problems(c["id"], r, im)
        read += len(r.get("eyes", [])) + bool(r.get("lid"))
    print(f"{len(rigs)} rigs for {len(chars)} characters, "
          f"{read} face box(es) read back off the artwork, "
          f"{joints} set(s) of joints too — {joints - hand} re-derived from the render, "
          f"{hand} hand-measured and held to the render they were measured on")
    for p in problems:
        print(f"  PROBLEM {p}")
    return 1 if problems else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--grid", help="comma-separated ids to measure boxes off")
    ap.add_argument("--zoom", help="crop the grid to a y range, e.g. 0,0.45")
    ap.add_argument("--suggest", nargs="?", const="", metavar="IDS",
                    help="propose ear/eye/lid boxes as PARTS source (default: every "
                         "character that has none)")
    a = ap.parse_args()
    if a.suggest is not None:
        ids = [i.strip() for i in a.suggest.split(",") if i.strip()]
        return suggest(ids or [c["id"] for c in json.loads(CHARS.read_text())["characters"]
                               if c["id"] not in PARTS])
    if a.grid:
        zoom = tuple(float(v) for v in a.zoom.split(",")) if a.zoom else None
        return grid([i.strip() for i in a.grid.split(",") if i.strip()], zoom)
    return check() if a.check else build(a.sheet)


if __name__ == "__main__":
    sys.exit(main())
