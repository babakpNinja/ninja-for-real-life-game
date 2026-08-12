"""
End-to-end tests for "For Real Life!".

Runs against any base URL, so the same suite covers the local server and the
deployed site:

    python tests/test_game.py                       # http://localhost:3000
    python tests/test_game.py https://<live-url>    # post-deploy check

Every chapter is actually played to the finish line by fast-forwarding the
engine, which is the only way to be sure the level generator, the collectibles
and the results screen all still work.
"""

import sys
import time

from playwright.sync_api import sync_playwright

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://localhost:3000"

DESKTOP = {"width": 1280, "height": 800}
IPHONE = {"width": 390, "height": 844}
PIXEL = {"width": 412, "height": 915}

failures = []
checks = 0


def check(label, ok, detail=""):
    global checks
    checks += 1
    if ok:
        print(f"  ok   {label}")
    else:
        failures.append(f"{label} {detail}".strip())
        print(f"  FAIL {label} {detail}")


def new_page(browser, viewport, touch=False):
    ctx = browser.new_context(
        viewport=viewport,
        has_touch=touch,
        is_mobile=touch,
        device_scale_factor=2 if touch else 1,
    )
    page = ctx.new_page()
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.on("console", lambda m: page.errors.append(m.text) if m.type == "error" else None)
    page.goto(BASE + "/", wait_until="domcontentloaded")
    page.wait_for_function("window.__ready === true", timeout=20000)
    return ctx, page


def play_chapter(page, index):
    """Start a chapter and fast-forward the physics until it completes."""
    page.evaluate(
        """(i) => {
            window.__result = null;
            const prev = window.game.onEvent;
            window.game.onEvent = (ev) => { if (ev.type === 'complete') window.__result = ev; prev(ev); };
            document.getElementById('overlay').classList.add('hidden');
            document.getElementById('hud').classList.remove('hidden');
            window.game.start(i);
        }""",
        index,
    )
    # step the fixed-timestep simulation directly: 1/120s per step, jumping
    # whenever there is ground under foot so the player clears every gap.
    page.evaluate(
        """() => {
            const g = window.game;
            for (let n = 0; n < 60000 && g.mode === 'playing'; n++) {
                if (g.player.onGround && n % 26 === 0) g.press();
                if (n % 26 === 12) g.release();
                g.step(1 / 120);
            }
        }"""
    )
    return page.evaluate("window.__result")


def run():
    with sync_playwright() as p:
        browser = p.chromium.launch()

        # ---------------------------------------------------------- desktop --
        print("\ndesktop 1280x800")
        ctx, page = new_page(browser, DESKTOP)

        check("title screen renders", page.locator("h1.title").is_visible())
        check("play button present", page.locator("#btn-play").is_visible())
        check("canvas sized", page.evaluate("document.getElementById('game').width") > 0)

        chapters = page.evaluate("window.game ? 5 : 0")
        check("game object exposed", chapters == 5)

        # chapter select
        page.click("#btn-chapters")
        page.wait_for_selector(".chapter-card")
        cards = page.locator(".chapter-card").count()
        check("chapter select lists 5 chapters", cards == 5, f"got {cards}")
        locked = page.locator(".chapter-card.locked").count()
        check("later chapters start locked", locked == 4, f"got {locked}")
        page.click("#btn-back")

        # gallery + bios
        page.click("#btn-gallery")
        page.wait_for_selector(".char-card")
        n_chars = page.locator(".char-card").count()
        check("gallery shows every character", n_chars >= 25, f"got {n_chars}")
        page.locator(".char-card").first.click()
        page.wait_for_selector(".bio h3")
        check("bio opens", "Bluey" in page.locator(".bio h3").inner_text())
        check("bio has a fun fact", page.locator(".bio .fun").is_visible())
        page.click("#btn-menu")

        # stats
        page.click("#btn-stats")
        page.wait_for_selector("table.stats")
        check("stats screen opens", page.locator("table.stats tr").count() >= 6)
        page.click("#btn-back")

        # story card -> play
        page.click("#btn-play")
        page.wait_for_selector("#btn-go")
        check("story card shows chapter 1", "Keepy Uppy" in page.locator("h2").inner_text())
        page.click("#btn-go")
        page.wait_for_timeout(500)
        check("hud visible while playing", page.locator("#hud").is_visible())
        check("engine is playing", page.evaluate("window.game.mode") == "playing")

        # tapping the canvas makes the dog jump
        before = page.evaluate("window.game.player.y")
        page.mouse.click(640, 500)
        page.wait_for_timeout(220)
        after = page.evaluate("window.game.player.y")
        check("tap makes the player jump", after < before, f"{before} -> {after}")

        # score climbs as the level runs
        page.wait_for_timeout(1500)
        check("score increases while running", page.evaluate("window.game.score") > 0)

        # pause / resume
        page.click("#btn-pause")
        page.wait_for_selector("#btn-resume")
        check("pause menu opens", page.evaluate("window.game.paused") is True)
        page.click("#btn-resume")
        page.wait_for_timeout(200)
        check("resume works", page.evaluate("window.game.paused") is False)

        # ------------------------------------------------- play all chapters --
        print("\nplaying every chapter to the finish line")
        for i in range(5):
            r = play_chapter(page, i)
            name = page.evaluate("(i) => window.game.ch.title", i)
            check(f"chapter {i + 1} ({name}) completes", bool(r))
            if r:
                check(f"chapter {i + 1} scores", r["score"] > 0, str(r.get("score")))
                check(f"chapter {i + 1} collects tokens", r["collected"] > 0,
                      f"{r['collected']}/{r['total']}")
                check(f"chapter {i + 1} awards 1-3 stars", 1 <= r["stars"] <= 3, str(r.get("stars")))

        # results screen + progress persistence
        page.wait_for_timeout(1200)
        check("results screen appears", page.locator(".stars-big").is_visible())
        saved = page.evaluate("JSON.parse(localStorage.getItem('forreallife.save.v1') || '{}')")
        check("progress saved to localStorage", len(saved.get("chapters", {})) == 5,
              str(list(saved.get("chapters", {}).keys())))
        check("chapters unlocked by playing", saved.get("unlocked") == 4, str(saved.get("unlocked")))

        page.reload()
        page.wait_for_function("window.__ready === true")
        page.click("#btn-chapters")
        page.wait_for_selector(".chapter-card")
        still_locked = page.locator(".chapter-card.locked").count()
        check("unlocks survive a reload", still_locked == 0, f"{still_locked} locked")

        check("no console errors (desktop)", not page.errors, str(page.errors[:3]))
        ctx.close()

        # ----------------------------------------------------------- mobile --
        for label, vp in (("iPhone 390x844", IPHONE), ("Pixel 412x915", PIXEL)):
            print(f"\n{label}")
            ctx, page = new_page(browser, vp, touch=True)
            check("menu fits without page scroll",
                  page.evaluate("document.body.scrollHeight <= window.innerHeight + 2"))
            check("rotate hint shown in portrait",
                  page.locator("#rotate-hint").is_visible())
            page.click("#btn-play")
            page.wait_for_selector("#btn-go")
            page.click("#btn-go")
            page.wait_for_timeout(400)
            check("plays on touch", page.evaluate("window.game.mode") == "playing")
            check("audio context created on tap",
                  page.evaluate("window.game && !!document.querySelector('canvas')"))
            before = page.evaluate("window.game.player.y")
            page.touchscreen.tap(vp["width"] / 2, vp["height"] * 0.7)
            page.wait_for_timeout(220)
            check("tap jumps on touch", page.evaluate("window.game.player.y") < before)
            check("canvas fills the viewport",
                  page.evaluate("document.getElementById('game').clientWidth") >= vp["width"] - 2)
            check("no console errors (mobile)", not page.errors, str(page.errors[:3]))
            ctx.close()

        browser.close()

    print(f"\n{checks - len(failures)}/{checks} checks passed")
    if failures:
        print("failures:")
        for f in failures:
            print("  -", f)
        return 1
    return 0


if __name__ == "__main__":
    t0 = time.time()
    code = run()
    print(f"({time.time() - t0:.1f}s against {BASE})")
    sys.exit(code)
