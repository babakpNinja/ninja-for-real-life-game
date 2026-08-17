"""How long the game is silent between two lines of a recorded read (#358).

A measurement, not a test: the suite's browser cannot hear anything, and none of
it ever waits on a network. Run this by hand when the reading changes shape —
more lines, bigger clips, a different fetch — and put the number in the issue.

    python scripts/read_gap.py                 # every profile below
    python scripts/read_gap.py --profile "slow 3G"

It opens the chapter-1 story card over a connection throttled with CDP to
Chrome's own presets and prints, for each line, the time from `ended` on line N
to line N+1 being audible.

One thing is faked, and only one: **the clock of playback.** This container has
no audio device, and headless Chromium then runs media as fast as it can decode
— a 3.75s clip ends about 80ms after `play()`. Measured raw, no line would have
any time at all to load the one behind it, which is not what a phone does, and
every profile would report the full download as a gap. So `play()` here waits
until the element is playable, fires `playing`, waits the clip's own `duration`,
and fires `ended`: the schedule a real device would keep. The fetch is the real
file over the real (throttled) connection and the game's own `read()` drives the
queue, so what is being measured is still the game.

Measured this way on 2026-08-17, four lines, chapter 1:

    profile          before #358        after
    no throttling      17ms mean         1ms
    fast 3G           741ms mean         1ms
    slow 3G          2551ms mean         1ms

"before" is the same script against the parent of the prefetch commit.
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


def run(browser, url, name, profile, wait):
    ctx = browser.new_context(viewport={"width": 900, "height": 600})
    page = ctx.new_page()
    page.goto(url)
    page.wait_for_function("() => window.__ready")
    page.evaluate(PACE)                     # before the card is opened, not after
    cdp = ctx.new_cdp_session(page)
    cdp.send("Network.enable")
    cdp.send("Network.emulateNetworkConditions", {
        "offline": False, "latency": 0,
        "downloadThroughput": -1, "uploadThroughput": -1,
    } if profile is None else {"offline": False, **profile})
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
    print(f"\n=== {name} ===")
    for at, line in gaps:
        print(f"  {at:>6} ms after {line!r}")
    if gaps:
        print(f"  n={len(gaps)} min={min(g for g, _ in gaps)} "
              f"max={max(g for g, _ in gaps)} "
              f"mean={round(sum(g for g, _ in gaps) / len(gaps))}")
    else:
        print(f"  nothing played ({len(log)} events) — the read never started")
    return gaps


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--profile", action="append", choices=list(PROFILES),
                    help="only this connection (repeatable); default is all three")
    ap.add_argument("--wait", type=int, default=60000,
                    help="ms to let the card read itself (default 60000)")
    args = ap.parse_args()
    wanted = args.profile or list(PROFILES)
    with local_server() as url, sync_playwright() as p:
        browser = p.chromium.launch()
        for name in wanted:
            run(browser, url, name, PROFILES[name], args.wait)
        browser.close()


if __name__ == "__main__":
    main()
