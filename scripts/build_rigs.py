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
SHEET = APP / "shots" / "_rig-contact-sheet.png"

ALPHA = 40  # a pixel counts as opaque above this
MIN_RUN = 3  # ignore hairline runs (antialiasing, a stray whisker)

NECK_RANGE = (0.30, 0.58)  # a cartoon dog's chin, as a fraction of its height
HIP_RANGE = (0.66, 0.86)
NECK_DEFAULT, HIP_DEFAULT = 0.45, 0.76
LEG_SAMPLE = 0.94  # the row to measure leg positions on: below the belly, above the feet

# Hand-authored joints, measured off shots/_rig-grid.png. Only the characters
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
        rig["legParts"] = len(rig["legPivots"])
        out[c["id"]] = rig
        how = "measured" if not rig["derived"] else ("clamped:" + ",".join(rig["clamped"]) if rig["clamped"] else "derived")
        print(
            f"  {c['id']:<12} neck={rig['neck']:.3f} hip={rig['hip']:.3f} "
            f"legs={rig['legParts']} pivots={rig['legPivots']}  {how}"
        )

    RIGS.write_text(
        json.dumps(
            {
                "note": "Cut-out rig per character, derived by scripts/build_rigs.py "
                "from the sprite's alpha silhouette. Fractions of image size. "
                "Parts are drawn back-to-front (legs, torso, head) so each part "
                "covers the seam of the one below it.",
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
        d.text((ox + 2, oy + cell - 14), cid, fill=(0, 0, 0))
    sheet.save(SHEET)
    print(f"  sheet -> {SHEET.relative_to(APP)}  (red=neck, blue=hip, green=leg pivots)")


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
    print(f"{len(rigs)} rigs for {len(chars)} characters")
    for p in problems:
        print(f"  PROBLEM {p}")
    return 1 if problems else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet", action="store_true")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    return check() if a.check else build(a.sheet)


if __name__ == "__main__":
    sys.exit(main())
