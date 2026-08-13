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


def walk(base: str, entries: list[str]) -> dict[str, int]:
    """{module path: status}, following imports out from the entry points."""
    seen: dict[str, int] = {}
    queue = [e.lstrip("./") for e in entries]
    while queue:
        path = queue.pop()
        if path in seen:
            continue
        code_status, body = _get(base, path)
        seen[path] = code_status
        if code_status != 200:
            continue
        folder = path.rsplit("/", 1)[0] if "/" in path else ""
        for rel in imports_in(body):
            target = rel.lstrip("./")
            queue.append(f"{folder}/{target}" if folder else target)
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
    missing = sorted(p for p, s in seen.items() if s != 200)
    return {"name": "modules", "state": DOWN if missing else READY,
            "detail": (f"{len(missing)} module(s) the page imports are not deployed: "
                       f"{', '.join(missing)} — the menu will render and do nothing" if missing
                       else f"{len(seen)} modules load, imports all resolve")}


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
    return [cast(base), modules(base), credits(base), player_state(base)]
