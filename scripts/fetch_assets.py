#!/usr/bin/env python3
"""Fetch official character artwork for the gallery + gameplay sprites.

Personal, non-commercial fan project. Every file written here records where it
came from in public/data/asset-credits.json so the credits screen can show it.

  python3 scripts/fetch_assets.py            # fetch anything missing
  python3 scripts/fetch_assets.py --force    # re-fetch everything
  python3 scripts/fetch_assets.py --check    # verify assets + credits agree
"""
from __future__ import annotations

import argparse
import html
import io
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
CHARS = APP / "public" / "data" / "characters.json"
CREDITS = APP / "public" / "data" / "asset-credits.json"
OUT = APP / "public" / "assets" / "characters"

WIKI = "https://bluey.fandom.com/api.php"
UA = "ninja-bluey-fanproject/1.0 (personal, non-commercial)"

# Wiki page title per character id. The wiki's own search is too loose for
# short names ("Lucky" hits an episode), so the mapping is explicit.
TITLES = {
    "bluey": "Bluey Heeler",
    "bingo": "Bingo Heeler",
    "bandit": "Bandit Heeler",
    "chilli": "Chilli Heeler",
    "muffin": "Muffin Heeler",
    "socks": "Socks Heeler",
    "stripe": "Stripe Heeler",
    "trixie": "Trixie Heeler",
    "nana_chris": "Chris Heeler",
    "bob": "Bob Heeler",
    "lucky": "Lucky Cattle Dog",
    "judo": "Judo Cavalier",
    "honey": "Honey Beagle",
    "coco": "Coco Poodle",
    "snickers": "Snickers Dachshund",
    "chloe": "Chloe Terrier",
    "mackenzie": "Mackenzie Border Collie",
    "rusty": "Rusty Kelpie",
    "indy": "Indy Afghan Hound",
    "winton": "Winton Bulldog",
    "jean_luc": "Jean-Luc Labrador",
    "calypso": "Calypso",
    "frisky": "Frisky Sheepdog",
    "rad": "Radley Heeler",
    "chattermax": "Chattermax",
}

# Titles the wiki may redirect from / alternatives to try in order.
FALLBACKS = {
    "lucky": ["Lucky", "Lucky (Bluey)"],
    "judo": ["Judo"],
    "honey": ["Honey"],
    "coco": ["Coco"],
    "snickers": ["Snickers"],
    "chloe": ["Chloe"],
    "mackenzie": ["Mackenzie"],
    "rusty": ["Rusty"],
    "indy": ["Indy"],
    "winton": ["Winton"],
    "jean_luc": ["Jean-Luc", "Jean Luc"],
    "frisky": ["Frisky"],
    "rad": ["Rad Heeler", "Rad"],
    "nana_chris": ["Nana Chris", "Chris Heeler (Nana)"],
    "bob": ["Bob Heeler", "Bob"],
    "calypso": ["Calypso Cavoodle", "Ms. Calypso"],
    "chattermax": ["Chattermax (toy)"],
}

# The page's own infobox image is usually a clean transparent render, but not
# always: Trixie's was an opaque screenshot and Chattermax has no page of his
# own (he is listed under "Toys"). Name the file directly in those cases.
FILE_OVERRIDES = {
    "trixie": ("File:Aunt Trixie.png", "Trixie Heeler"),
    "chattermax": ("File:Bluey.tv - Chattermax Icon.png", "Chattermax"),
}

MAX_H = 512  # stored height; sprites are drawn ~1/4 of this on screen
MIN_CUTOUT = 0.12  # below this the "render" is really a screenshot with a background

# --- the licensing fact, authored once --------------------------------------
# Before the artwork shipped the README said "no copyrighted art is used or
# shipped"; the moment 25 renders landed that was false, and nothing would have
# failed if I had not happened to reread it. So the sentence lives here, gets
# written into asset-credits.json by fetch(), and every other copy of it in the
# app is checked against that file by --check.
NOTICE_SHORT = "Fan-made, unofficial and non-commercial. Bluey © Ludo Studio Pty Ltd."
NOTICE = (
    NOTICE_SHORT + " Character names and artwork are the property of their "
    "respective owners. Artwork retrieved from the community Bluey wiki; each "
    "image links to its source page."
)
SOURCE_SITE = "https://bluey.fandom.com/"

README = APP / "README.md"
INDEX = APP / "public" / "index.html"
MAIN_JS = APP / "public" / "js" / "main.js"

# Claims that were true while every character was drawn on a canvas and are
# false now that renders ship. Named specifically — the check is about this
# fact, not about prose in general: "the props are drawn from scratch" is still
# true, so a phrase only counts when its own sentence is about the characters.
STALE_CLAIMS = (
    ("drawn from scratch", "character"),
    ("drawn procedurally", "character"),
    ("no copyrighted", None),
)


def _get(url: str, tries: int = 3) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    last = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read()
        except Exception as exc:  # network flake, not a missing page
            last = exc
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"GET {url}: {last}")


def page_image(titles: list[str]) -> tuple[str, str] | None:
    """Return (page_title, image_url) for the first title that has one."""
    q = urllib.parse.urlencode(
        {
            "action": "query",
            "titles": "|".join(titles),
            "prop": "pageimages",
            "piprop": "original",
            "redirects": "1",
            "format": "json",
        }
    )
    data = json.loads(_get(f"{WIKI}?{q}").decode("utf-8"))
    pages = data.get("query", {}).get("pages", {})
    # Preserve the caller's preference order rather than the API's page order.
    by_title = {p.get("title"): p for p in pages.values()}
    norm = {t.lower(): t for t in by_title}
    for want in titles:
        p = by_title.get(want) or by_title.get(norm.get(want.lower(), ""))
        if p and p.get("original", {}).get("source"):
            return p["title"], p["original"]["source"]
    # A redirect can land on a differently-named page; take any hit.
    for p in pages.values():
        if p.get("original", {}).get("source"):
            return p["title"], p["original"]["source"]
    return None


def file_image(file_title: str) -> str | None:
    """Resolve a File: page to its full-size URL."""
    q = urllib.parse.urlencode(
        {
            "action": "query",
            "titles": file_title,
            "prop": "imageinfo",
            "iiprop": "url",
            "format": "json",
        }
    )
    data = json.loads(_get(f"{WIKI}?{q}").decode("utf-8"))
    for p in data.get("query", {}).get("pages", {}).values():
        for ii in p.get("imageinfo", []):
            if ii.get("url"):
                return ii["url"]
    return None


SHADOW_MAX_ALPHA = 170  # a baked contact shadow is a translucent wash
SHADOW_MAX_CHROMA = 22  # ...and it is grey: little spread between R, G and B


def strip_baked_shadow(im):
    """Erase the flat grey ellipse these renders sit on.

    The game draws its own shadow that tracks the character's height, so a
    baked one slides along the ground and reads as a bug. Both wash colours in
    use here ((37,33,33,54) and (187,189,193,128)) are translucent and grey,
    which no part of a character is. Returns (image, pixels_cleared).
    """
    px = im.load()
    cleared = 0
    for y in range(im.height):
        for x in range(im.width):
            r, g, b, a = px[x, y]
            if 0 < a < SHADOW_MAX_ALPHA and max(r, g, b) - min(r, g, b) < SHADOW_MAX_CHROMA:
                px[x, y] = (r, g, b, 0)
                cleared += 1
    return im, cleared


def normalise(raw: bytes, dest: Path) -> dict:
    """Trim transparent margins, cap height, write PNG. Returns size info."""
    from PIL import Image

    im = Image.open(io.BytesIO(raw))
    im = im.convert("RGBA")
    im, cleared = strip_baked_shadow(im)
    bbox = im.split()[3].getbbox()
    if bbox:
        im = im.crop(bbox)
    if im.height > MAX_H:
        w = max(1, round(im.width * MAX_H / im.height))
        im = im.resize((w, MAX_H), Image.LANCZOS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    im.save(dest, "PNG", optimize=True)
    return {
        "w": im.width,
        "h": im.height,
        "bytes": dest.stat().st_size,
        "cutout": cutout_score(im),
        "shadow_px": cleared,
    }


def cutout_score(im) -> float:
    """Fraction of fully-transparent pixels.

    A character render on alpha sits around 0.4-0.6. A screenshot pasted into
    the infobox scores ~0.0 and looks wrong the moment it is drawn over the
    game background, so the number is worth storing and asserting on.
    """
    a = im.split()[3]
    n = a.width * a.height
    clear = sum(c for v, c in zip(range(256), a.histogram()) if v < 8)
    return round(clear / n, 3)


def load_chars() -> list[dict]:
    d = json.loads(CHARS.read_text())
    return d if isinstance(d, list) else d["characters"]


def fetch(force: bool) -> int:
    credits = json.loads(CREDITS.read_text()) if CREDITS.exists() else {}
    assets = credits.get("assets", {})
    problems = []
    for c in load_chars():
        cid = c["id"]
        dest = OUT / f"{cid}.png"
        if dest.exists() and not force and cid in assets:
            print(f"  have  {cid}")
            continue
        titles = [TITLES.get(cid, c["name"])] + FALLBACKS.get(cid, [])
        try:
            if cid in FILE_OVERRIDES:
                file_title, credit_page = FILE_OVERRIDES[cid]
                url = file_image(file_title)
                if not url:
                    problems.append(f"{cid}: override {file_title} has no image")
                    continue
                title = credit_page
            else:
                hit = page_image(titles)
                if not hit:
                    problems.append(f"{cid}: no page image for {titles}")
                    continue
                title, url = hit
            info = normalise(_get(url), dest)
        except Exception as exc:
            problems.append(f"{cid}: {exc}")
            continue
        assets[cid] = {
            "file": f"assets/characters/{cid}.png",
            "source": f"https://bluey.fandom.com/wiki/{urllib.parse.quote(title.replace(' ', '_'))}",
            "image": url.split("/revision/")[0],
            "retrieved": date.today().isoformat(),
            **info,
        }
        flag = "  OPAQUE?" if info["cutout"] < MIN_CUTOUT else ""
        print(
            f"  got   {cid}  {info['w']}x{info['h']}  {info['bytes'] // 1024}KB"
            f"  cutout={info['cutout']}  <- {title}{flag}"
        )
        time.sleep(0.4)

    credits["assets"] = dict(sorted(assets.items()))
    # Assigned, not setdefault: this file is the one author of the notice, so an
    # edit made here must land rather than being kept out by an older copy.
    credits["notice"] = NOTICE
    credits["notice_short"] = NOTICE_SHORT
    credits["source_site"] = SOURCE_SITE
    CREDITS.write_text(json.dumps(credits, indent=2) + "\n")
    for p in problems:
        print(f"  MISS  {p}")
    return 1 if problems else 0


def _flat(text: str) -> str:
    """One long line, so a sentence wrapped across a README still matches."""
    return " ".join(text.split())


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", _flat(text)) if s]


def _prose_only(markdown: str) -> str:
    """Drop fenced code blocks and table rows.

    A file listing ("art.js — props, backdrops and the fallback dog, drawn
    procedurally") has no full stops, so it reads as one enormous sentence that
    mentions everything and trips every phrase. Claims live in prose.
    """
    out, fenced = [], False
    for line in markdown.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced or line.lstrip().startswith("|"):
            continue
        out.append(re.sub(r"^\s*>\s?", "", line))
    return "\n".join(out)


def _tag_text(pattern: str, path: Path) -> str | None:
    """The visible text of the first element matching pattern, or None."""
    m = re.search(pattern, path.read_text(), re.S)
    if not m:
        return None
    inner = re.sub(r"<[^>]+>", " ", m.group(1))
    return _flat(html.unescape(inner).replace(" ", " "))


def prose_problems(credits: dict, shipped: int) -> list[str]:
    """Every hand-written copy of the licensing notice must still be the notice.

    `shipped` is how many character renders are on disk: the false claims are
    only false once there is artwork to be wrong about.
    """
    notice, short = credits.get("notice", ""), credits.get("notice_short", "")
    if not notice or not short:
        return ["asset-credits.json has no notice/notice_short — run fetch_assets.py"]

    problems = []
    if not notice.startswith(short):
        problems.append(
            f"notice_short is not how notice begins, so the menu line and the "
            f"credits screen say different things:\n      short:  {short}\n"
            f"      notice: {notice}"
        )

    readme = _flat(_prose_only(README.read_text()))
    if notice not in readme:
        problems.append(
            "README.md does not carry the notice from asset-credits.json — "
            f"paste this sentence into its licensing paragraph:\n      {notice}"
        )
    if shipped:
        for sentence in _sentences(_prose_only(README.read_text())):
            low = sentence.lower()
            for phrase, alongside in STALE_CLAIMS:
                if phrase in low and (alongside is None or alongside in low):
                    problems.append(
                        f"README.md still claims {sentence!r} — false, "
                        f"{shipped} character renders ship in "
                        f"{OUT.relative_to(APP)}/"
                    )

    splash = _tag_text(r'<p class="credits">(.*?)</p>', INDEX)
    if splash is None:
        problems.append('index.html has no <p class="credits"> — the boot splash lost its notice')
    elif splash != short:
        problems.append(
            f"index.html's boot splash notice has drifted:\n      is:     {splash}"
            f"\n      should: {short}"
        )

    m = re.search(r'const NOTICE_SHORT = "(.*?)";', MAIN_JS.read_text())
    if not m:
        problems.append("main.js has no NOTICE_SHORT constant to fall back on")
    elif m.group(1) != short:
        problems.append(
            f"main.js NOTICE_SHORT has drifted from asset-credits.json:\n"
            f"      is:     {m.group(1)}\n      should: {short}"
        )
    return problems


def check() -> int:
    """Assets and credits must agree, and cover every character."""
    problems = []
    if not CREDITS.exists():
        print("no asset-credits.json")
        return 1
    credits = json.loads(CREDITS.read_text())
    assets = credits.get("assets", {})
    total = 0
    for c in load_chars():
        cid = c["id"]
        entry = assets.get(cid)
        if not entry:
            problems.append(f"{cid}: no credit entry")
            continue
        f = APP / "public" / entry["file"]
        if not f.exists():
            problems.append(f"{cid}: {entry['file']} missing on disk")
            continue
        total += f.stat().st_size
        for k in ("source", "image", "retrieved"):
            if not entry.get(k):
                problems.append(f"{cid}: credit entry has no {k}")
        if "cutout" not in entry:
            problems.append(f"{cid}: credit entry has no cutout score")
        elif entry["cutout"] < MIN_CUTOUT:
            problems.append(
                f"{cid}: cutout {entry['cutout']} < {MIN_CUTOUT} — that is a "
                "screenshot with a background, not a transparent render"
            )
    for cid in assets:
        if cid not in {c["id"] for c in load_chars()}:
            problems.append(f"{cid}: credited but not a character")
    shipped = len(list(OUT.glob("*.png"))) if OUT.exists() else 0
    problems += prose_problems(credits, shipped)
    print(f"{len(assets)} assets, {total // 1024}KB total, {shipped} renders on disk")
    for p in problems:
        print(f"  PROBLEM {p}")
    return 1 if problems else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    return check() if a.check else fetch(a.force)


if __name__ == "__main__":
    sys.exit(main())
