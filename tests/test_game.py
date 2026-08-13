"""
End-to-end tests for "For Real Life!".

Runs against any base URL, so the same suite covers the local server and the
deployed site:

    python -m pytest tests -q                                # localhost:3000
    python -m pytest tests -q --base-url=https://<live-url>  # post-deploy check

Every chapter is actually played to the finish line by fast-forwarding the
engine, which is the only way to be sure the level generator, the collectibles
and the results screen all still work.

The tests share one page per viewport and **run in file order**: the game is a
sequence (play a chapter, it unlocks the next, progress is saved), so the state
each test needs is what the ones above it left behind. Keep new tests in the
place their state belongs.
"""

import json
import urllib.request

import pytest

DESKTOP = {"width": 1280, "height": 800}
IPHONE = {"width": 390, "height": 844}
PIXEL = {"width": 412, "height": 915}


@pytest.fixture(scope="module")
def desktop(make_page):
    return make_page(DESKTOP)


@pytest.fixture(scope="module", params=[IPHONE, PIXEL], ids=["iphone", "pixel"])
def phone(request, make_page):
    return make_page(request.param, touch=True)


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


# --- what the deploy says about itself --------------------------------------
# The content floors monitoring reads. Cheap, and a failure here explains most
# of the failures below, so it runs first.

@pytest.fixture(scope="module")
def health(base_url):
    with urllib.request.urlopen(f"{base_url}/api/health", timeout=15) as resp:
        return json.loads(resp.read())


def test_health_says_ok(health):
    assert health.get("status") == "ok", str(health)[:120]


def test_all_25_characters_shipped(health):
    assert health.get("characters") == 25, f"got {health.get('characters')}"


def test_all_5_chapters_shipped(health):
    assert health.get("chapters") == 5, f"got {health.get('chapters')}"


# --- desktop: the menus a grown-up drives -----------------------------------

def test_title_screen_renders(desktop):
    assert desktop.locator("h1.title").is_visible()
    assert desktop.locator("#btn-play").is_visible()
    assert desktop.evaluate("document.getElementById('game').width") > 0


def test_the_game_object_is_exposed(desktop):
    """Everything below drives the engine directly; without this nothing works."""
    assert desktop.evaluate("window.game ? 5 : 0") == 5


def test_chapter_select_lists_five_chapters_with_four_locked(desktop):
    desktop.click("#btn-chapters")
    desktop.wait_for_selector(".chapter-card")
    cards = desktop.locator(".chapter-card").count()
    locked = desktop.locator(".chapter-card.locked").count()
    desktop.click("#btn-back")
    assert cards == 5, f"got {cards}"
    assert locked == 4, f"got {locked}"


def test_the_gallery_shows_every_character_with_a_bio(desktop):
    desktop.click("#btn-gallery")
    desktop.wait_for_selector(".char-card")
    n_chars = desktop.locator(".char-card").count()
    desktop.locator(".char-card").first.click()
    desktop.wait_for_selector(".bio h3")
    bio, fun = desktop.locator(".bio h3").inner_text(), desktop.locator(".bio .fun").is_visible()
    desktop.click("#btn-menu")
    assert n_chars >= 25, f"got {n_chars}"
    assert "Bluey" in bio and fun


def test_the_stats_screen_opens(desktop):
    desktop.click("#btn-stats")
    desktop.wait_for_selector("table.stats")
    rows = desktop.locator("table.stats tr").count()
    desktop.click("#btn-back")
    assert rows >= 6, f"got {rows}"


# --- desktop: playing -------------------------------------------------------

def test_the_story_card_leads_into_chapter_one(desktop):
    desktop.click("#btn-play")
    desktop.wait_for_selector("#btn-go")
    assert "Keepy Uppy" in desktop.locator("h2").inner_text()
    desktop.click("#btn-go")
    desktop.wait_for_timeout(500)
    assert desktop.locator("#hud").is_visible()
    assert desktop.evaluate("window.game.mode") == "playing"


def test_tapping_the_canvas_makes_the_player_jump(desktop):
    before = desktop.evaluate("window.game.player.y")
    desktop.mouse.click(640, 500)
    desktop.wait_for_timeout(220)
    after = desktop.evaluate("window.game.player.y")
    assert after < before, f"{before} -> {after}"


def test_the_score_climbs_while_the_level_runs(desktop):
    desktop.wait_for_timeout(1500)
    assert desktop.evaluate("window.game.score") > 0


def test_pause_and_resume(desktop):
    desktop.click("#btn-pause")
    desktop.wait_for_selector("#btn-resume")
    assert desktop.evaluate("window.game.paused") is True
    desktop.click("#btn-resume")
    desktop.wait_for_timeout(200)
    assert desktop.evaluate("window.game.paused") is False


@pytest.mark.parametrize("index", range(5))
def test_every_chapter_plays_to_the_finish_line(desktop, index):
    """The one test that would catch a level nobody can finish."""
    result = play_chapter(desktop, index)
    name = desktop.evaluate("(i) => window.game.ch.title", index)
    assert result, f"chapter {index + 1} ({name}) never completed"
    assert result["score"] > 0, str(result.get("score"))
    assert result["collected"] > 0, f"{result['collected']}/{result['total']}"
    assert 1 <= result["stars"] <= 3, str(result.get("stars"))


# --- desktop: what playing left behind --------------------------------------

def test_the_results_screen_appears(desktop):
    desktop.wait_for_timeout(1200)
    assert desktop.locator(".stars-big").is_visible()


def test_progress_is_saved_and_unlocks_the_rest(desktop):
    saved = desktop.evaluate("JSON.parse(localStorage.getItem('forreallife.save.v1') || '{}')")
    assert len(saved.get("chapters", {})) == 5, str(list(saved.get("chapters", {}).keys()))
    assert saved.get("unlocked") == 4, str(saved.get("unlocked"))


def test_unlocks_survive_a_reload(desktop):
    desktop.reload()
    desktop.wait_for_function("window.__ready === true")
    desktop.click("#btn-chapters")
    desktop.wait_for_selector(".chapter-card")
    still_locked = desktop.locator(".chapter-card.locked").count()
    assert still_locked == 0, f"{still_locked} locked"


def test_no_console_errors_on_desktop(desktop):
    """Last, so it covers everything the tests above did."""
    assert not desktop.errors, str(desktop.errors[:3])


# --- phones: the only way it is really played -------------------------------

def test_the_menu_fits_without_scrolling(phone):
    assert phone.evaluate("document.body.scrollHeight <= window.innerHeight + 2")
    assert phone.locator("#rotate-hint").is_visible()


def test_it_plays_on_touch(phone):
    phone.click("#btn-play")
    phone.wait_for_selector("#btn-go")
    phone.click("#btn-go")
    phone.wait_for_timeout(400)
    assert phone.evaluate("window.game.mode") == "playing"
    assert phone.evaluate("window.game && !!document.querySelector('canvas')")


def test_a_tap_jumps_on_touch(phone):
    vp = phone.viewport_size
    before = phone.evaluate("window.game.player.y")
    phone.touchscreen.tap(vp["width"] / 2, vp["height"] * 0.7)
    phone.wait_for_timeout(220)
    assert phone.evaluate("window.game.player.y") < before


def test_the_canvas_fills_the_viewport(phone):
    vp = phone.viewport_size
    width = phone.evaluate("document.getElementById('game').clientWidth")
    assert width >= vp["width"] - 2, f"{width} < {vp['width']}"


def test_no_console_errors_on_touch(phone):
    assert not phone.errors, str(phone.errors[:3])
