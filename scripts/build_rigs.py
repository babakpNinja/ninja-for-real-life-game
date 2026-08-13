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
  python3 scripts/build_rigs.py --check    # rigs.json covers every asset
  python3 scripts/build_rigs.py --grid bluey,bingo   # measuring grids for the boxes
"""
from __future__ import annotations

import argparse
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

NECK_RANGE = (0.30, 0.58)  # a cartoon dog's chin, as a fraction of its height
HIP_RANGE = (0.66, 0.86)
NECK_DEFAULT, HIP_DEFAULT = 0.45, 0.76
LEG_SAMPLE = 0.94  # the row to measure leg positions on: below the belly, above the feet

# Hand-authored joints, measured off shots/_debug/rig-grid.png. Only the characters
# that get large motion need this: the four playable heroes and the five
# cameos, which cheer. Everyone else appears in the gallery at idle, where the
# rotations are a couple of degrees and a joint line a few percent off is not
# visible. Deriving a chin line from a silhouette is unreliable (ears and
# raised arms dominate the row widths), so those nine are measured, not guessed.
OVERRIDES: dict[str, dict] = {
    "bluey": {"neck": 0.55, "hip": 0.76},
    "bingo": {"neck": 0.57, "hip": 0.78},
    "bandit": {"neck": 0.52, "hip": 0.78},
    "chilli": {"neck": 0.53, "hip": 0.77},
    "muffin": {"neck": 0.50, "hip": 0.78},
    "lucky": {"neck": 0.42, "hip": 0.75},
    "nana_chris": {"neck": 0.44, "hip": 0.79},
    # Chattermax is a toy owl: no neck, and two little feet right at the bottom.
    "chattermax": {"neck": 0.34, "hip": 0.90},
    # Snickers is a dachshund — long body, and his legs never separate.
    "snickers": {"neck": 0.36, "hip": 0.80},
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
# There is no silhouette rule for these: an ear is the same colour as the head
# it grows out of, and a tail only exists in a render where the character
# happens to be standing side-on. So they are measured, only for the characters
# who appear large (the four heroes and the cameos who cheer), and every one is
# optional — sprites.js draws a rig without them exactly as it did before.
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
        "lid": [0.64, 0.43, 0.70, 0.47],
    },
    "bandit": {
        "ears": [
            {"box": [0.17, 0.0, 0.37, 0.17], "pivot": [0.27, 0.17]},
            {"box": [0.49, 0.0, 0.70, 0.17], "pivot": [0.60, 0.17]},
        ],
        "eyes": [[0.33, 0.21, 0.50, 0.39], [0.505, 0.215, 0.575, 0.31]],
        "lid": [0.10, 0.33, 0.14, 0.40],
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
    "muffin": {
        "ears": [
            {"box": [0.29, 0.0, 0.53, 0.20], "pivot": [0.41, 0.20]},
            {"box": [0.55, 0.0, 0.81, 0.20], "pivot": [0.68, 0.20]},
        ],
        "eyes": [[0.40, 0.29, 0.62, 0.48], [0.635, 0.29, 0.78, 0.43]],
        "lid": [0.28, 0.32, 0.34, 0.40],
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
}


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
    hip = None
    for y in range(bottom, top, -1):
        if len(rr[y]) < 2:
            if hip is not None:
                break
            continue
        hip = y
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


def check() -> int:
    if not RIGS.exists():
        print("no rigs.json")
        return 1
    rigs = json.loads(RIGS.read_text())["rigs"]
    chars = json.loads(CHARS.read_text())["characters"]
    problems = []
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
    print(f"{len(rigs)} rigs for {len(chars)} characters")
    for p in problems:
        print(f"  PROBLEM {p}")
    return 1 if problems else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--grid", help="comma-separated ids to measure boxes off")
    ap.add_argument("--zoom", help="crop the grid to a y range, e.g. 0,0.45")
    a = ap.parse_args()
    if a.grid:
        zoom = tuple(float(v) for v in a.zoom.split(",")) if a.zoom else None
        return grid([i.strip() for i in a.grid.split(",") if i.strip()], zoom)
    return check() if a.check else build(a.sheet)


if __name__ == "__main__":
    sys.exit(main())
