#!/usr/bin/env python3
"""Take the screenshots that go in the README and in Slack.

Boots the game's own server (or points at --base-url), walks the screens and
writes PNGs into shots/. Named shots so a rerun replaces them rather than
piling up stale ones next to the new.

  python3 scripts/shots.py                       # local
  python3 scripts/shots.py --base-url https://…  # the deployed site
  python3 scripts/shots.py --prefix live         # name them apart
"""
from __future__ import annotations

import argparse
import contextlib
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
SHOTS = APP / "shots"


@contextlib.contextmanager
def server():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    proc = subprocess.Popen(
        ["node", "server.js"], cwd=APP,
        env={"PORT": str(port), "PATH": __import__("os").environ["PATH"]},
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    url = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            urllib.request.urlopen(url, timeout=1)
            break
        except Exception:
            time.sleep(0.25)
    else:
        proc.kill()
        raise RuntimeError("server did not come up")
    try:
        yield url
    finally:
        proc.terminate()
        proc.wait(timeout=10)


# Chapters 2-5 are locked in a fresh profile, so the shot walk would only ever
# reach the first one. Unlock them the same way playing does: in localStorage.
SAVE = """() => localStorage.setItem('forreallife.save.v1', JSON.stringify(
  {chapters: {0:{stars:3},1:{stars:3},2:{stars:3},3:{stars:3},4:{stars:3}}, unlocked: 4}))"""


def separated(prefix: str) -> str:
    """`live` and `live-` must name the same files.

    A rerun is supposed to replace the shots it took last time. A prefix given
    without its separator doesn't collide with the previous run — it forks a
    second, parallel set (`live00-menu.png` beside `live-00-menu.png`), which
    looks like someone else's output and gets left behind untracked.
    """
    return prefix + "-" if prefix and prefix[-1] not in "-_." else prefix


def walk(page, url, prefix, wide=True):
    page.goto(url, wait_until="domcontentloaded")
    page.evaluate(SAVE)
    page.goto(url, wait_until="networkidle")
    page.wait_for_function("window.__ready === true", timeout=15000)
    page.wait_for_timeout(1200)  # let the sprites decode and the idle settle
    shot = lambda name: page.screenshot(path=str(SHOTS / f"{prefix}{name}.png"))

    shot("00-menu")
    page.click("#btn-credits")
    page.wait_for_timeout(400)
    shot("01-credits")
    page.click("#btn-back")
    page.wait_for_timeout(300)

    page.click("#btn-gallery")
    page.wait_for_timeout(2500)  # the gallery lazy-loads 25 portraits
    shot("02-gallery")
    page.click(".char-card")
    page.wait_for_timeout(900)
    shot("03-bio")
    page.click("#btn-back")
    page.wait_for_timeout(300)
    page.click("#btn-back")
    page.wait_for_timeout(300)

    if not wide:
        return
    page.click("#btn-chapters")
    page.wait_for_timeout(600)
    shot("04-chapters")
    for i in range(5):
        page.evaluate(f"window.__jump = {i}")
        page.evaluate(
            "(i) => { const b = document.querySelector(`.chapter-card[data-ch='${i}']`);"
            "if (b) b.click(); }", i,
        )
        page.wait_for_timeout(500)
        go = page.query_selector("#btn-go")
        if not go:
            break
        go.click()
        page.wait_for_timeout(2600)
        shot(f"05-ch{i + 1}")
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
        for sel in ("#btn-quit", "#btn-menu", "#btn-back"):
            b = page.query_selector(sel)
            if b:
                b.click()
                break
        page.wait_for_timeout(500)
        if not page.query_selector(".chapter-card"):
            page.click("#btn-chapters")
            page.wait_for_timeout(500)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url")
    ap.add_argument("--prefix", default="")
    a = ap.parse_args()
    prefix = separated(a.prefix)
    from playwright.sync_api import sync_playwright

    SHOTS.mkdir(exist_ok=True)
    ctx = contextlib.nullcontext(a.base_url) if a.base_url else server()
    with ctx as url, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1024, "height": 640})
        walk(page, url, prefix)
        page.close()
        mob = browser.new_page(
            viewport={"width": 844, "height": 390},
            device_scale_factor=2, is_mobile=True, has_touch=True,
        )
        walk(mob, url, prefix + "mobile-", wide=False)
        mob.close()
        # And the same phone held upright, which is a different picture rather
        # than a narrower one: the zoom leaves 573 world px of sky above the
        # world and 273 of ground below it, and #326 and #328 are both about what
        # is in those bands. Every shot of them was taken by hand until now.
        up = browser.new_page(
            viewport={"width": 390, "height": 844},
            device_scale_factor=2, is_mobile=True, has_touch=True,
        )
        walk(up, url, prefix + "upright-")
        up.close()
        browser.close()
    print(f"shots -> {SHOTS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
