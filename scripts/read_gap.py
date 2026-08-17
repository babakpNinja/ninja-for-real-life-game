"""How long the game keeps you waiting for a line it has recorded (#358, #366).

A measurement, not a test: the suite's browser cannot hear anything, and none of
it ever waits on a network. Run this by hand when the reading changes shape —
more lines, bigger clips, a different fetch — and put the number in the issue.

    python scripts/read_gap.py                          # every profile below
    python scripts/read_gap.py --profile "slow 3G"
    python scripts/read_gap.py --mode greeting --mode hello

Three modes, each over a connection throttled with CDP to Chrome's own presets:

* `read` (the default) opens the chapter-1 story card and times, for each line,
  `ended` on line N to line N+1 being audible — the silence *inside* a read;
* `greeting` runs chapter 1 and catches each friend as the player reaches them,
  timing the catch to "Hi Bluey, I'm Bingo!" being audible — the *first* line of
  a read, which no earlier line can have loaded;
* `hello` opens four bio cards and times the tap to the dog saying hello.

One thing is faked, and only one: **the clock of playback.** This container has
no audio device, and headless Chromium then runs media as fast as it can decode
— a 3.75s clip ends about 80ms after `play()`. Measured raw, no line would have
any time at all to load the one behind it, which is not what a phone does, and
every profile would report the full download as a gap. So `play()` here waits
until the element is playable, fires `playing`, waits the clip's own `duration`,
and fires `ended`: the schedule a real device would keep. The fetch is the real
file over the real (throttled) connection and the game's own `read()` drives the
queue, so what is being measured is still the game.

Measured this way on 2026-08-17, four lines, chapter 1 (`read`):

    profile          before #358        after
    no throttling      17ms mean         1ms
    fast 3G           741ms mean         1ms
    slow 3G          2551ms mean         1ms

And the same day, three catches, chapter 1 (`greeting`), before and after the
warm-up in `Sound.warm`:

    profile          before #366        after
    no throttling     127ms mean       116ms
    fast 3G           568ms mean        67ms
    slow 3G          2313ms mean       108ms

~120ms is what this browser takes to start a clip it already has, so the
after column is the floor, not a remaining delay. "before" is the same script
against the same commit with the fix stashed.
"""
import argparse
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP / "tests"))

from conftest import local_server                  # noqa: E402
from playwright.sync_api import sync_playwright    # noqa: E402

# Chrome DevTools' own presets, in the units CDP wants (bytes/sec, ms).
PROFILES = {
    "no throttling": None,
    "fast 3G": {"downloadThroughput": 1.6e6 / 8, "uploadThroughput": 750e3 / 8,
                "latency": 562.5},
    "slow 3G": {"downloadThroughput": 500e3 / 8, "uploadThroughput": 500e3 / 8,
                "latency": 2000},
}

PACE = """
() => {
  window.__log = [];
  const t = () => performance.now();
  HTMLMediaElement.prototype.play = function () {
    const line = this.dataset.line || this.src;
    const audible = () => {
      window.__log.push({ at: t(), ev: "playing", line });
      // a real device gives the next clip this long to arrive
      setTimeout(() => {
        window.__log.push({ at: t(), ev: "ended", line });
        this.dispatchEvent(new Event("ended"));
      }, (this.duration || 0) * 1000);
    };
    if (this.readyState >= 3) audible();
    else this.addEventListener("canplay", audible, { once: true });
    return Promise.resolve();
  };
}
"""


def open_page(browser, url, profile):
    """A booted game on a throttled connection, with playback paced.

    The returned `net` grows for the life of the page: one entry per request,
    in CDP's own monotonic seconds, which is what `queued_behind` reads.
    """
    ctx = browser.new_context(viewport={"width": 900, "height": 600})
    page = ctx.new_page()
    page.goto(url)
    page.wait_for_function("() => window.__ready")
    page.evaluate(PACE)                     # before the card is opened, not after
    cdp = ctx.new_cdp_session(page)
    cdp.send("Network.enable")
    net = {}
    cdp.on("Network.requestWillBeSent", lambda e: net.setdefault(e["requestId"], {
        "url": e["request"]["url"], "sent": e["timestamp"], "done": None, "bytes": 0}))
    for ev, key in (("Network.loadingFinished", "encodedDataLength"),
                    ("Network.loadingFailed", None)):
        cdp.on(ev, lambda e, key=key: net.get(e["requestId"], {}).update(
            done=e["timestamp"], bytes=e.get(key, 0) if key else 0))
    cdp.send("Network.emulateNetworkConditions", {
        "offline": False, "latency": 0,
        "downloadThroughput": -1, "uploadThroughput": -1,
    } if profile is None else {"offline": False, **profile})
    return ctx, page, net


def queued_behind(net, want, after):
    """What was still in flight when the request for `want` went out (#370).

    The delay a player meets is not the clip's own size — it is everything the
    screen was already downloading when they tapped. This answers that with the
    connection's own record rather than a guess: the entry for the wanted file,
    and every request sent before it that had not finished by then.
    """
    mine = sorted((r for r in net.values() if want in r["url"] and r["sent"] >= after),
                  key=lambda r: r["sent"])
    if not mine:
        return None
    me = mine[0]
    ahead = [r for r in net.values()
             if r["sent"] < me["sent"] and (r["done"] is None or r["done"] > me["sent"])]
    return {
        "file": me["url"].rsplit("/", 1)[-1],
        "took": None if me["done"] is None else round((me["done"] - me["sent"]) * 1000),
        "ahead": len(ahead),
        "ahead_kb": round(sum(r["bytes"] for r in ahead) / 1024),
        "names": sorted({r["url"].rsplit("/", 1)[-1] for r in ahead})[:6],
    }


def report(name, rows, empty):
    print(f"\n=== {name} ===")
    for at, line in rows:
        print(f"  {at:>6} ms  {line!r}")
    if rows:
        print(f"  n={len(rows)} min={min(g for g, _ in rows)} "
              f"max={max(g for g, _ in rows)} "
              f"mean={round(sum(g for g, _ in rows) / len(rows))}")
    else:
        print(f"  {empty}")
    return rows


def between_lines(browser, url, name, profile, wait):
    """Silence between line N ending and line N+1 being audible (#358)."""
    ctx, page, net = open_page(browser, url, profile)
    page.click("#btn-story")
    page.wait_for_selector("#btn-go")
    page.wait_for_timeout(wait)             # the card is ~20s of audio, paced
    log = page.evaluate("() => window.__log")
    ctx.close()

    gaps = []
    for i, row in enumerate(log):
        if row["ev"] == "ended":
            nxt = next((r for r in log[i + 1:] if r["ev"] == "playing"), None)
            if nxt:
                gaps.append((round(nxt["at"] - row["at"]), row["line"][:34]))
    return report(name, gaps, f"nothing played ({len(log)} events) — the read never started")


def first_line(browser, url, name, profile, wait):
    """Catch → greeting audible: the first line of a read, from cold (#366).

    The one-line read, where there is no line before to have loaded it. The
    catch is the game's own `catchFriend` on a chapter started through the menu,
    so what runs afterwards is `main.js`'s handler and `sound.read(...,
    {recorded: true})` — the same path a jump into a friend takes, minus the
    jumping.

    Each friend is caught at the moment the running player *arrives* at them
    (5.9s, 11.2s and 16.7s into chapter 1) rather than as soon as the chapter
    starts. That is the earliest a catch can happen, and it is the difference
    between measuring a warm-up and measuring a stopwatch started before it: the
    same run caught 1.5s in reports 2741ms on slow 3G, because 150KB has not
    arrived yet — and no player is there yet either.
    """
    ctx, page, net = open_page(browser, url, profile)
    page.click("#btn-play")                 # a save with no hero picks one first
    page.click(".hero-card[data-id='bluey']")
    page.wait_for_function("() => window.game && window.game.friends.length")
    n = page.evaluate("() => window.game.friends.length")

    delays = []
    for i in range(n):
        page.wait_for_function(
            "(i) => window.game.player.x >= window.game.friends[i].atX", arg=i, timeout=wait)
        # the line this catch will say, so a clip still arriving for the screen
        # before it cannot be mistaken for the greeting (it was, at 919ms)
        page.evaluate("""(i) => {
          const g = window.game;
          const hero = g.characters.find((c) => c.id === g.hero);
          const friend = g.characters.find((c) => c.id === g.friends[i].id);
          window.__want = `Hi ${hero.name}, I'm ${friend.name}!`;
          window.__t0 = performance.now();
          g.catchFriend(g.friends[i]);
        }""", i)
        heard = ("() => window.__log.some((r) => r.ev === 'playing' && "
                 "r.at > window.__t0 && r.line === window.__want)")
        try:
            page.wait_for_function(heard, timeout=wait)
        except Exception:
            delays.append((wait, f"{page.evaluate('() => window.__want')}: "
                                 f"not audible within {wait}ms"))
            continue
        row = page.evaluate("""() => {
          const r = window.__log.find((x) => x.ev === 'playing' &&
            x.at > window.__t0 && x.line === window.__want);
          return [Math.round(r.at - window.__t0), r.line];
        }""")
        delays.append((row[0], row[1]))
        page.wait_for_timeout(2500)         # let the clip finish before the next catch
    page.evaluate("() => window.game.stop()")
    ctx.close()
    return report(name, delays, "nobody was caught — the chapter never started")


def bio_hello(browser, url, name, profile, wait):
    """Tap a character card → their hello audible (#366).

    The same shape as the greeting: the first line of the bio read, fetched at
    the moment the card is opened. Four cards, tapped one after another.
    """
    ctx, page, net = open_page(browser, url, profile)
    page.click("#btn-gallery")
    page.wait_for_selector(".char-card")
    ids = page.evaluate(
        "() => [...document.querySelectorAll('.char-card')].slice(0, 4).map((b) => b.dataset.id)")

    delays = []
    for cid in ids:
        # the clip this tap should produce, named before it is tapped: "the first
        # thing that played" would happily time a portrait's own line, and on a
        # slow profile the card before it is still finishing when this one opens
        want = page.evaluate("""(id) => {
          const b = document.querySelector(`.char-card[data-id='${id}'] b`);
          const line = `G'day! I'm ${b.textContent}.`;
          const clip = (window.__sound.voices || {})[line];
          window.__want = line;
          window.__t0 = performance.now();
          return clip ? clip.file : null;
        }""", cid)
        mark = max((r["sent"] for r in net.values()), default=0)
        page.click(f".char-card[data-id='{cid}']")
        heard = ("() => window.__log.some((r) => r.ev === 'playing' && "
                 "r.at > window.__t0 && r.line === window.__want)")
        try:
            page.wait_for_function(heard, timeout=wait)
            row = page.evaluate("""() => {
              const r = window.__log.find((x) => x.ev === 'playing' &&
                x.at > window.__t0 && x.line === window.__want);
              return [Math.round(r.at - window.__t0), r.line];
            }""")
            delays.append((row[0], row[1]))
        except Exception:
            delays.append((wait, f"{cid}: nothing audible in {wait}ms"))
        # what the tap was actually queued behind, from the connection's record
        q = queued_behind(net, want, mark) if want else None
        if q:
            print(f"  {q['file']}: took {q['took']}ms, {q['ahead']} request(s) still in "
                  f"flight when it was asked for ({q['ahead_kb']}KB in all)"
                  + (f" — {', '.join(q['names'])}…" if q["names"] else ""))
        page.click("#btn-back")
        page.wait_for_selector(".char-card")
    ctx.close()
    return report(name, delays, "no card was opened")


MODES = {"read": between_lines, "greeting": first_line, "hello": bio_hello}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--profile", action="append", choices=list(PROFILES),
                    help="only this connection (repeatable); default is all three")
    ap.add_argument("--mode", action="append", choices=list(MODES),
                    help="what to time (repeatable); default is 'read'")
    ap.add_argument("--wait", type=int, default=60000,
                    help="ms to let a read happen (default 60000)")
    args = ap.parse_args()
    wanted = args.profile or list(PROFILES)
    modes = args.mode or ["read"]
    with local_server() as url, sync_playwright() as p:
        browser = p.chromium.launch()
        for mode in modes:
            for name in wanted:
                MODES[mode](browser, url, f"{mode} — {name}", PROFILES[name], args.wait)
        browser.close()


if __name__ == "__main__":
    main()
