#!/usr/bin/env python3
"""What "safe to show" means for *For Real Life!* (#29).

``tools/demoready.py`` already knows the game is up and that the menu renders,
and ``uptime`` already holds the content floors (25 characters, 5 chapters). This
hook is for what neither can see over a status code:

- **cast** — the roster is the game's whole content. A truncated or half-written
  deploy still serves 200s: characters with no palette draw as nothing, a
  duplicate id silently replaces another dog, and a roster the server disagrees
  with means ``/api/health`` and the file it counts came from different builds.
- **modules** — everything here is ES modules loaded by the browser, with no
  bundler and no build step: ``index.html`` names one entry point and each module
  imports the next. A module that did not deploy is a 404 the server answers
  instantly and cheerfully, and the page then shows a menu that does nothing.
  Walking the import graph is the closest HTTP equivalent of "does it run".
- **styles** — the same half-landed deploy that loses a module can lose
  ``css/style.css``, and nothing above would notice: the game runs, serves 200s
  and renders a menu, unstyled and collapsed. A stylesheet the page links and the
  server does not have is DOWN; a missing icon or manifest is only DIRTY.
- **credits** — it is an unofficial fan tribute. The disclaimer is not decoration;
  showing this to anyone with it missing is the embarrassing case, which is what
  DIRTY means here.

There is deliberately no staleness or dirty-state check: progress lives in each
visitor's ``localStorage``, so there is no server-side state for one person's
play session to leave behind. That is said out loud as a SKIPPED check rather
than left as a silent gap — a check nobody made reads exactly like one that
passed.

HTTP only, ~7 requests, no browser: this runs on the daily/weekly crons, and a
Playwright launch per demo is the kind of cost that gets a cron switched off.
"""

from __future__ import annotations

import json
import posixpath
import re
import urllib.error
import urllib.request

TIMEOUT = 30
READY, STALE, DIRTY, DOWN, SKIPPED = "READY", "STALE", "DIRTY", "DOWN", "SKIPPED"

# What the game needs of every dog to be drawable and namable. `art.js` reads the
# palette directly, so a character missing one is drawn as a hole.
REQUIRED_FIELDS = ("id", "name", "species", "role", "palette")
REQUIRED_COLOURS = ("coat", "belly", "patch")

# The roster the menu offers. Fewer than this and the chapter select is a
# different game from the one that was signed off.
MIN_PLAYABLE = 5

ENTRY = re.compile(r'<script[^>]+type="module"[^>]+src="([^"]+)"')

# The page's <link> tags, read in two steps because the attributes come in any
# order: one regex per attribute would have to be written twice over.
LINK = re.compile(r"<link\b[^>]*>", re.I)
ATTR = re.compile(r'(\w[\w-]*)\s*=\s*"([^"]*)"')

# An href the browser fetches from this site. A data: URI (the icon today) is
# already in the page, and an absolute URL is somebody else's deploy: asking for
# either would report on something this check cannot fix.
EXTERNAL = ("data:", "http://", "https://", "//", "mailto:")

# The three ways one module can pull in another. Only the first is used today,
# but the other two are the ones that would go *unnoticed*: a module the walk
# cannot see is a 404 nobody reports, so they are matched before they are used.
IMPORT = re.compile(r'^\s*(?:import|export)\s+[^;]*?from\s+["\'](\.{1,2}/[^"\']+)["\']', re.M)
BARE_IMPORT = re.compile(r'^\s*import\s+["\'](\.{1,2}/[^"\']+)["\']', re.M)   # side effects only
LAZY_IMPORT = re.compile(r'\bimport\(\s*["\'](\.{1,2}/[^"\']+)["\']')         # dynamic import()

# The one line that has to survive every redeploy of a fan-made tribute.
CREDIT_MARKERS = ("Fan-made", "Ludo Studio")


def _url(base: str, path: str) -> str:
    return base.rstrip("/") + "/" + path.lstrip("/")


def _get(base: str, path: str) -> tuple[int, str]:
    """(status, body). A 404 is an answer, not an exception — it is the finding."""
    try:
        with urllib.request.urlopen(_url(base, path), timeout=TIMEOUT) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""


def _json(base: str, path: str):
    status, body = _get(base, path)
    if status != 200:
        raise RuntimeError(f"{path} answered {status}")
    return json.loads(body)


def cast(base: str) -> dict:
    """Is every dog on the deployed roster complete, unique, and counted?"""
    roster = _json(base, "/data/characters.json").get("characters") or []
    health = _json(base, "/api/health")

    broken = [c.get("id") or "?" for c in roster
              if any(not c.get(f) for f in REQUIRED_FIELDS)
              or any(not (c.get("palette") or {}).get(k) for k in REQUIRED_COLOURS)]
    ids = [c.get("id") for c in roster]
    duplicates = sorted({i for i in ids if i and ids.count(i) > 1})
    playable = sum(1 for c in roster if c.get("playable"))
    counted = health.get("characters")

    problems = []
    if broken:
        problems.append(f"{len(broken)} character(s) missing a name or palette "
                        f"({', '.join(broken[:3])}) — they draw as holes")
    if duplicates:
        problems.append(f"duplicate id(s) {', '.join(duplicates)} — one dog replaces another")
    if playable < MIN_PLAYABLE:
        problems.append(f"only {playable} playable of {MIN_PLAYABLE} — the character select is short")
    if counted is not None and counted != len(roster):
        problems.append(f"/api/health counts {counted} characters but the file has {len(roster)} — "
                        f"the server and the data came from different builds")

    return {"name": "cast", "state": DOWN if problems else READY,
            "detail": "; ".join(problems) if problems
                      else f"{len(roster)} characters, {playable} playable, all drawable"}


def imports_in(body: str) -> list[str]:
    """Every relative module this file pulls in, in all three spellings."""
    return [*IMPORT.findall(body), *BARE_IMPORT.findall(body), *LAZY_IMPORT.findall(body)]


OUTSIDE = "outside the site root"


def resolve(folder: str, ref: str) -> str | None:
    """Where an import written inside ``folder`` actually points.

    The browser resolves it against the *importing file's* folder, so this has to
    as well: ``../lib/x.js`` from ``js/main.js`` is ``lib/x.js``, not ``js/lib/x.js``.
    Stripping the leading dots (which is what this used to do) re-rooted every
    parent-relative import under the child folder and reported a deployed module
    as missing — a false DOWN on a healthy demo, which is how a check teaches
    people to ignore it (#93).

    ``None`` for a reference that climbs above the site root: the server has no
    such URL to answer, so it is the app that is wrong, not the deploy.
    """
    target = posixpath.normpath(posixpath.join(folder, ref))
    if target.startswith("..") or target.startswith("/"):
        return None
    return target


def walk(base: str, entries: list[str]) -> dict[str, int | str]:
    """{path: status}, following imports out from the entry points.

    A path that cannot be resolved is kept with ``OUTSIDE`` as its status rather
    than dropped: silently skipping it would turn a broken import into a clean
    walk, which is the failure this whole check exists to prevent.
    """
    seen: dict[str, int | str] = {}
    queue = [(".", e) for e in entries]
    while queue:
        folder, ref = queue.pop()
        path = resolve(folder, ref)
        if path is None:
            seen[posixpath.join(folder, ref).lstrip("./")] = OUTSIDE
            continue
        if path in seen:
            continue
        code_status, body = _get(base, path)
        seen[path] = code_status
        if code_status != 200:
            continue
        here = path.rsplit("/", 1)[0] if "/" in path else ""
        queue += [(here, ref) for ref in imports_in(body)]
    return seen


def modules(base: str) -> dict:
    """Walk the ES-module graph the browser would walk, from index.html.

    No bundler, so nothing links these files together at build time: a module
    that failed to deploy is found only by asking for it. The server answers a
    missing one with a cheerful 404 and the menu renders anyway.
    """
    status, index = _get(base, "/")
    if status != 200:
        return {"name": "modules", "state": DOWN, "detail": f"/ answered {status}"}
    entries = ENTRY.findall(index)
    if not entries:
        return {"name": "modules", "state": DOWN,
                "detail": "index.html loads no module — the page cannot start the game"}

    seen = walk(base, entries)
    escaped = sorted(p for p, s in seen.items() if s == OUTSIDE)
    missing = sorted(p for p, s in seen.items() if s != 200 and s != OUTSIDE)
    problems = []
    if missing:
        problems.append(f"{len(missing)} module(s) the page imports are not deployed: "
                        f"{', '.join(missing)} — the menu will render and do nothing")
    if escaped:
        problems.append(f"{len(escaped)} import(s) point {OUTSIDE} ({', '.join(escaped)}) — "
                        f"the browser cannot fetch them from this site either")
    return {"name": "modules", "state": DOWN if problems else READY,
            "detail": "; ".join(problems) if problems
                      else f"{len(seen)} modules load, imports all resolve"}


def links_in(index: str) -> list[dict[str, str]]:
    """Every ``<link>`` on the page as a dict of its attributes."""
    return [dict(ATTR.findall(tag)) for tag in LINK.findall(index)]


def fetched(href: str) -> bool:
    """Is this href a file the browser would ask *this* site for?"""
    return bool(href) and not href.startswith(EXTERNAL)


def styles(base: str) -> dict:
    """Does the page's stylesheet (and anything else it <link>s) deploy? (#94)

    The module walk proves the game *runs*; nothing proved it is *dressed*. With
    no bundler, ``css/style.css`` is one more file that can fail to upload on its
    own, and when it does the game still serves 200s, still starts, still passes
    every other check here — as an unstyled column of controls with the layout
    collapsed. That is not showable, so it is DOWN.

    Other links are held to a lower bar: a missing icon is a blemish in a browser
    tab, so it is DIRTY, not DOWN. Links the browser does not fetch from this site
    — the icon is an inline ``data:`` SVG today — are counted and named as skipped
    rather than passed over in silence.
    """
    status, index = _get(base, "/")
    if status != 200:
        return {"name": "styles", "state": DOWN, "detail": f"/ answered {status}"}

    links = links_in(index)
    sheets = [l["href"] for l in links
              if "stylesheet" in l.get("rel", "").lower() and fetched(l.get("href", ""))]
    others = [l["href"] for l in links
              if "stylesheet" not in l.get("rel", "").lower() and fetched(l.get("href", ""))]
    inline = [l.get("rel", "?") for l in links if not fetched(l.get("href", ""))]

    if not sheets:
        return {"name": "styles", "state": DOWN,
                "detail": "the page links no stylesheet — the game would render as an "
                          "unstyled column of controls"}

    missing_sheets = sorted(h for h in sheets if _get(base, h)[0] != 200)
    if missing_sheets:
        return {"name": "styles", "state": DOWN,
                "detail": f"the stylesheet is not deployed ({', '.join(missing_sheets)}) — the "
                          f"game runs, unstyled and collapsed; do not show it"}

    missing_others = sorted(h for h in others if _get(base, h)[0] != 200)
    if missing_others:
        return {"name": "styles", "state": DIRTY,
                "detail": f"linked file(s) missing: {', '.join(missing_others)} — cosmetic, "
                          f"the game plays"}

    counted = f"{len(sheets)} stylesheet(s) and {len(others)} linked file(s) load"
    return {"name": "styles", "state": READY,
            "detail": counted + (f"; {len(inline)} inline/external link(s) not checked "
                                 f"({', '.join(inline)})" if inline else "")}


def credits(base: str) -> dict:
    """The unofficial-tribute disclaimer is the one line that must not go missing."""
    status, index = _get(base, "/")
    if status != 200:
        return {"name": "credits", "state": DOWN, "detail": f"/ answered {status}"}
    missing = [m for m in CREDIT_MARKERS if m not in index]
    return {"name": "credits", "state": DIRTY if missing else READY,
            "detail": (f"the fan-made disclaimer is gone ({', '.join(missing)} not on the page) — "
                       f"do not show this until it is back" if missing
                       else "fan-made disclaimer on the page")}


def player_state(base: str) -> dict:
    """Deliberately nothing to check — said out loud rather than left out.

    MUSC can go DIRTY because a smoke test leaves cases half-submitted on a
    shared board. Here every visitor's stars and chapter progress live in their
    own ``localStorage``: nobody can dirty this demo for anybody else.
    """
    return {"name": "player state", "state": SKIPPED,
            "detail": "progress is per-visitor localStorage — no shared state to leave dirty"}


def checks(base: str) -> list[dict]:
    return [cast(base), modules(base), styles(base), credits(base), player_state(base)]
