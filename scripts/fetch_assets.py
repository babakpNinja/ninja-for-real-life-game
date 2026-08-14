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
from typing import NamedTuple

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

# --- action poses -----------------------------------------------------------
# The rig animates one front-facing render by cutting it into bands and
# rotating them. That is fine for breathing and a blink; for a run it tore the
# character apart — leg columns shearing sideways, the tail sliding out as a
# dark slab at hip height. The fix is not a better rig, it is the artist's own
# drawing of the pose: the wiki has genuine side-on running renders.
#
# `--poses` fetches these into public/assets/poses/ and writes the frame sets
# to public/data/poses.json, so `sprites.js` can swap a frame in for a state
# and only fall back to the rig where there is no artwork for it.
#
# What is NOT here matters as much: there is exactly one running render per
# character on the wiki, so a "run" is one frame plus motion, not a six-frame
# cycle — the file format takes a list so more frames are data if they ever
# exist. Where a state has no drawing at all the rig draws it, and RIG_OK below
# is where that has to be said out loud.
#
# A frame is a file name, or a `Frame` when the drawing has to be cut out of a
# bigger picture — see `Frame`.


class Frame(NamedTuple):
    """One pose source: a wiki file, and optionally a box to cut out of it.

    `crop` is (left, top, right, bottom) as fractions of the source, applied
    before anything else. It exists because some poses are only drawn as part
    of a two-character picture; taking a rectangle out of someone's artwork is
    the same act of copying as taking the whole file, and it is credited the
    same way, with the box recorded so the cut can be checked against the
    original.

    A straight rectangle through two overlapping dogs always leaves a piece of
    the other one behind — Bluey's paw hangs over Bingo's — so a cropped frame
    also keeps only its largest connected shape. That is why `crop` is a flag
    for "there is more than one character in this file" and not just a box.
    """

    file: str
    crop: tuple[float, float, float, float] | None = None


def _frames(spec) -> list[Frame]:
    return [f if isinstance(f, Frame) else Frame(f) for f in spec]


POSES = {
    "bluey": {
        "run": ["Bluey-Running.png"],
        "jump": ["Bluey-Leaping.png"],
        "cheer": ["Bluey-Celebrating.png"],
    },
    "bingo": {
        "run": ["Bingo-Running.png"],
        # The only drawing of Bingo off the ground is the one she shares with
        # Bluey, both mid-leap on alpha. Cropped to her side of it.
        "jump": [Frame("Jump_bluey_bingo.png", crop=(0.0, 0.0, 0.4759, 1.0))],
        "cheer": ["Bingo-Dance.png"],
    },
    "bandit": {
        "run": ["Bandit-Obstacle_Course-Running.png"],
    },
    "chilli": {
        # Not a running render — there is no such drawing of Chilli — but a
        # full stride with her weight over the front foot, which is what a run
        # needs and what the rig cannot make out of a standing render. Her
        # cheer is the front-on dance, so the two do not read as the same pose.
        "run": ["Chilli-Island_Rhythms.png"],
        "cheer": ["Chilli-Dancing.png"],
    },
}
POSES_OUT = APP / "public" / "assets" / "poses"
POSES_JSON = APP / "public" / "data" / "poses.json"
SPRITES_JS = APP / "public" / "js" / "sprites.js"


def states() -> set[str]:
    """The animation states, read out of the code that draws them.

    A state named in poses.json that `poseFor` has no case for is a pose
    fetched, credited and never drawn — and a typo ("jumping") looks exactly
    like a deliberate one. sprites.js is the author; this reads its switch
    rather than keeping a second list here to drift.
    """
    body = SPRITES_JS.read_text().split("function poseFor(", 1)[-1]
    found = set(re.findall(r'case "(\w+)":', body))
    if "default:" in body:
        found.add("idle")  # poseFor's default arm; the caller's name for it
    return found


def pose_fallbacks() -> dict[str, str]:
    """The state-to-state fallbacks sprites.js draws, read out of sprites.js.

    `poseFrame` will use one state's artwork for another where it is the same
    drawing (a float is a jump held longer). Coverage has to know about that or
    it reports a gap the player cannot see; sprites.js is the author here,
    exactly as it is for `states`.
    """
    m = re.search(r"const POSE_FALLBACK = \{(.*?)\};", SPRITES_JS.read_text(), re.S)
    return dict(re.findall(r'(\w+):\s*"(\w+)"', m.group(1))) if m else {}


# --- where the rig is allowed to draw a hero --------------------------------
# A playable character can be put into any state in `states()` within two taps,
# and where there is no drawing for it the rig draws it instead — a standing
# render cut into bands, which is the look the pose artwork was fetched to get
# rid of. Sometimes that is fine and sometimes it is the bug, and nothing could
# tell the difference: the pose tests check what poses.json *claims*, so an
# absence was invisible. Every (character, state) with no artwork behind it has
# to be named here with its reason; `--check` fails on one that is not, and
# fails again on an entry that artwork has since made untrue.
#
# "*" is every playable character, for a state where nobody has artwork and the
# reason is about the state rather than about the character.
RIG_OK = {
    ("*", "idle"): "a standing render is what idle wants; the rig only breathes and blinks",
    # Bandit's jump/cheer/float and Chilli's jump/float used to be excused here:
    # there is still no such drawing on the wiki, but the rig is no longer what
    # the player sees. They borrow their own run render through POSE_FALLBACK
    # and move on it, because the rig's front-facing source made them read as a
    # different character the moment they left the ground (#215). Their absence
    # from this table is what keeps that true — `draws` follows the same chain
    # sprites.js does, so if the fallback were removed these five would come
    # back as undeclared gaps rather than quietly returning to the rig.
    ("muffin", "run"): "Muffin has no action render at all: her 43 files are the "
                       "standing render, unboxing screenshots and group shots",
    ("muffin", "jump"): "as above — nothing of Muffin off the ground exists",
    ("muffin", "float"): "as above — nothing for float to borrow",
    ("muffin", "cheer"): "as above — the nearest is the standing render, waving",
}

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

# --- the game's name, authored once -----------------------------------------
# It was "For Real Life!" and it is now "Ana Bingo!", and renaming it meant
# finding eight hand-written copies across five files — a title tag, a meta
# description, an <h1>, a package description, a README heading, a server log
# line and two docstrings. Nothing would have failed if I had missed one, and
# the menu would have disagreed with the tab. Same treatment as the notice: it
# lives here, `fetch()` writes it into asset-credits.json, and `--check` reads
# every visible copy back and compares.
GAME_NAME = "Ana Bingo!"
GAME_TAGLINE = "a heeler family adventure"
OLD_NAME_OK = "old-name-on-purpose:"  # a line may say the old name if it says why

README = APP / "README.md"
INDEX = APP / "public" / "index.html"
MAIN_JS = APP / "public" / "js" / "main.js"
PACKAGE = APP / "package.json"
SERVER_JS = APP / "server.js"

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


def largest_shape(im, alpha: int = 8):
    """Keep the biggest connected opaque shape; erase anything else.

    Only used on a cropped frame, where a rectangle through a two-character
    picture leaves a piece of the other character floating in the corner.
    Returns (image, pixels_erased) so the caller can print how much of the
    source it threw away — a big number means the crop box is wrong.
    """
    px = im.load()
    seen = [[False] * im.width for _ in range(im.height)]
    best, best_n = None, 0
    for sy in range(im.height):
        for sx in range(im.width):
            if seen[sy][sx] or px[sx, sy][3] <= alpha:
                continue
            shape, stack = [], [(sx, sy)]
            seen[sy][sx] = True
            while stack:
                x, y = stack.pop()
                shape.append((x, y))
                for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                    if 0 <= nx < im.width and 0 <= ny < im.height and not seen[ny][nx] \
                            and px[nx, ny][3] > alpha:
                        seen[ny][nx] = True
                        stack.append((nx, ny))
            if len(shape) > best_n:
                best, best_n = set(shape), len(shape)
    erased = 0
    if best is not None:
        for y in range(im.height):
            for x in range(im.width):
                if px[x, y][3] > alpha and (x, y) not in best:
                    r, g, b, _ = px[x, y]
                    px[x, y] = (r, g, b, 0)
                    erased += 1
    return im, erased


def normalise(raw: bytes, dest: Path, crop: tuple | None = None) -> dict:
    """Trim transparent margins, cap height, write PNG. Returns size info."""
    from PIL import Image

    im = Image.open(io.BytesIO(raw))
    im = im.convert("RGBA")
    stray = 0
    if crop:
        l, t, r, b = crop
        im = im.crop((round(l * im.width), round(t * im.height),
                      round(r * im.width), round(b * im.height)))
        im, stray = largest_shape(im)
    im, cleared = strip_baked_shadow(im)
    bbox = im.split()[3].getbbox()
    if bbox:
        im = im.crop(bbox)
    if im.height > MAX_H:
        w = max(1, round(im.width * MAX_H / im.height))
        im = im.resize((w, MAX_H), Image.LANCZOS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    im.save(dest, "PNG", optimize=True)
    info = {
        "w": im.width,
        "h": im.height,
        "bytes": dest.stat().st_size,
        "cutout": cutout_score(im),
        "shadow_px": cleared,
    }
    if crop:
        info["crop"] = [round(v, 4) for v in crop]
        info["stray_px"] = stray
    return info


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


def pose_id(cid: str, state: str, i: int) -> str:
    return f"{cid}:{state}:{i}"


def fetch_poses(force: bool) -> int:
    """Fetch the action-pose renders and write public/data/poses.json.

    Each frame is credited exactly like a character render — same normalise(),
    same shadow strip, same cutout score — because it is the same kind of
    thing: someone else's artwork, shipped here, which has to say where it came
    from. They live under a separate `poses` key so the per-character checks
    above keep meaning "every character has its render".
    """
    credits = json.loads(CREDITS.read_text()) if CREDITS.exists() else {}
    poses = credits.get("poses", {})
    frames: dict[str, dict[str, list[str]]] = {}
    problems = []
    for cid, states in POSES.items():
        for state, files in states.items():
            for i, frame in enumerate(_frames(files)):
                name = frame.file
                key = pose_id(cid, state, i)
                rel = f"assets/poses/{cid}-{state}-{i}.png"
                dest = APP / "public" / rel
                frames.setdefault(cid, {}).setdefault(state, []).append(rel)
                if dest.exists() and not force and key in poses:
                    print(f"  have  {key}")
                    continue
                try:
                    url = file_image(f"File:{name}")
                    if not url:
                        problems.append(f"{key}: File:{name} has no image")
                        continue
                    info = normalise(_get(url), dest, frame.crop)
                except Exception as exc:
                    problems.append(f"{key}: {exc}")
                    continue
                poses[key] = {
                    "file": rel,
                    "source": "https://bluey.fandom.com/wiki/"
                    + urllib.parse.quote(f"File:{name}".replace(" ", "_")),
                    "image": url.split("/revision/")[0],
                    "retrieved": date.today().isoformat(),
                    **info,
                }
                flag = "  OPAQUE?" if info["cutout"] < MIN_CUTOUT else ""
                cut = f"  cropped, {info['stray_px']}px of a neighbour erased" if frame.crop else ""
                print(
                    f"  got   {key}  {info['w']}x{info['h']}"
                    f"  {info['bytes'] // 1024}KB  cutout={info['cutout']}"
                    f"  <- {name}{cut}{flag}"
                )
                time.sleep(0.4)

    credits["poses"] = dict(sorted(poses.items()))
    CREDITS.write_text(json.dumps(credits, indent=2) + "\n")
    POSES_JSON.write_text(json.dumps({"frames": frames}, indent=2) + "\n")
    for p in problems:
        print(f"  MISS  {p}")
    return 1 if problems else 0


def pose_problems() -> list[str]:
    """poses.json, the files it names and their credits must all agree.

    Three ways this goes wrong and none of them fail on their own: a frame in
    poses.json with no file (the character silently drops back to the rig mid
    chapter), a file on disk nobody credits, and a state named here that
    `sprites.js` never asks for — a pose fetched, credited and never drawn.
    """
    if not POSES_JSON.exists():
        return ["no public/data/poses.json — run fetch_assets.py --poses"]
    credits = json.loads(CREDITS.read_text())
    poses = credits.get("poses", {})
    frames = json.loads(POSES_JSON.read_text()).get("frames", {})
    ids = {c["id"] for c in load_chars()}
    problems = []
    named = set()
    for cid, by_state in frames.items():
        if cid not in ids:
            problems.append(f"poses.json has frames for {cid}, which is not a character")
        for state, files in by_state.items():
            if state not in states():
                problems.append(
                    f"{cid}: poses.json names the state {state!r}, which sprites.js "
                    f"never draws — one of {sorted(states())}"
                )
            for i, rel in enumerate(files):
                named.add(rel)
                key = pose_id(cid, state, i)
                if not (APP / "public" / rel).exists():
                    problems.append(f"{key}: {rel} missing on disk")
                entry = poses.get(key)
                if not entry:
                    problems.append(f"{key}: no credit entry")
                    continue
                if entry.get("file") != rel:
                    problems.append(f"{key}: credited as {entry.get('file')}, drawn from {rel}")
                for k in ("source", "image", "retrieved"):
                    if not entry.get(k):
                        problems.append(f"{key}: credit entry has no {k}")
                if entry.get("cutout", 0) < MIN_CUTOUT:
                    problems.append(
                        f"{key}: cutout {entry.get('cutout')} < {MIN_CUTOUT} — that is a "
                        "screenshot with a background, not a transparent render"
                    )
    for key, entry in poses.items():
        if entry.get("file") not in named:
            problems.append(f"{key}: credited but poses.json never draws it")
    if POSES_OUT.exists():
        for f in sorted(POSES_OUT.glob("*.png")):
            rel = f"assets/poses/{f.name}"
            if rel not in named:
                problems.append(f"{rel}: on disk but poses.json never draws it")
    return problems


def draws(cid: str, state: str, frames: dict, fallbacks: dict[str, str]) -> str | None:
    """The state whose artwork would be drawn for `cid` in `state`, or None.

    `sprites.js` asks for the state's own frames first and then walks
    POSE_FALLBACK until it reaches a state that has some, and this answers the
    same question about the data — so a gap here is a gap the player can see.

    A chain and not a single hop, because `poseFile` is: float borrows the jump,
    and a jump nobody drew borrows the run, so a held jump by Bandit is two
    links from artwork. Stopping at one would report a gap that is drawn, which
    would then have to be excused in RIG_OK — a comment claiming the rig draws
    something the player never sees it draw. `seen` guards a fallback that ever
    points in a circle, exactly as the loop in `poseFile` does.
    """
    seen: set[str] = set()
    want = state
    while want and want not in seen:
        seen.add(want)
        if (frames.get(cid) or {}).get(want):
            return want
        want = fallbacks.get(want)
    return None


def coverage_problems() -> list[str]:
    """Every state a playable character can be put into is drawn on purpose.

    Either there is artwork for it (its own, or the one state sprites.js falls
    back to), or RIG_OK says the rig is acceptable there and why. The absence
    of a pose cannot fail any other check in this file — poses.json is the only
    thing they read, and poses.json does not know what is missing from it.

    Both directions: an undeclared gap, and a declaration artwork has since
    made untrue (which would otherwise sit here reading as a known limitation
    long after it stopped being one).
    """
    if not POSES_JSON.exists():
        return []  # pose_problems has already said this louder
    frames = json.loads(POSES_JSON.read_text()).get("frames", {})
    fallbacks = pose_fallbacks()
    playable = [c["id"] for c in load_chars() if c.get("playable")]
    every = sorted(states())
    problems = []
    for cid in playable:
        for state in every:
            drawn = draws(cid, state, frames, fallbacks)
            why = RIG_OK.get((cid, state)) or RIG_OK.get(("*", state))
            if drawn and (cid, state) in RIG_OK:
                problems.append(
                    f"{cid}:{state}: RIG_OK says the rig draws this, but there is "
                    f"{drawn} artwork for it now — drop the line"
                )
            elif not drawn and not why:
                problems.append(
                    f"{cid} is playable and has no pose artwork for {state!r}, so the "
                    f"rig draws it. Fetch a render, or say why the rig is right here "
                    f"in RIG_OK[({cid!r}, {state!r})]"
                )
    for (cid, state), why in RIG_OK.items():
        if cid != "*" and cid not in playable:
            problems.append(f"RIG_OK names {cid}, which is not a playable character")
        if state not in every:
            problems.append(f"RIG_OK names the state {state!r}, which nothing draws")
        elif cid == "*" and all(draws(c, state, frames, fallbacks) for c in playable):
            problems.append(
                f"RIG_OK's '*' line for {state!r} is out of date: every playable "
                f"character has artwork for it now"
            )
        elif not str(why).strip():
            problems.append(f"{cid}:{state}: the RIG_OK entry gives no reason")
    return problems


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


def name_problems() -> list[str]:
    """Every visible copy of the game's name must still be the game's name.

    The name is not data the app fetches — the tab title and the meta
    description have to be right before any JavaScript runs, so they are static
    text, and static text is what drifts. `GAME_NAME` above is the author; each
    copy below is read back out of the file that carries it and compared.

    `package.json`'s *name* is deliberately not on this list: it is the Railway
    service (`for-real-life-game`), which stays put so the URL Babak already has
    keeps working. Only its human-readable description is a copy of this fact.
    """
    problems = []

    def want(where, got, must_contain=GAME_NAME):
        if got is None:
            problems.append(f"{where} is missing — nothing there to carry the game's name")
        elif must_contain not in got:
            problems.append(f"{where} does not say {must_contain!r}:\n      is: {got}")

    index = INDEX.read_text()
    m = re.search(r"<title>(.*?)</title>", index, re.S)
    want("index.html <title>", _flat(html.unescape(m.group(1))) if m else None)
    m = re.search(r'<meta name="description" content="(.*?)"', index, re.S)
    want("index.html meta description", _flat(html.unescape(m.group(1))) if m else None)

    # both of them: index.html carries the menu heading as static markup so the
    # panel is not empty before main.js runs, and main.js writes it again when it
    # rebuilds the overlay. Checking one found eight of the nine copies.
    for where in (INDEX, MAIN_JS):
        want(f'{where.name} <h1 class="title">',
             _tag_text(r'<h1 class="title">(.*?)</h1>', where))

    pkg = json.loads(PACKAGE.read_text())
    want("package.json description", pkg.get("description"))

    m = re.search(r"^#\s+(.*)$", README.read_text(), re.M)
    want("README.md's heading", _flat(m.group(1)) if m else None)

    m = re.search(r"console\.log\(`(.*?) listening", SERVER_JS.read_text())
    want("server.js's startup line", m.group(1) if m else None)

    # ...and no copy of the old one left behind. A rename that half happened is
    # worse than either name: the tab and the menu disagree and it reads as a
    # different site. Comments and docstrings count — they are what the next
    # reader believes.
    #
    # One sentence has to say the old name out loud: the README paragraph that
    # explains why the URL still carries it. A scan for a phrase always ends up
    # banning its own explanation, and the wrong answer is to reword the
    # explanation until the scan is happy. So a line may opt out by saying so,
    # in the file, with the reason attached — a marker with nothing after it is
    # still a problem, because "why" is the part worth reading.
    for path in (INDEX, MAIN_JS, README, PACKAGE, SERVER_JS, APP / "public" / "js" / "chapters.js"):
        for i, line in enumerate(path.read_text().splitlines(), 1):
            # the <h1> writes it as "For Real&nbsp;Life!", which is the same
            # words to a reader and a different string to a scan
            line = html.unescape(line).replace("\xa0", " ")  # escaped: an invisible one reads as a no-op
            if OLD_NAME_OK in line:
                if not line.split(OLD_NAME_OK, 1)[1].strip():
                    problems.append(f"{path.name}:{i} claims {OLD_NAME_OK} and gives no reason")
                continue
            if "For Real Life" in line and "for-real-life-game" not in line:
                problems.append(f"{path.name}:{i} still says the old name: {line.strip()}")
    return problems


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
    problems += name_problems()
    problems += pose_problems()
    problems += coverage_problems()
    frames = sum(len(f) for s in json.loads(POSES_JSON.read_text())["frames"].values()
                 for f in s.values()) if POSES_JSON.exists() else 0
    print(f"{len(assets)} assets, {total // 1024}KB total, {shipped} renders on disk, "
          f"{frames} pose frames, {len(RIG_OK)} states left to the rig on purpose, "
          f"and every visible copy of {GAME_NAME!r} checked")
    for p in problems:
        print(f"  PROBLEM {p}")
    return 1 if problems else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--poses", action="store_true", help="fetch the action-pose frames")
    a = ap.parse_args()
    if a.check:
        return check()
    if a.poses:
        return fetch_poses(a.force)
    return fetch(a.force)


if __name__ == "__main__":
    sys.exit(main())
