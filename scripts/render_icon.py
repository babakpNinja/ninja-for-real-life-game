#!/usr/bin/env python3
"""Render the home-screen icons from the one drawing the game already has.

iOS will not take an SVG for `apple-touch-icon`, so a phone that adds this game
to its home screen (#354) needs PNGs — and the moment a PNG is drawn by hand it
is a second author of the same dog, free to drift from the favicon nobody would
think to check. So there is exactly one picture, `public/assets/icon.svg`, and
this script photographs it.

Two things it does *not* invent:

  * the surround colour comes from the manifest's `background_color`, which is
    also what the phone paints behind the app while it starts, so the tile and
    the splash cannot disagree;
  * the sizes come from the manifest's `icons` list, plus `APPLE` for the one
    size only `<link rel="apple-touch-icon">` asks for.

The face fills 78% of the tile rather than all of it: iOS rounds the corners of
whatever it is given, and this drawing is a circle 92% of its own box wide, so
edge to edge would have the mask bite into the dog's head.

    python scripts/render_icon.py            # write any icon that is missing or stale
    python scripts/render_icon.py --check    # say what would change, write nothing
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
MANIFEST = APP / "public" / "manifest.webmanifest"
ICON = APP / "public" / "assets" / "icon.svg"

# The one size that is not in the manifest: Safari reads it from a <link> tag,
# and 180 is what an iPhone at 3x asks for.
APPLE = ("assets/icon-180.png", 180)

# How much of the tile the face is allowed, corner to corner. See the docstring.
FACE = 0.78

# The SVG is pasted in as markup rather than loaded through <img src>: a page
# built with `set_content` has no origin, so a `file://` subresource is refused
# — and a refused image still reports `complete`, so the first version of this
# script wrote three tiles of flat blue and said it had worked.
PAGE = """<!doctype html><html><body style="margin:0;width:{n}px;height:{n}px;
background:{bg}"><div style="position:absolute;left:50%;top:50%;width:{face}px;
height:{face}px;transform:translate(-50%,-50%)">{svg}</div>
<style>svg {{ width: 100%; height: 100%; display: block; }}</style></body></html>"""


def wanted(manifest: dict) -> list[tuple[str, int]]:
    """Every PNG that has to exist, as (path relative to public/, pixels)."""
    out = []
    for icon in manifest.get("icons", []):
        if icon.get("type") != "image/png":
            continue
        px = int(str(icon["sizes"]).lower().split("x")[0])
        out.append((icon["src"], px))
    out.append(APPLE)
    return sorted(set(out))


def render(pairs: list[tuple[str, int]], public: Path, bg: str, svg: Path) -> list[str]:
    """Photograph the SVG at each size. Returns the paths written."""
    from playwright.sync_api import sync_playwright

    markup = svg.read_text().strip()
    written = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            for src, px in pairs:
                page = browser.new_page(viewport={"width": px, "height": px},
                                        device_scale_factor=1)
                page.set_content(PAGE.format(n=px, bg=bg, face=round(px * FACE),
                                             svg=markup))
                drawn = page.evaluate("() => document.querySelector('svg')"
                                      "?.getBoundingClientRect().width || 0")
                if drawn < px * FACE - 1:
                    raise SystemExit(f"{src}: the drawing came out {drawn}px wide in a "
                                     f"{px}px tile — nothing to photograph")
                out = public / src
                out.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(out), omit_background=False)
                page.close()
                written.append(src)
        finally:
            browser.close()
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true",
                    help="name the icons that are missing, write nothing")
    args = ap.parse_args(argv)

    manifest = json.loads(MANIFEST.read_text())
    public = MANIFEST.parent
    pairs = wanted(manifest)
    missing = [src for src, _ in pairs if not (public / src).exists()]

    if args.check:
        for src in missing:
            print(f"MISSING  {src}")
        print(f"{len(pairs) - len(missing)}/{len(pairs)} icons present"
              + (f", background {manifest['background_color']}" if not missing else ""))
        return 1 if missing else 0

    written = render(pairs, public, manifest["background_color"], ICON)
    for src in written:
        print(f"wrote {src} ({(public / src).stat().st_size} bytes)")
    print(f"{len(written)} icon(s) from {ICON.name} on {manifest['background_color']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
