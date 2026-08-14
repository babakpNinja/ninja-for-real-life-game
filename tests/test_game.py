"""
End-to-end tests for "Ana Bingo!".

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

import collections
import contextlib
import json
import math
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest
from conftest import DESKTOP        # the viewport `own_page` opens too, authored there
from playwright.sync_api import TimeoutError as PlaywrightTimeout

APP = Path(__file__).resolve().parent.parent

# The game's name is authored once, in fetch_assets.py. `--check` proves the
# static copies agree with it; this suite is the only thing that can say the
# *rendered* page does, which is the copy anyone actually reads.
GAME_NAME = re.search(r'^GAME_NAME = "(.*)"$',
                      (APP / "scripts" / "fetch_assets.py").read_text(), re.M).group(1)

IPHONE = {"width": 390, "height": 844}
PIXEL = {"width": 412, "height": 915}

# Read from the tree, not from the server, because it decides how many tests
# there are: a rig that lost its eyes should make this file shorter in the diff
# that lost them, not quietly test one dog less.
RIGS = json.loads((APP / "public" / "data" / "rigs.json").read_text())["rigs"]
EYED = sorted(cid for cid, r in RIGS.items() if r.get("eyes"))
EARED = sorted(cid for cid, r in RIGS.items() if r.get("ears"))

# Same rule for the pose frames: every (character, state) the data claims is a
# test, so a pose that stops being drawn takes its test with it rather than
# leaving one that passes over nothing.
POSES = json.loads((APP / "public" / "data" / "poses.json").read_text())["frames"]
POSED = sorted((cid, state) for cid, by in POSES.items() for state in by)

# Who the rig still has to carry: a character the player can pick with no
# running render anywhere. Read the same way, so the day one is drawn for her
# the rig test below loses its subject and says so.
PLAYABLE = [c["id"] for c in
            json.loads((APP / "public" / "data" / "characters.json").read_text())["characters"]
            if c.get("playable")]
RIGGED_RUN = sorted(cid for cid in PLAYABLE if "run" not in POSES.get(cid, {}))


@pytest.fixture(scope="module")
def desktop(make_page):
    return make_page(DESKTOP)


@pytest.fixture(scope="module", params=[IPHONE, PIXEL], ids=["iphone", "pixel"])
def phone(request, make_page):
    return make_page(request.param, touch=True)


def play_chapter(page, index):
    """Start a chapter and fast-forward the physics until it completes.

    Ends with `stop()`: crossing the finish line leaves the results screen
    painting for as long as the page is open, and these run on the shared pages
    (#182). The physics here is stepped by hand, so nothing is lost by putting
    the render loop down — the completion event has already been captured.
    """
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
            g.stop();
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


# --- the character artwork --------------------------------------------------
# The sprites are the game's whole look now, and they are separate files: a
# deploy can serve the code and lose the pictures. These check the bytes are
# really there before any test looks at a canvas.

@pytest.fixture(scope="module")
def art_credits(base_url):
    with urllib.request.urlopen(f"{base_url}/data/asset-credits.json", timeout=15) as resp:
        return json.loads(resp.read())


def test_every_character_has_credited_artwork(art_credits):
    assets = art_credits.get("assets", {})
    assert len(assets) == 25, f"got {len(assets)}"
    missing = [k for k, v in assets.items() if not (v.get("source") and v.get("retrieved"))]
    assert not missing, f"uncredited: {missing}"


def test_every_character_image_is_actually_served(base_url, art_credits):
    """A 404 here is a sprite that silently falls back to the old drawn dog."""
    bad = []
    for cid, entry in sorted(art_credits["assets"].items()):
        req = urllib.request.Request(f"{base_url}/{entry['file']}", method="HEAD")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status != 200:
                    bad.append(f"{cid}:{resp.status}")
        except Exception as exc:
            bad.append(f"{cid}:{exc}")
    assert not bad, f"not served: {bad}"


def test_the_rigs_cover_every_character(base_url, art_credits):
    with urllib.request.urlopen(f"{base_url}/data/rigs.json", timeout=15) as resp:
        rigs = json.loads(resp.read())["rigs"]
    missing = sorted(set(art_credits["assets"]) - set(rigs))
    assert not missing, f"no rig: {missing}"
    wrong = [k for k, r in rigs.items() if not 0.05 < r["neck"] < r["hip"] < 1.0]
    assert not wrong, f"joints out of order: {wrong}"


def test_the_page_asks_not_to_be_indexed(base_url):
    """It is a personal fan project; it should not turn up in search."""
    with urllib.request.urlopen(f"{base_url}/robots.txt", timeout=15) as resp:
        assert "Disallow: /" in resp.read().decode()


# --- desktop: the menus a grown-up drives -----------------------------------

def test_title_screen_renders(desktop):
    assert desktop.locator("h1.title").is_visible()
    # the tab and the menu have to be the same game
    assert desktop.locator("h1.title").inner_text().replace("\u00a0", " ") == GAME_NAME
    assert GAME_NAME in desktop.title()
    assert desktop.locator("#btn-play").is_visible()
    assert desktop.evaluate("document.getElementById('game').width") > 0


def test_the_game_object_is_exposed(desktop):
    """Everything below drives the engine directly; without this nothing works."""
    assert desktop.evaluate("window.game ? 5 : 0") == 5


def test_the_menu_family_is_drawn_from_the_real_artwork(desktop):
    """Not just 'the canvas is not blank': every menu dog must have used its
    sprite. drawCharacter records "fallback" when it had to draw the old
    procedural dog instead, which is exactly the regression to catch."""
    desktop.wait_for_function(
        "() => window.__art && window.__art().loaded.length >= 5", timeout=15000
    )
    art = desktop.evaluate("window.__art()")
    assert not art["failed"], f"images failed to load: {art['failed']}"
    fell_back = [k for k, v in art["drawn"].items() if v != "rig"]
    assert not fell_back, f"drawn without artwork: {fell_back}"


def test_the_credits_screen_names_the_owner_and_links_to_the_show(desktop):
    desktop.click("#btn-credits")
    desktop.wait_for_selector(".credits-body")
    body = desktop.locator(".credits-body").inner_text()
    links = desktop.locator(".link-list a")
    hrefs = [links.nth(i).get_attribute("href") for i in range(links.count())]
    desktop.click("#btn-back")
    assert "Ludo Studio" in body and "non-commercial" in body, body[:200]
    assert any("bluey.tv" in h for h in hrefs), hrefs
    assert any("iview.abc.net.au" in h for h in hrefs), hrefs
    assert all(h.startswith("https://") for h in hrefs), hrefs


def test_the_menu_and_credits_screen_render_the_notice_from_the_credits_file(
    desktop, art_credits
):
    """The licensing sentence has four copies (README, boot splash, menu line,
    credits screen). asset-credits.json is the author of it; these two are the
    ones a player actually reads, and they used to word it themselves — so a
    corrected notice could ship while the game kept saying the old thing."""
    notice = " ".join(art_credits["notice"].split())
    short = " ".join(art_credits["notice_short"].split())
    assert notice.startswith(short), (notice, short)

    menu = " ".join(desktop.locator("#overlay .credits").inner_text().split())
    assert short in menu, menu

    desktop.click("#btn-credits")
    desktop.wait_for_selector(".credits-body")
    body = " ".join(desktop.locator(".credits-body").inner_text().split())
    desktop.click("#btn-back")
    assert notice in body, body[:300]


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
    attrib = desktop.locator(".bio .attrib").inner_text()
    src = desktop.locator(".bio .attrib a").get_attribute("href")
    desktop.click("#btn-menu")
    assert n_chars >= 25, f"got {n_chars}"
    assert "Bluey" in bio and fun
    assert "Ludo Studio" in attrib, attrib
    assert src and src.startswith("https://"), src


def test_the_whole_gallery_loads_its_portraits(desktop):
    """Twenty-five lazy-loaded images: any one of them can 404 on its own."""
    desktop.click("#btn-gallery")
    desktop.wait_for_selector(".char-card")
    desktop.wait_for_function(
        "() => window.__art().loaded.length >= 25", timeout=30000
    )
    art = desktop.evaluate("window.__art()")
    desktop.click("#btn-back")
    assert not art["failed"], f"failed to load: {art['failed']}"
    fell_back = [k for k, v in art["drawn"].items() if v != "rig"]
    assert not fell_back, f"drawn without artwork: {fell_back}"


def test_the_stats_screen_opens(desktop):
    desktop.click("#btn-stats")
    desktop.wait_for_selector("table.stats")
    rows = desktop.locator("table.stats tr").count()
    desktop.click("#btn-back")
    assert rows >= 6, f"got {rows}"


# --- desktop: playing -------------------------------------------------------
# These four are one chapter played across four tests, which is what a shared
# page is for: the click that starts it, a jump, a score that climbs with real
# time, and the pause button. Each hands the running game to the next, so each
# declares it (#182) — and the last one puts the loop down.

@pytest.mark.leaves_a_game_running(reason="test_tapping_the_canvas_makes_the_player_jump "
                                          "jumps the player this starts")
def test_the_story_card_leads_into_chapter_one(desktop):
    desktop.click("#btn-play")
    desktop.wait_for_selector("#btn-go")
    assert "Keepy Uppy" in desktop.locator("h2").inner_text()
    desktop.click("#btn-go")
    desktop.wait_for_timeout(500)
    assert desktop.locator("#hud").is_visible()
    assert desktop.evaluate("window.game.mode") == "playing"


@pytest.mark.leaves_a_game_running(reason="test_the_score_climbs_while_the_level_runs "
                                          "needs the same chapter still running")
def test_tapping_the_canvas_makes_the_player_jump(desktop):
    before = desktop.evaluate("window.game.player.y")
    desktop.mouse.click(640, 500)
    desktop.wait_for_timeout(220)
    after = desktop.evaluate("window.game.player.y")
    assert after < before, f"{before} -> {after}"


@pytest.mark.leaves_a_game_running(reason="test_pause_and_resume pauses the chapter "
                                          "this one watched score")
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
    # end of the chain: nothing after this plays the chapter the story card
    # started, so it is put down rather than left painting (#182)
    desktop.evaluate("() => window.game.stop()")


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


def test_every_hero_played_as_its_real_self(desktop):
    """After all five chapters, each playable hero has been drawn in the world.
    If any drew as the fallback dog, its sprite was missing during play.

    Cameos are not asserted here: the chapters above are fast-forwarded through
    ``game.step()`` without a render pass, so a cameo standing mid-level is
    never painted. They get their own page and their own render, two tests
    below.
    """
    art = desktop.evaluate("window.__art()")
    for cid in ["bluey", "bingo", "bandit", "chilli"]:
        assert art["drawn"].get(cid) == "rig", f"{cid} drew as {art['drawn'].get(cid)}"


def test_opening_a_chapter_fetches_that_chapter_s_cast(desktop):
    """Only the menu family is fetched at boot — 25 sprites is a lot of mobile
    data. The rest arrive when a chapter's story card is opened, which is the
    only thing standing between a cameo and a second of the fallback dog.

    Runs after the reload above, so the image registry starts empty.
    """
    cast = desktop.evaluate("window.__cast")
    for i, (hero, cameo) in enumerate(cast):
        # the test above leaves the chapter list open; every later pass comes
        # back via the menu
        if not desktop.locator(".chapter-card").first.is_visible():
            desktop.click("#btn-chapters")
        desktop.wait_for_selector(".chapter-card")
        desktop.click(f".chapter-card[data-ch='{i}']")
        desktop.wait_for_selector("#btn-go")
        want = [c for c in (hero, cameo) if c]
        desktop.wait_for_function(
            "(want) => want.every((c) => window.__art().loaded.includes(c))",
            arg=want, timeout=15000,
        )
        rigged = desktop.evaluate("window.__art().rigged")
        assert all(c in rigged for c in want), f"chapter {i + 1}: no rig for {want}"
        desktop.click("#btn-back")
        desktop.wait_for_selector("#btn-play")


RUN_TO_CAMEO = """
(i) => {
  const g = window.game;
  document.getElementById('overlay').classList.add('hidden');
  const before = window.__art().loaded.slice();
  g.start(i);
  g.paused = true;   // the rAF loop keeps painting; the physics is driven here
  // same fast-forward as play_chapter, stopped just short of the cameo so it is
  // on screen and the level is still running
  for (let n = 0; n < 60000 && g.mode === 'playing' && g.player.x < g.cameoX - 40; n++) {
    if (g.player.onGround && n % 26 === 0) g.press();
    if (n % 26 === 12) g.release();
    g.step(1 / 120);
  }
  g.render();
  // read straight after the draw, before anything can decode: this is what the
  // cameo looked like on its very first frame
  return {
    cameo: g.ch.cameo, x: g.player.x, cameoX: g.cameoX, mode: g.mode,
    loadedBefore: before.includes(g.ch.cameo),
    first: window.__art().drawn[g.ch.cameo],
  };
}
"""


def test_every_cameo_is_painted_from_its_own_artwork(own_page, art_credits):
    """The cameo friends are the only characters no test has ever seen drawn.

    ``play_chapter`` fast-forwards ``game.step()`` with no render pass, so a
    cameo standing mid-level is never painted; the gallery draws portraits from
    the character list, which is a different id. A cameo whose id does not match
    its asset key would therefore wave as the procedural dog in the finished
    game and every test would still pass.

    Its own page, and the chapters are started directly rather than through the
    story card — which preloads the cast — so the first paint is the uncached
    one. Two of the five (Lucky, Nana) are in neither the menu family nor
    anything drawn before this, and those two also prove the fallback branch
    runs for real: an unloaded cameo *must* draw as the fallback dog first.
    """
    page = own_page
    cast = page.evaluate("window.__cast")
    assert [c for _, c in cast].count(None) == 0, cast

    for i, (_hero, cameo) in enumerate(cast):
        assert cameo in art_credits["assets"], f"chapter {i + 1} cameo {cameo!r} has no artwork"
        run = page.evaluate(RUN_TO_CAMEO, i)
        assert run["cameo"] == cameo, run
        assert run["x"] >= run["cameoX"] - 60, f"never reached the cameo: {run}"
        if not run["loadedBefore"]:
            assert run["first"] == "fallback", (
                f"{cameo} was not loaded yet and did not use the fallback: {run}")
        page.wait_for_function(
            "(c) => window.__art().loaded.includes(c)", arg=cameo, timeout=15000)
        page.evaluate("() => window.game.render()")
        drawn = page.evaluate("window.__art().drawn")
        # "its own artwork" is either way of using it: the cut-out rig, or a
        # pose frame the artist drew. What this test is here to catch is
        # "fallback" — the procedural dog standing in for a real character.
        assert drawn.get(cameo) in ("rig", "pose"), (
            f"chapter {i + 1}: {cameo} drew as {drawn.get(cameo)}")
    page.evaluate("() => window.game.stop()")


# --- when the pictures never arrive -----------------------------------------

@pytest.fixture
def no_art_page(own_page):
    """The same game on a connection that will not give up the artwork.

    The route is installed and then the page reloaded, because the sprites the
    menu asks for are requested during boot — routing after that would block
    nothing that matters.
    """
    own_page.route("**/assets/characters/*", lambda route: route.abort())
    own_page.reload(wait_until="domcontentloaded")
    own_page.wait_for_function("window.__ready === true", timeout=20000)
    return own_page


def test_the_fallback_dogs_carry_a_page_that_never_gets_its_artwork(no_art_page):
    """The safety net, executed. Every other test asserts the opposite — that
    nothing fell back — so this branch has never once run under test, and its
    failure mode is a blank character in front of a three-year-old on exactly
    the slow connection it exists for.
    """
    page = no_art_page
    page.click("#btn-gallery")
    page.wait_for_selector(".char-card")
    page.wait_for_function("() => window.__art().failed.length >= 25", timeout=30000)
    art = page.evaluate("window.__art()")
    assert not art["loaded"], f"something loaded anyway: {art['loaded']}"
    assert len(art["drawn"]) >= 25, f"only {len(art['drawn'])} characters were drawn at all"
    rigged = [k for k, v in art["drawn"].items() if v != "fallback"]
    assert not rigged, f"claimed to draw from artwork it never got: {rigged}"

    # ...and a fallback that draws nothing is the failure this is guarding
    ink = page.evaluate(
        """() => [...document.querySelectorAll('.char-card canvas')].slice(0, 6).map((n) => {
             const d = n.getContext('2d').getImageData(0, 0, n.width, n.height).data;
             let on = 0;
             for (let i = 3; i < d.length; i += 4) if (d[i] > 8) on++;
             return { id: n.dataset.pal, part: on / (n.width * n.height) };
           })"""
    )
    blank = [c for c in ink if c["part"] < 0.02]
    assert not blank, f"fallback drew (almost) nothing for {blank}"
    assert not any("Uncaught" in e for e in page.errors), str(page.errors[:3])


def test_a_page_that_never_gets_its_artwork_stops_asking(no_art_page):
    """The retry has to give up, and coming back online has to undo that.

    `sprite()` is called from the render loop, so a failure retried
    unconditionally is one request per character per frame — sixty a second,
    forever, on the connection least able to afford it. Five tries take under
    five seconds, though, and a tunnel lasts longer than that, so the giving up
    is only allowed to be final until the browser says the connection is back.
    """
    page = no_art_page
    asked = []
    page.on("request", lambda r: asked.append(r.url) if "/characters/" in r.url else None)
    page.click("#btn-gallery")
    page.wait_for_selector(".char-card")
    page.wait_for_function("() => window.__art().gaveUp.length >= 25", timeout=30000)
    settled = len(asked)
    # longer than the longest backoff, so "no new requests" means stopped rather
    # than merely waiting: a retry that never gives up is throttled too, and a
    # window shorter than its delay cannot tell the two apart
    page.wait_for_timeout(2500)
    assert len(asked) == settled, (
        f"still asking for artwork after giving up on it: {len(asked) - settled} more requests")
    tries = page.evaluate("() => window.__art().tries")
    assert set(tries.values()) == {5}, f"gave up after the wrong number of tries: {tries}"

    # the tunnel ends: the browser fires this, and giving up has to be undone by it
    page.evaluate("() => window.dispatchEvent(new Event('online'))")
    page.wait_for_timeout(300)
    assert len(asked) > settled, "coming back online asked for nothing"


def test_one_dropped_request_is_not_the_final_answer(own_page):
    """A dropped connection is not a missing file. This is the phone-in-a-car
    case: the first request for a character fails, every later one succeeds, and
    the character must end up drawn from its own artwork rather than staying a
    procedural dog until someone reloads — which a three-year-old will not do.
    """
    page = own_page
    dropped, retried = [], []

    def once(route):
        # bluey is the menu's first dog and is asked for during boot, so the drop
        # lands on the real path rather than on a request invented by the test
        if "bluey" in route.request.url and not dropped:
            dropped.append(route.request.url)
            route.abort()
        else:
            if "bluey" in route.request.url:
                retried.append(route.request.url)
            route.continue_()

    page.route("**/assets/characters/*", once)
    page.reload(wait_until="domcontentloaded")
    page.wait_for_function("window.__ready === true", timeout=20000)
    page.wait_for_function("() => window.__art().failed.includes('bluey')", timeout=15000)
    assert dropped, "the route never fired: nothing was dropped, so nothing is being tested"

    page.wait_for_function("() => window.__art().loaded.includes('bluey')", timeout=15000)
    art = page.evaluate("window.__art()")
    assert "bluey" not in art["failed"], "still marked failed after it loaded"
    assert art["drawn"].get("bluey") in ("rig", "pose"), (
        f"the menu is still drawing the fallback dog: {art['drawn']}")
    # the retry must not be answerable from whatever cached the failure
    assert any("retry=" in u for u in retried), f"retried the identical URL: {retried}"
    page.unroute("**/assets/characters/*")


RIG_DIFF = """
async ({ id, state, times, drop, dropOnce, nudge, region, pad }) => {
  const s = await import('/js/sprites.js');
  const art = await s.loadArt();
  // Every caller of this helper is asking a question about the *rig* — does
  // this tail rotate, is this ear box cut from the right place. A character
  // with pose art for `state` draws that instead and the rig is never touched,
  // so the answer would be "nothing moved" for a reason that has nothing to do
  // with the rig. Take the poses off for the duration; `art.poses` is the
  // module's own object, the same aliasing `drop` uses on `art.rigs`.
  const heldPoses = art.poses[id];
  delete art.poses[id];
  s.preload([id]);
  for (let i = 0; i < 100 && !s.artState().loaded.includes(id); i++) {
    await new Promise((r) => setTimeout(r, 50));
  }
  const im = new Image();
  im.src = art.credits.assets[id].file;
  await im.decode();
  const size = 320, h = size * 0.9, w = (h * im.width) / im.height;
  const left = size / 2 - w / 2, top = size - h;
  const rig = art.rigs[id];

  // the region of interest, in canvas pixels, padded: a part swings outside
  // the box it was cut from
  const boxes = [];
  if (region === 'tail' && rig.tail) boxes.push(rig.tail.box);
  if (region === 'eyes') (rig.eyes || []).forEach((e) => boxes.push(e));
  if (region === 'ears') (rig.ears || []).forEach((e) => boxes.push([e.box[0], 0, e.box[2], e.box[3]]));
  const rects = boxes.map((b) => [
    left + (b[0] - pad) * w, top + (b[1] - pad) * h,
    left + (b[2] + pad) * w, top + (b[3] + pad) * h,
  ]);

  // 'blink' means "whenever this character happens to blink", found with the
  // module's own clock rather than a copy of its offset
  const at = times.map((t) => {
    if (t !== 'blink') return t;
    for (let u = 0; u < 4; u += 0.005) if (s.blinkAmount(id, u) > 0.99) return u;
    return 0;
  });

  const shot = (t) => {
    const c = document.createElement('canvas');
    c.width = size; c.height = size + 4;
    const ctx = c.getContext('2d');
    s.drawCharacter(ctx, id, size / 2, size, h, null, t, state, 1);
    return ctx.getImageData(0, 0, c.width, c.height).data;
  };
  // `drop` takes parts off the rig for both frames — the same animation played
  // by a character who has no tail. `dropOnce` takes them off for the second
  // frame only, which compares two rigs rather than two moments.
  const saved = {};
  const lift = (keys) => keys.forEach((k) => {
    if (k in rig) { saved[k] = rig[k]; delete rig[k]; }
  });
  lift(drop);
  const before = shot(at[0]);
  lift(dropOnce);
  // moving a part's pivot for the second frame makes the rig's own rotation the
  // only difference between two otherwise identical draws: at an angle of zero
  // spinning about a different point cannot change a pixel, and mid-swing it must
  const held = nudge ? rig[nudge.part] : null;
  if (held) {
    const move = (pv) => [pv[0] + nudge.by[0], pv[1] + nudge.by[1]];
    rig[nudge.part] = Array.isArray(held)
      ? held.map((e) => ({ ...e, pivot: move(e.pivot) }))
      : { ...held, pivot: move(held.pivot) };
  }
  const after = shot(at[1]);
  if (held) rig[nudge.part] = held;
  Object.assign(rig, saved);
  if (heldPoses) art.poses[id] = heldPoses;

  let inside = 0, outside = 0;
  const stray = [size, size, 0, 0]; // where the unexpected pixels are, for the message
  for (let y = 0; y < size + 4; y++) {
    for (let x = 0; x < size; x++) {
      const i = (y * size + x) * 4;
      let d = 0;
      for (let k = 0; k < 4; k++) d = Math.max(d, Math.abs(before[i + k] - after[i + k]));
      if (d <= 12) continue;
      if (rects.some((r) => x >= r[0] && x <= r[2] && y >= r[1] && y <= r[3])) { inside++; continue; }
      outside++;
      stray[0] = Math.min(stray[0], x); stray[1] = Math.min(stray[1], y);
      stray[2] = Math.max(stray[2], x); stray[3] = Math.max(stray[3], y);
    }
  }
  return { inside, outside, stray, rects, drawn: s.artState().drawn[id], at };
}
"""


def rig_diff(page, id, state, times, region=None, pad=0.0, drop=(), drop_once=(), nudge=None):
    """Pixels that change between two frames, split by whether they fall in the
    region of interest. `nudge` is (part, dx, dy). See RIG_DIFF."""
    return page.evaluate(RIG_DIFF, {
        "id": id, "state": state, "times": list(times), "region": region,
        "pad": pad, "drop": list(drop), "dropOnce": list(drop_once),
        "nudge": {"part": nudge[0], "by": list(nudge[1:])} if nudge else None,
    })


def test_the_tail_wags(desktop):
    """The whole point of the rig, and the one thing that reads at gameplay
    size.

    Not a diff of two moments: in a run cycle the body lifts and squashes, so
    the tail corner changes even for a dog whose tail never moves, and the
    cut-out lands on different subpixels each frame. Instead the same moment is
    drawn twice with the tail's pivot moved. Rotating about a different point
    only shows if there is a rotation at all, so the frame a quarter of a wag in
    must change and the frame where the angle passes through zero cannot.
    """
    swung = rig_diff(desktop, "bluey", "run", [0.0714, 0.0714], "tail", 0.2, nudge=("tail", 0.15, 0))
    rest = rig_diff(desktop, "bluey", "run", [0, 0], "tail", 0.2, nudge=("tail", 0.15, 0))
    assert swung["drawn"] == "rig", swung
    assert rest["inside"] + rest["outside"] == 0, f"rotated at rest? {rest}"
    assert swung["inside"] > 100, f"the pose is not swinging the tail: {swung}"


@pytest.mark.parametrize("cid", EARED)
def test_the_ears_flop(desktop, cid):
    """The same trick as the tail, on the pair of ears, which swing mirrored and
    a third as far. Their zero is not at t=0 — the run pose runs them a little
    behind the legs — so both moments are read off `ear: Math.sin(step - 0.7)`
    with step = t * 11.

    Every character with ears (#139): an ear is *cut out* of the head and redrawn
    rotated, so a box that takes too little leaves the ear behind and one that
    takes too much rotates a slice of skull with it. Both show up as movement.
    """
    # 0.7 / STRIDE, not the 0.0636 it rounds to: a rounded-off zero leaves a
    # residual angle of 4e-5 rad, which is exactly zero pixels of movement until
    # the body is drawn under a rotation and a squash, and then it is up to ten
    # of resampling. The zero has to be the real one for this to be a control.
    rest_at = 0.7 / desktop.evaluate("async () => (await import('/js/sprites.js')).STRIDE")
    flop = rig_diff(desktop, cid, "run", [0.2064, 0.2064], "ears", 0.2, nudge=("ears", 0.15, 0))
    rest = rig_diff(desktop, cid, "run", [rest_at, rest_at], "ears", 0.2, nudge=("ears", 0.15, 0))
    assert flop["drawn"] == "rig", flop
    assert rest["inside"] + rest["outside"] == 0, f"rotated at rest? {rest}"
    assert flop["inside"] > 100, f"the pose is not swinging the ears: {flop}"


def test_removing_the_tail_changes_only_the_tail_corner(desktop):
    """The tail is cut out of the leg band and redrawn at an angle. Cut wrong,
    it tears a hole in a leg or leaves a copy behind — either way the picture
    changes somewhere other than the tail.

    Also the optional-part guarantee: with `tail` deleted the rig still draws
    from the artwork rather than falling back to the procedural dog.
    """
    r = rig_diff(desktop, "bluey", "jump", [0, 0], "tail", 0.1, drop_once=["tail"])
    assert r["drawn"] == "rig", r
    assert r["outside"] < 30, f"dropping the tail changed the body at {r['stray']}: {r}"


@pytest.mark.parametrize("cid", EYED)
def test_the_eyes_shut_for_a_blink(desktop, cid):
    """A still pose sampled off a blink and again at its peak. Nothing else in
    the pose moves — the check below proves that by replaying the same two
    moments with the eye boxes removed and getting an identical picture — so
    every pixel that changes is the blink, and it must land on the eyes.

    Every character with eyes, not just Bluey: most of these boxes were proposed
    by ``build_rigs.py --suggest`` from the artwork's own white blobs (#139), and
    a wipe that misses does not fail anywhere else — it just paints a slab of fur
    colour across a cheek, twice a chapter, on one dog.

    What this cannot tell you is whether the box is on an *eye*: the region it
    compares against is the rig's own box, so a box measured onto a muzzle would
    pass here happily. That much is a looking-at-it job (``--sheet``); this is
    the part a machine can hold, that each of them blinks and that the wipe stays
    where the rig says it will.
    """
    r = rig_diff(desktop, cid, "jump", [0, "blink"], "eyes", 0.1)
    blind = rig_diff(desktop, cid, "jump", [0, "blink"], "eyes", 0.1, drop=["eyes"])
    assert r["drawn"] == "rig", r
    assert blind["inside"] + blind["outside"] == 0, f"the pose is not still: {blind}"
    assert r["inside"] > 200, f"the eyes did not close: {r}"
    # a ratio and a tenth of the width of padding, not zero: the pose leans and
    # squashes the body, so a box measured on the flat artwork lands a few
    # degrees off once it is drawn — Bandit's small right eye by about 7px
    assert r["outside"] < r["inside"] * 0.05, f"the blink painted across the face: {r}"


def test_a_rig_with_no_extras_still_draws_its_character(desktop):
    """Two of the twenty-five have no tail, ears or eyes measured, and most of
    the rest are missing at least one. A rig without them must still draw from
    the artwork rather than falling back to the procedural dog."""
    r = rig_diff(desktop, "bluey", "jump", [0, 0], drop_once=["tail", "ears", "eyes"])
    assert r["drawn"] == "rig", f"drew as {r['drawn']} without its extras"
    assert r["outside"] > 0, "dropping every part changed nothing — was anything drawn?"


BOUND_PROBE = """
async ({ id, steps }) => {
  const s = await import('/js/sprites.js');
  const art = await s.loadArt();
  // the rig is the thing under test, so take the pose artwork off for the
  // duration — same aliasing of the module's own object as RIG_DIFF
  const heldPoses = art.poses[id];
  delete art.poses[id];
  s.preload([id]);
  for (let i = 0; i < 100 && !s.artState().loaded.includes(id); i++) {
    await new Promise((r) => setTimeout(r, 50));
  }

  // one contact to the next, sampled evenly. The cadence is the module's own —
  // a copy of STRIDE here would keep passing after the rig stopped using it.
  const half = Math.PI / s.STRIDE;
  const eps = half / 400;
  const W = 320, H = 460, size = 200, floor = 400;
  const frames = [];
  for (let i = 0; i <= steps; i++) {
    const t = (i * half) / steps;
    const c = document.createElement('canvas');
    c.width = W; c.height = H;
    const ctx = c.getContext('2d');
    s.drawCharacter(ctx, id, W / 2, floor, size, null, t, 'run', 1);
    const d = ctx.getImageData(0, 0, W, H).data;
    // alpha > 120 is the character: the contact shadow never gets past 0.2, so
    // this is the dog's own silhouette and not the mark it leaves on the ground
    let top = H, bottom = -1, left = W, right = -1;
    for (let y = 0; y < H; y++) {
      for (let x = 0; x < W; x++) {
        if (d[(y * W + x) * 4 + 3] <= 120) continue;
        if (y < top) top = y;
        if (y > bottom) bottom = y;
        if (x < left) left = x;
        if (x > right) right = x;
      }
    }
    frames.push({ t, up: floor - bottom, tall: bottom - top, wide: right - left,
                  contact: s.footfall((t - eps) * s.STRIDE, (t + eps) * s.STRIDE) });
  }
  if (heldPoses) art.poses[id] = heldPoses;
  return { frames, size, drawn: s.artState().drawn[id] };
}
"""


def test_a_character_with_no_run_artwork_bounds_instead_of_sliding(desktop):
    """#168: a dog whose legs cannot move is not jogging, it is bounding.

    The rig cuts one front-facing standing render into bands, so a band below
    the hip is not a leg — it is both legs, the gap between them and, for
    Muffin, her tail. Swinging it slid a grey slab out sideways. So the rig
    bounds the whole drawing instead: off the ground, gathered when it lands,
    drawn out at the top, one smooth arc rather than a jitter. Measured off the
    silhouette it actually paints, sampled from one footfall to the next.

    Chilli was the case this was written for; she has a stride render now
    (#206). Muffin is the one left — she is on the character select screen and
    her 43 files on the wiki are the standing render, unboxing screenshots and
    group shots, so the rig is all she will ever have. `RIGGED_RUN` is read from
    the shipped data, so the day somebody draws her running this test loses its
    subject rather than going on passing over nobody.

    Every hero is measured too, with its pose art taken off, because the rig is
    the thing under test and it has to hold up for whoever ends up on it.
    """
    heroes = sorted({hero for hero, _ in desktop.evaluate("window.__cast")})
    assert len(heroes) >= 4, heroes
    assert RIGGED_RUN, ("every playable character has run artwork now — the rig no longer "
                        "runs anyone, so this test is guarding nothing; see RIG_OK in "
                        "fetch_assets.py before deleting it")

    for cid in sorted(set(heroes) | set(RIGGED_RUN)):
        r = desktop.evaluate(BOUND_PROBE, {"id": cid, "steps": 8})
        f, size = r["frames"], r["size"]
        assert r["drawn"] == "rig", f"{cid} drew as {r['drawn']}, not the rig"

        # the ends of the sample are the contacts the dust is spawned on, and
        # they are where the feet are down: an arc out of step with `footfall`
        # puffs dust under a dog that is already in the air
        assert f[0]["contact"] and f[-1]["contact"], \
            f"{cid}: sampled a stride that does not start and end on a footfall: {f}"
        assert not any(x["contact"] for x in f[1:-1]), \
            f"{cid}: more contacts than the arc has bottoms: {f}"
        for end in (f[0], f[-1]):
            assert end["up"] <= 2, f"{cid}: feet {end['up']}px off the ground at a footfall"

        up = [x["up"] for x in f]
        apex = f[up.index(max(up))]
        assert max(up) >= 0.1 * size, \
            f"{cid}: bounds {max(up)}px on a {size}px body — that is a bob, not a bound"
        # one hop: up all the way to the top, then down all the way back
        top = up.index(max(up))
        assert up[:top + 1] == sorted(up[:top + 1]) and up[top:] == sorted(up[top:], reverse=True), \
            f"{cid}: the arc is not one rise and one fall: {up}"

        # gathered on the ground, drawn out in the air — the squash and stretch
        # that says the body is pushing off rather than being carried
        assert apex["tall"] > f[0]["tall"] * 1.05, \
            f"{cid}: same shape landing ({f[0]['tall']}px) as at the top ({apex['tall']}px)"


# --- pose frames: the artist's own drawing of the action --------------------

POSE_DIFF = """
async ({ a, b, mirror }) => {
  const s = await import('/js/sprites.js');
  const art = await s.loadArt();
  const size = 320;

  const shot = async (spec) => {
    // `frames` replaces what poses.json says for this character, so a test can
    // ask "which file did it draw?" by naming a different one. art.poses is
    // the module's own object; the rig tests lean on the same aliasing.
    const held = art.poses[spec.id];
    if (spec.frames) art.poses[spec.id] = spec.frames;
    s.preload([spec.id]);
    const want = Object.values(art.poses[spec.id] || {}).flat();
    for (let i = 0; i < 100; i++) {
      const have = s.artState().poseFrames;
      if (want.every((f) => have.includes(f))) break;
      await new Promise((r) => setTimeout(r, 50));
    }
    const c = document.createElement('canvas');
    c.width = size; c.height = size + 4;
    const ctx = c.getContext('2d');
    s.drawCharacter(ctx, spec.id, size / 2, size, size * 0.9, null,
                    spec.t, spec.state, spec.facing === undefined ? 1 : spec.facing);
    const out = {
      data: ctx.getImageData(0, 0, c.width, c.height).data,
      drawn: s.artState().drawn[spec.id],
    };
    if (held === undefined) delete art.poses[spec.id]; else art.poses[spec.id] = held;
    return out;
  };

  const A = await shot(a);
  const B = await shot(b);
  // `mirror` compares A against B flipped about the vertical centre line, which
  // is what facing is supposed to do to the picture.
  let changed = 0, opaqueA = 0, opaqueB = 0;
  for (let y = 0; y < size + 4; y++) {
    for (let x = 0; x < size; x++) {
      const i = (y * size + x) * 4;
      const j = (y * size + (mirror ? size - 1 - x : x)) * 4;
      if (A.data[i + 3] > 8) opaqueA++;
      if (B.data[j + 3] > 8) opaqueB++;
      let d = 0;
      for (let k = 0; k < 4; k++) d = Math.max(d, Math.abs(A.data[i + k] - B.data[j + k]));
      if (d > 12) changed++;
    }
  }
  return { changed, opaqueA, opaqueB, a: A.drawn, b: B.drawn };
}
"""


def pose_diff(page, a, b, mirror=False):
    """Pixels that differ between two draws of the same character. See POSE_DIFF."""
    return page.evaluate(POSE_DIFF, {"a": a, "b": b, "mirror": mirror})


@pytest.mark.parametrize("cid,state", POSED, ids=[f"{c}-{s}" for c, s in POSED])
def test_a_pose_frame_is_drawn_instead_of_the_rig(desktop, cid, state):
    """The point of the whole change: where there is a drawing of the action,
    that drawing is what appears — not a standing render cut into bands.

    Drawn *and* different from the rig's version of the same moment. Reporting
    "pose" while painting nothing would satisfy the first half on its own.
    """
    r = pose_diff(desktop,
                  {"id": cid, "state": state, "t": 0.31},
                  {"id": cid, "state": state, "t": 0.31, "frames": {}})
    assert r["a"] == "pose", f"{cid} in {state} drew as {r['a']}"
    assert r["b"] == "rig", f"with no pose frames {cid} drew as {r['b']} — not the rig"
    assert r["opaqueA"] > 1000, f"the pose frame painted almost nothing: {r}"
    assert r["changed"] > r["opaqueA"] * 0.1, (
        f"the pose render is the same picture as the rig's: {r}")


@pytest.mark.parametrize("cid,state", POSED, ids=[f"{c}-{s}" for c, s in POSED])
def test_the_pose_drawn_is_the_file_the_data_names(desktop, cid, state):
    """A pose that draws *some* frame is not the same as one that draws the
    right frame: `poseFrame` picks by index into a list, so an off-by-one or a
    stale cache key would still show a dog.

    Rather than re-doing the draw transform here to compare against the PNG —
    the mistake #157 records — the same draw is asked for twice with only the
    named file changed. Identical output would mean the name is not what it is
    drawing from.
    """
    other = "assets/poses/bluey-cheer-0.png" if cid != "bluey" else "assets/poses/bingo-cheer-0.png"
    r = pose_diff(desktop,
                  {"id": cid, "state": state, "t": 0.31},
                  {"id": cid, "state": state, "t": 0.31, "frames": {state: [other]}})
    assert r["a"] == "pose" and r["b"] == "pose", r
    assert r["opaqueA"] > 1000 and r["opaqueB"] > 1000, f"one of them is blank: {r}"
    assert r["changed"] > r["opaqueA"] * 0.2, (
        f"swapping the frame file changed almost nothing — is it drawing what "
        f"poses.json names? {r}")


@pytest.mark.parametrize("cid,state", POSED, ids=[f"{c}-{s}" for c, s in POSED])
def test_a_pose_faces_the_way_it_is_travelling(desktop, cid, state):
    """Every pose render is drawn facing one way. A side-scroller that ignores
    `facing` has the character running backwards the moment it turns around,
    which is worse than the front-facing standing render it replaced.

    The check is that facing -1 is facing 1 *mirrored*, not merely different: a
    draw that ignored facing and happened to move would pass 'different'.
    """
    same = pose_diff(desktop,
                     {"id": cid, "state": state, "t": 0.31, "facing": 1},
                     {"id": cid, "state": state, "t": 0.31, "facing": -1},
                     mirror=True)
    assert same["a"] == "pose" and same["b"] == "pose", same
    assert same["opaqueA"] > 1000, f"nothing was drawn: {same}"
    # not zero: the sprite is tilted, so the mirror of a lean is not the lean —
    # the two pictures agree on the body and disagree along the edges
    assert same["changed"] < same["opaqueA"] * 0.35, (
        f"facing -1 is not the mirror of facing 1: {same}")

    unmirrored = pose_diff(desktop,
                           {"id": cid, "state": state, "t": 0.31, "facing": 1},
                           {"id": cid, "state": state, "t": 0.31, "facing": -1})
    assert unmirrored["changed"] > same["changed"], (
        f"flipping made no difference to the picture — facing is ignored: {unmirrored}")


def test_every_pose_state_ships_exactly_one_frame():
    """The wiki holds one action render of each character, so each state is a
    still. This reads the shipped poses.json rather than restating it: the day a
    real two-frame set lands, this fails and the pair below has to be revisited
    together — a cycle needs drawing code, and drawing code needs a test."""
    bad = {f"{cid}.{state}": len(POSES[cid][state])
          for cid, state in POSED if len(POSES[cid][state]) != 1}
    assert not bad, (
        f"a pose set is no longer a single still: {bad} — sprites.js draws frames[0] "
        "and never advances, so every frame after the first would be dead data")


def test_a_pose_never_advances_past_its_first_frame(desktop):
    """The decision #169 asked for, pinned in the picture: poses do not cycle.

    The same character at two moments a long way apart, given a two-frame set
    whose second frame is a completely different drawing. If any cadence is
    reintroduced, one of these samples lands on frame 1 and the drawing changes.
    So this is checked against a one-frame set at the same instants — the motion
    applied on top is identical, and only the artwork could differ.
    """
    one = ["assets/poses/bluey-run-0.png"]
    two = ["assets/poses/bluey-run-0.png", "assets/poses/bluey-cheer-0.png"]

    for t in (0.042, 0.125, 0.31, 0.5, 1.7):
        r = pose_diff(desktop,
                      {"id": "bluey", "state": "run", "t": t, "frames": {"run": one}},
                      {"id": "bluey", "state": "run", "t": t, "frames": {"run": two}})
        assert r["a"] == "pose" and r["b"] == "pose", r
        assert r["opaqueA"] > 1000, f"nothing was drawn at t={t}: {r}"
        assert r["changed"] == 0, (
            f"at t={t} the second frame of the set was drawn: {r} — poses are stills, "
            "one render per state; see poseFrame in sprites.js")


def test_a_held_jump_keeps_the_leaping_drawing(desktop):
    """`float` is the jump held down, and nobody has drawn a separate one.

    Without POSE_FALLBACK a held jump swapped the artist's leaping render for the
    rig half-way up and back again on the way down — the sliced-limbs look
    reappearing mid-air on the one character with the artwork to avoid it. Nobody
    reported it because the pose tests only check states poses.json names, and it
    names no float.
    """
    r = pose_diff(desktop,
                  {"id": "bluey", "state": "float", "t": 0.31},
                  {"id": "bluey", "state": "float", "t": 0.31, "frames": {}})
    assert r["a"] == "pose", f"a held jump drew as {r['a']} — the fall drops to the rig"
    assert r["b"] == "rig", f"with no pose frames float drew as {r['b']} — not the rig"
    assert r["opaqueA"] > 1000, f"the borrowed frame painted almost nothing: {r}"

    # and what it borrows is the jump set specifically. Renaming the file under
    # `jump` has to change the picture float draws, or it is reading something
    # else and happening to look right.
    swapped = pose_diff(
        desktop,
        {"id": "bluey", "state": "float", "t": 0.31,
         "frames": {"jump": ["assets/poses/bluey-jump-0.png"]}},
        {"id": "bluey", "state": "float", "t": 0.31,
         "frames": {"jump": ["assets/poses/bluey-cheer-0.png"]}})
    assert swapped["a"] == "pose" and swapped["b"] == "pose", swapped
    assert swapped["changed"] > swapped["opaqueA"] * 0.1, (
        f"float ignored the jump set: {swapped} — see POSE_FALLBACK in sprites.js")


def test_a_state_with_its_own_art_does_not_fall_back(desktop):
    """The fallback is a floor, not an override: the day somebody draws a real
    floating Bluey, that drawing is what floats.

    Two sets that agree on float and disagree on jump, both asked for float. The
    lookup is `set[state] || set[POSE_FALLBACK[state]]`, so these are the same
    picture; consult the fallback first and they are two different dogs.
    """
    r = pose_diff(desktop,
                  {"id": "bluey", "state": "float", "t": 0.31,
                   "frames": {"float": ["assets/poses/bluey-run-0.png"],
                              "jump": ["assets/poses/bluey-jump-0.png"]}},
                  {"id": "bluey", "state": "float", "t": 0.31,
                   "frames": {"float": ["assets/poses/bluey-run-0.png"],
                              "jump": ["assets/poses/bluey-cheer-0.png"]}})
    assert r["a"] == "pose" and r["b"] == "pose", r
    assert r["opaqueA"] > 1000, f"nothing was drawn: {r}"
    assert r["changed"] == 0, (
        f"float drew the jump frame over its own: {r} — the fallback is only for a "
        "state with no artwork of its own")


# --- the footfalls: what makes a still read as a run ------------------------

FOOTFALL = """
async ({ steps }) => {
  const s = await import('/js/sprites.js');
  let t = 0, hits = 0;
  // seconds in, phase out: `footfall` is asked about ground covered, and a
  // caller standing still covers it at the nominal speed (see stridePhase).
  for (const dt of steps) { if (s.footfall(t * s.STRIDE, (t + dt) * s.STRIDE)) hits++; t += dt; }
  return { hits, stride: s.STRIDE, t };
}
"""


@pytest.mark.parametrize("dt,ids", [(1 / 240, "240fps"), (1 / 60, "60fps"), (1 / 12, "12fps")],
                         ids=["240fps", "60fps", "12fps"])
def test_the_same_footfalls_are_reported_at_every_frame_rate(desktop, dt, ids):
    """Contacts are a property of the ground covered, not of how often it is
    read — this probe covers it at a steady speed, so its phase is a clock. A
    frame long enough to step over a whole contact must still report it, and a
    very short one must not report the same contact twice — otherwise the dust
    thins out on a fast machine and doubles up on a slow one."""
    span = 2.0
    r = desktop.evaluate(FOOTFALL, {"steps": [dt] * round(span / dt)})
    # exactly, not about: one contact every half turn, and the count of half
    # turns in the span the harness actually walked. A tolerance of one here
    # would forgive a window that overlaps its neighbour, which is the way a
    # frame-rate assumption gets in — it double-counts only the contacts that
    # land in the overlap, so it is a few percent off and always looks fine.
    want = math.floor(r["t"] * r["stride"] / math.pi)
    assert r["hits"] == want, (
        f"{r['hits']} footfalls in {r['t']:.3f}s at {ids}, expected {want}")


def test_a_footfall_is_reported_once(desktop):
    """The interval either contains a contact or it does not; asking twice about
    the same stretch of time must not produce two puffs of dust."""
    beat = math.pi / desktop.evaluate("async () => (await import('/js/sprites.js')).STRIDE")
    # a hair either side of the first contact after zero
    r = desktop.evaluate(FOOTFALL, {"steps": [beat * 0.99, beat * 0.02, beat * 0.5]})
    assert r["hits"] == 1, f"the contact at {beat:.3f}s was counted {r['hits']} times"


def test_running_kicks_up_dust_under_the_feet(own_page):
    """Its own page: this drives the physics by hand and leaves the engine mid-
    chapter, which the shared desktop page's later tests would inherit.

    Every other source of particles is taken away first, so a burst can only be
    a footfall: the player is pinned to the ground (landing puffs need an
    airborne frame), the collectibles, which sparkle when picked up, are emptied
    out of the level, and the balloon — which sparkles eight when it is bopped —
    is taken away.
    """
    r = own_page.evaluate(
        """async () => {
            const { stridePhase } = await import('/js/sprites.js');
            const g = window.game;
            document.getElementById('overlay').classList.add('hidden');
            g.start(0);
            g.level.tokens = [];
            g.level.secret.taken = true;
            g.balloon = null;
            g.particles.length = 0;
            // phase, not seconds: the cadence is paid for in ground covered on
            // foot, so the count this test wants is a count of half turns of the
            // stride, and the stride only turns while the player is moving.
            const from = stridePhase(g.player.strode);
            let bursts = 0;
            const dt = 1 / 120;
            // count particles that are new, not a longer array: a burst landing
            // on the step that sweeps up the last one leaves the length alone,
            // so a shorter particle life would read here as a missing footfall
            const known = new WeakSet();
            for (let n = 0; n < 240; n++) {
                g.player.onGround = true; g.player.vy = 0;
                g.step(dt);
                let fresh = 0;
                for (const q of g.particles) if (!known.has(q)) { known.add(q); fresh++; }
                if (fresh) bursts++;
            }
            const out = { bursts, from, to: stridePhase(g.player.strode),
                          secs: g.t, state: g.player.state };
            g.stop();   // `start()` left the loop painting this chapter (#182)
            return out;
        }"""
    )
    assert r["state"] == "run", f"the player was not running: {r}"
    want = math.floor(r["to"] / math.pi) - math.floor(r["from"] / math.pi)
    assert r["bursts"] == want, (
        f"{r['bursts']} dust puffs over {r['to'] - r['from']:.2f} rad of stride "
        f"({r['secs']:.2f}s of running), expected {want} — one per footfall, and no "
        "step may claim a contact its neighbour already reported")


def test_the_cadence_is_paid_for_in_ground_not_in_seconds(own_page):
    """A slowed player takes the same number of steps to cross the same ground.

    This is the footskate: the chapters run at 220-292 px/s and a bumped player
    keeps 45% of that, so one wall-clock rhythm has the feet paddling at up to
    three times the speed of the floor under them. Driving the same *distance*
    at two speeds is the reading that tells the two apart — a clock-driven
    cadence spends far more steps on the slow lap, and it is only wrong by a
    ratio, so a same-speed test sees nothing.
    """
    laps = own_page.evaluate(
        """async ({ far }) => {
            const { stridePhase } = await import('/js/sprites.js');
            const g = window.game;
            document.getElementById('overlay').classList.add('hidden');
            const out = [];
            for (const slowed of [false, true]) {
                g.start(0);
                g.level.tokens = [];
                g.level.secret.taken = true;
                g.balloon = null;
                g.particles.length = 0;
                const known = new WeakSet();
                let bursts = 0, secs = 0;
                const dt = 1 / 120;
                while (g.player.strode < far) {
                    g.player.onGround = true; g.player.vy = 0;
                    if (slowed) g.player.slow = 1;   // topped up: it decays by dt
                    g.step(dt);
                    secs += dt;
                    let fresh = 0;
                    for (const q of g.particles) if (!known.has(q)) { known.add(q); fresh++; }
                    if (fresh) bursts++;
                }
                out.push({ slowed, bursts, secs, phase: stridePhase(g.player.strode) });
            }
            g.stop();
            return out;
        }""",
        {"far": 900},
    )
    fast, slow = laps
    # the two laps must really have differed, or they are the same reading twice
    assert slow["secs"] > fast["secs"] * 1.5, (
        f"the slowed lap took {slow['secs']:.2f}s against {fast['secs']:.2f}s — the "
        "slow was not applied, so this test is comparing a run with itself")
    assert fast["bursts"] == slow["bursts"], (
        f"{fast['bursts']} footfalls crossing 900px at speed against "
        f"{slow['bursts']} crossing the same 900px slowed — the feet are keeping "
        "time with the clock instead of with the floor")


# A particle in the array is not a particle on the screen. The running dust was
# once white at 0.55 alpha over a path that is nearly white: three of them
# existed at the feet and the picture was byte-identical (#169). That was fixed
# with a test for that one source on that one chapter, and every other burst was
# still only ever checked by `particles.length` — which counts objects, not
# pixels, and says nothing about the five chapters' different palettes (#173).
#
# So: one entry per call site that makes particles. `make` fires exactly that
# burst and returns "" (or the reason it cannot fire here — some chapters have
# no balloon), and the probe below measures what the burst is worth in pixels.
# The siblings are stubbed out first, so what is measured can only be this
# source; the probe stubs all three afterwards, so a later frame's burst cannot
# be counted as this one's.
#
# The one reason a burst can be missing that is not a fault: not every chapter
# has a balloon. Spelled once, here, and substituted into the JS so the reason
# the probe returns and the reason the test forgives cannot drift apart.
NO_BALLOON = "this chapter has no balloon"
ABSENT = {NO_BALLOON}

PARTICLE_SOURCES = {
    "the takeoff puff": {"factory": "puff", "make": """
        g.scuff = () => {}; g.sparkle = () => {};
        g.player.onGround = true; g.jumpBuffer = 0.2;
        g.particles = [];
        g.step(1 / 120);
        if (!g.particles.length) return "the jump made no puff";
        return "";
    """},
    "the landing puff": {"factory": "puff", "make": """
        g.scuff = () => {}; g.sparkle = () => {};
        g.player.onGround = true; g.jumpBuffer = 0.2;
        g.step(1 / 120);                       // up, and throw that puff away
        g.particles = [];
        for (let n = 0; n < 400 && !g.particles.length; n++) g.step(1 / 120);
        if (!g.particles.length) return "never landed again";
        return "";
    """},
    "the footfall dust": {"factory": "scuff", "make": """
        g.puff = () => {}; g.sparkle = () => {};
        g.particles = [];
        for (let n = 0; n < 600 && !g.particles.length; n++) {
            g.player.onGround = true; g.player.vy = 0;   // pinned: only footfalls
            g.step(1 / 120);
        }
        if (!g.particles.length) return "no footfall in five seconds of running";
        return "";
    """},
    "a collected token's sparkle": {"factory": "sparkle", "make": """
        g.puff = () => {}; g.scuff = () => {};
        const tk = g.level.tokens.find((t) => !t.taken);
        if (!tk) return "no token left to collect";
        // moved to where one is about to be picked up — beside the player and a
        // little above it, which is where they float — rather than onto it: at
        // the player's own position the character is drawn over the burst
        tk.x = g.player.x + 40; tk.y = g.player.y - 90;
        g.particles = [];
        g.step(1 / 120);
        if (!g.particles.length) return "the token was not collected";
        return "";
    """},
    "the secret's sparkle": {"factory": "sparkle", "make": """
        g.puff = () => {}; g.scuff = () => {};
        const sec = g.level.secret;
        if (sec.taken) return "the secret is already found";
        sec.x = g.player.x + 40; sec.y = g.player.y - 90;
        g.particles = [];
        g.step(1 / 120);
        if (!g.particles.length) return "the secret was not found";
        return "";
    """},
    "the bopped balloon's sparkle": {"factory": "sparkle", "make": """
        g.puff = () => {}; g.scuff = () => {};
        if (!g.balloon) return NO_BALLOON;
        g.balloon.x = g.player.x + 40; g.balloon.y = g.player.y - 120;
        g.balloon.vy = 0;
        g.particles = [];
        g.step(1 / 120);
        if (!g.particles.length) return "the balloon was not bopped";
        return "";
    """},
}

# Draw the frame, take the burst away, draw it again, count what changed. Both
# renders happen inside one synchronous block, so nothing moves between them and
# the difference is the particles and only the particles.
#
# Measured over the whole life of the burst, not at the frame it is born on: a
# sparkle spawns inside the character that set it off and is hidden for the
# first frames, which reads as "changed no pixels at all" if you look once.
#
# The whole life means all of it — the longest here is the sparkle's 0.6s, which
# is 36 frames, and the loop stops early anyway when the last particle dies. The
# window used to be 8 frames, which is the burst still inside the character that
# made it: it read a secret's sparkle at a third of its real worth, and a run
# that lifts the body over the burst was enough to fail it.
SEEN_PROBE = """
({ chapter, frames }) => {
    const g = window.game;
    document.getElementById('overlay').classList.add('hidden');
    g.start(chapter);
    const make = () => { /*MAKE*/ };
    let out;
    try {
        const why = make();
        if (why) return { skipped: why };
        const had = g.particles.length;
        g.puff = () => {}; g.scuff = () => {}; g.sparkle = () => {};
        const c = g.ctx.canvas, w = c.width, h = c.height;
        let seen = 0, top = 0, alive = 0;
        for (let n = 0; n < frames && g.particles.length; n++) {
            alive++;
            g.render();
            const shown = g.ctx.getImageData(0, 0, w, h).data;
            const kept = g.particles;
            g.particles = [];
            g.render();
            const bare = g.ctx.getImageData(0, 0, w, h).data;
            g.particles = kept;
            let count = 0, peak = 0;
            for (let i = 0; i < shown.length; i += 4) {
                const d = Math.max(Math.abs(shown[i] - bare[i]),
                                   Math.abs(shown[i + 1] - bare[i + 1]),
                                   Math.abs(shown[i + 2] - bare[i + 2]));
                if (d > 12) count++;
                if (d > peak) peak = d;
            }
            if (count > seen) seen = count;
            if (peak > top) top = peak;
            g.step(1 / 60);
        }
        out = { had, seen, top, alive };
    } finally {
        // The stubs above are the *instance's* own properties, shadowing the
        // prototype's. Left there they outlive `start()`, and the next chapter
        // measured on this page quietly makes no particles at all.
        delete g.puff; delete g.scuff; delete g.sparkle;
        g.stop();     // `start()` began painting; five chapters of that is load
                      // on every page after this one (#182)
    }
    return out;
}
"""


def particles_seen(page, chapter, source, frames=40):
    """How many pixels one source's burst is worth on one chapter, at its best
    frame: `seen` pixels changed by more than a nudge, `top` the strongest
    channel difference anywhere in the picture."""
    js = (SEEN_PROBE.replace("/*MAKE*/", PARTICLE_SOURCES[source]["make"])
                    .replace("NO_BALLOON", json.dumps(NO_BALLOON)))
    return page.evaluate(js, {"chapter": chapter, "frames": frames})


@pytest.mark.parametrize("source", list(PARTICLE_SOURCES))
def test_every_particle_source_can_actually_be_seen(own_page, source):
    """Its own page: this drives the physics by hand and leaves the engine
    mid-chapter, which the shared desktop page's later tests would inherit.

    Every chapter, because they do not share a palette — dust that shows up on
    Chapter 1's path can be invisible on Chapter 4's.
    """
    seen, skipped = {}, {}
    for chapter in range(5):
        r = particles_seen(own_page, chapter, source)
        if r.get("skipped"):
            skipped[chapter] = r["skipped"]
        else:
            seen[chapter] = r
    # "could not fire it here" is not "there is nothing to see here": the only
    # skip this accepts is a chapter that does not have the thing at all. Left
    # open, a probe that silently stopped working reads as five green chapters.
    unexplained = {c: why for c, why in skipped.items() if why not in ABSENT}
    assert not unexplained, (
        f"the probe could not make {source} happen: {unexplained} — that is a broken "
        "probe, not an invisible burst")
    assert seen, f"{source} never fired on any chapter: {skipped}"
    for chapter, r in sorted(seen.items()):
        # Measured over all five chapters and the full life of each burst, a
        # particle is worth 89 pixels (the smallest: dust on Chapter 1's pale
        # path) to 212 (a takeoff puff on Chapter 5). 40 sits well under every
        # one of them, and is the same bound the single-chapter dust test this
        # grew out of used.
        assert r["seen"] >= 40 * r["had"], (
            f"{source}: {r['had']} particles changed only {r['seen']} pixels on chapter "
            f"{chapter} over {r['alive']} frames — drawn, and not visible against that "
            f"background. Skipped elsewhere: {skipped}")
        # and its strongest pixel by 51/255 at worst (a landing puff on Chapter 3)
        assert r["top"] >= 35, (
            f"{source}: the strongest pixel it changed on chapter {chapter} moved by "
            f"{r['top']}/255 — too faint to read as anything")


def test_every_particle_call_site_is_covered_by_a_visibility_case():
    """The point of the table above is that a new burst cannot be added without
    someone asking whether it can be seen. That only holds if the table is
    checked against the code: this counts the calls to each factory in the
    engine and fails when one of them has no case here."""
    src = (APP / "public" / "js" / "game.js").read_text()
    sites = collections.Counter(re.findall(r"this\.(puff|scuff|sparkle)\(", src))
    cases = collections.Counter(s["factory"] for s in PARTICLE_SOURCES.values())
    assert sites == cases, (
        f"game.js calls {dict(sites)} but PARTICLE_SOURCES covers {dict(cases)} — every "
        "call site that makes particles needs a case saying what it looks like, or a "
        "stated reason it is exempt")


# Draw one character over a state change and report how much the picture moved
# from each frame to the next, as the summed channel difference over the whole
# canvas — with `hard: true` for the same run with the blend switched off, which
# is the defect this is here to catch, measured on the same machine in the same
# browser rather than guessed at as a constant.
#
# The background is filled, not cleared: on a transparent canvas the alpha
# channel of a fading drawing swamps the measurement, and the game has no
# transparent background — a crossfade there really is over the sky.
BLEND_PROBE = """
async ({ id, dt, steps, changeAt, from_, to, hard }) => {
  const s = await import('/js/sprites.js');
  const cv = document.createElement('canvas');
  cv.width = 320; cv.height = 280;
  const c = cv.getContext('2d');
  const pal = { body: '#4a90d9', belly: '#dfe9f3', ear: '#2f6ba8' };
  let state = from_, was = from_, changedAt = -10, changedOn = -1;
  const shots = [];
  for (let i = 0; i < steps; i++) {
    const t = i * dt;
    const want = t >= changeAt ? to : from_;
    // the game's own rule, from game.js: remember what we were and when
    if (want !== state) { was = state; changedAt = t; state = want; changedOn = i; }
    c.fillStyle = '#8ec7e8';
    c.fillRect(0, 0, cv.width, cv.height);
    s.drawCharacter(c, id, 160, 230, 92, pal, t, state, 1,
                    hard ? null : { from: was, k: (t - changedAt) / s.BLEND });
    shots.push(c.getImageData(0, 0, cv.width, cv.height).data);
  }
  const diffs = [];
  for (let i = 1; i < shots.length; i++) {
    const a = shots[i - 1], b = shots[i];
    let n = 0;
    for (let p = 0; p < a.length; p += 4) {
      n += Math.abs(a[p] - b[p]) + Math.abs(a[p + 1] - b[p + 1]) +
           Math.abs(a[p + 2] - b[p + 2]);
    }
    diffs.push(Math.round(n / 1000));
  }
  return { diffs, changedOn, blend: s.BLEND, drawn: window.__art().drawn[id] };
}
"""


@pytest.mark.parametrize("cid,how", [("bluey", "pose"), ("bandit", "rig")],
                         ids=["one drawing to another", "a drawing to the rig"])
def test_a_state_change_is_spread_over_several_frames(desktop, cid, how):
    """Landing used to be one frame: the drawing of a jump on one, the drawing of
    a run on the next, and for everyone but Bluey — who is the only one with a
    jump of her own — a swap from a pose render to the cut-out rig as well.

    This is a bound on the picture rather than on `BLEND`: the same run is
    measured twice, once blended and once with the blend switched off, and the
    worst single frame of the blended one has to be a small share of the snap's.
    Frames differ anyway (a run bobs), so the yardstick is the change *over* the
    largest ordinary frame, and both are measured here rather than written down.
    """
    desktop.wait_for_function(
        "(c) => window.__art().poseFrames.some((f) => f.includes(c + '-run'))",
        arg=cid, timeout=15000)
    dt, steps, at = 1 / 60, 40, 20 / 60
    runs = {}
    for hard in (False, True):
        runs[hard] = desktop.evaluate(BLEND_PROBE, {
            "id": cid, "dt": dt, "steps": steps, "changeAt": at,
            "from_": "run", "to": "jump", "hard": hard})
    blend, snap = runs[False], runs[True]
    assert blend["drawn"] == how, f"{cid} was not drawn as a {how}: {blend['drawn']}"

    # frame n of `diffs` is the change between shot n and shot n+1, so the
    # change carried by the frame the state flipped on is diffs[changedOn - 1]
    cut = blend["changedOn"]
    assert cut > 0, f"the state never changed: {blend}"
    steady = max(blend["diffs"][: cut - 1])   # ordinary running, before any of it
    moved = blend["diffs"][cut - 1:]
    snapped = snap["diffs"][cut - 1:]

    # vacuity: the two states have to *look* different, or there is nothing here
    # to spread out and any blend would pass
    assert max(snapped) > 2.5 * steady, (
        f"snapping from run to jump changed {max(snapped)} against {steady} for an "
        f"ordinary frame — too little for this test to be measuring anything")
    spike = max(moved) - steady
    worst = max(snapped) - steady
    assert spike < 0.35 * worst, (
        f"the worst frame of the blend moved {spike} over an ordinary frame, "
        f"{spike / worst:.0%} of the {worst} a snap moves — the change is still "
        "landing on one frame")

    # ...and it is spread, rather than merely smaller. Over the window the blend
    # is supposed to occupy, count the frames that changed at all — a jump is a
    # still, so a snap has exactly one and then nothing.
    window = round(blend["blend"] / dt) + 2
    floor = 0.05 * worst
    spent = sum(1 for d in moved[:window] if d > floor)
    assert sum(1 for d in snapped[:window] if d > floor) <= 2, (
        f"the unblended run changed on {sum(1 for d in snapped[:window] if d > floor)} "
        "frames, so counting frames cannot tell the two apart here")
    assert spent >= 5, (
        f"only {spent} frames moved after the state flipped; a blend of "
        f"{blend['blend']}s at {1 / dt:.0f}fps should take several")


# Draw the same moment of a crossfade over two very different backgrounds. A
# pixel that comes out the same colour on both is opaque; one that does not is
# letting the background through. Doing it this way needs no knowledge of what
# the character is supposed to look like, which is the point — the artwork is
# free to change.
GHOST_PROBE = """
async ({ id, fade, from_, to }) => {
  const s = await import('/js/sprites.js');
  const W = 320, H = 280;
  const cv = document.createElement('canvas');
  cv.width = W; cv.height = H;
  const c = cv.getContext('2d');
  const pal = { body: '#4a90d9', belly: '#dfe9f3', ear: '#2f6ba8' };
  const shot = (bg, k) => {
    c.fillStyle = bg; c.fillRect(0, 0, W, H);
    s.drawCharacter(c, id, 160, 230, 92, pal, 0.4, to, 1, { from: from_, k });
    return c.getImageData(0, 0, W, H).data;
  };
  const A = '#8ec7e8', B = '#101010';        // sky, and nearly black
  const oldA = shot(A, 0), oldB = shot(B, 0);   // k=0 and k=1 are the two states
  const newA = shot(A, 1), newB = shot(B, 1);   // themselves, drawn whole
  const midA = shot(A, fade), midB = shot(B, fade);
  const solid = (p, q, i) => Math.abs(p[i] - q[i]) <= 2 &&
                             Math.abs(p[i + 1] - q[i + 1]) <= 2 &&
                             Math.abs(p[i + 2] - q[i + 2]) <= 2;
  const both = new Uint8Array(W * H);
  for (let n = 0; n < W * H; n++) {
    both[n] = solid(oldA, oldB, n * 4) && solid(newA, newB, n * 4) ? 1 : 0;
  }
  // Only pixels well inside both drawings are asked about: the two states are
  // in different positions (the motion is mixed too), so the edges legitimately
  // move, and where only one of them covers the pixel it is *meant* to be part
  // way through appearing.
  const R = 6;
  const near = [[R,0],[-R,0],[0,R],[0,-R],[4,4],[-4,4],[4,-4],[-4,-4]];
  let body = 0, ghost = 0;
  for (let y = R; y < H - R; y++) for (let x = R; x < W - R; x++) {
    const n = y * W + x;
    if (!both[n] || !near.every(([dx, dy]) => both[(y + dy) * W + x + dx])) continue;
    body++;
    if (!solid(midA, midB, n * 4)) ghost++;
  }
  return { body, ghost };
}
"""


@pytest.mark.parametrize("cid", ["bluey", "bandit"],
                         ids=["one drawing to another", "a drawing to the rig"])
def test_a_character_does_not_go_see_through_while_it_changes(desktop, cid):
    """The obvious crossfade — the old drawing at `1 - k`, the new one over it at
    `k` — leaves a `k(1 - k)` share of the background showing through wherever
    both cover the same pixel: a quarter of the sky, straight through the middle
    of the dog, half way through every landing. It looks like a bug and it is
    invisible to the test above, which only ever asks how much the picture
    *changed* from one frame to the next: a ghost that fades in and out smoothly
    is smooth.
    """
    desktop.wait_for_function(
        "(c) => window.__art().poseFrames.some((f) => f.includes(c + '-run'))",
        arg=cid, timeout=15000)
    r = desktop.evaluate(GHOST_PROBE, {"id": cid, "fade": 0.5,
                                       "from_": "run", "to": "jump"})
    assert r["body"] > 200, (
        f"only {r['body']} pixels are inside both drawings of {cid}, which is too "
        "little of a character to say anything about")
    assert r["ghost"] == 0, (
        f"{r['ghost']} of {r['body']} pixels inside {cid} changed with the "
        "background half way through the change — the character is translucent")


# A game nobody is playing. The player runs on his own and only jumps when a
# finger says so, so with no input he walks into every gap the chapter has. What
# happens after that is the whole question: a lift back onto the ledge he just
# left is a lift into the same water, and the game used to splash at the first
# pit for as long as anyone watched it (#176).
#
# Nothing is drawn — this is about where he ends up, not how it looks — so a full
# minute of a chapter costs about as much as a single frame does above. The pits
# are counted off the level itself rather than written down here: the horizontal
# projection of the platforms, whose holes are the water he can fall in.
NO_INPUT_RUN = """
({ chapter, seconds, dt }) => {
  const g = window.game;
  document.getElementById('overlay').classList.add('hidden');
  g.start(chapter);
  g.toast = () => {};
  const at = [];
  let seen = g.splashes;
  for (let i = 0; i < Math.round(seconds / dt) && g.mode === 'playing'; i++) {
    g.step(dt);
    if (g.splashes > seen) { seen = g.splashes; at.push(Math.round(g.player.x)); }
  }
  const out = { at, splashes: g.splashes, mode: g.mode,
                x: Math.round(g.player.x), length: g.ch.length };
  g.stop();   // five chapters played to the end otherwise leaves five results
              // screens animating on this page, and every other page slows down
  const spans = g.level.plats.map((s) => [s.x, s.x + s.w]).sort((a, b) => a[0] - b[0]);
  let reach = null, pits = 0;
  for (const [a, b] of spans) {
    if (reach !== null && a > reach) pits++;
    reach = reach === null ? b : Math.max(reach, b);
  }
  return { ...out, pits };
}
"""


def test_a_game_left_alone_does_not_drown_at_the_same_pit(own_page):
    """One splash per pit at most, and never the same pit twice.

    Where a splash puts him down is the only thing that decides this, and it is
    checked by playing rather than by reading `recoverySpot`: each splash has to
    be further along the chapter than the last, which is only true if he was
    lifted past the water instead of back behind it. Before #176 chapter 2
    answered with the same handful of pixels over and over.

    All five chapters on one page rather than a page each: a minute of physics
    costs about a second here, and every extra browser context slows the pages
    the rest of the file is sharing. Its own page all the same, because it
    leaves the engine wherever the last level ended.
    """
    runs = {c: own_page.evaluate(NO_INPUT_RUN, {"chapter": c, "seconds": 60, "dt": 1 / 60})
            for c in range(5)}
    assert len(runs) == 5, f"only {len(runs)} chapters were driven"
    for chapter, r in sorted(runs.items()):
        at, pits = r["at"], r["pits"]
        assert r["splashes"] == len(at), (
            f"chapter {chapter}: {r['splashes']} splashes but {len(at)} were seen "
            "— the run missed some")
        assert at, (
            f"chapter {chapter} never dropped an untouched player in the water in "
            f"60s ({pits} pits, ended at x={r['x']}) — nothing here was measured")
        apart = [b - a for a, b in zip(at, at[1:])]
        assert all(d > 100 for d in apart), (
            f"chapter {chapter} splashed at {at}: a splash within 100px of the one "
            "before it is the same pit again, which no waiting ever gets past")
        assert len(at) <= pits, (
            f"chapter {chapter} splashed {len(at)} times over {pits} pits")
        # and the whole point of the lift: left alone, it gets to the end
        assert r["mode"] == "finished", (
            f"chapter {chapter} was still playing after 60s at x={r['x']} of "
            f"{r['length']}, having splashed {len(at)} times at {at}")


# Every x a chapter can drop him at, asked of the real `recoverySpot`. A play
# finds five or six pits; this walks the whole course a pixel at a time, which is
# what it takes to say the answer is never "there is no ground ahead" — the case
# `recoverySpot` used to carry a `lastSafe` field for (#184).
GROUND_AHEAD_SWEEP = """
async ({ step }) => {
  const c = await import('/js/chapters.js');
  const m = await import('/js/game.js');
  const g = window.game;
  const out = [];
  for (let i = 0; i < c.CHAPTERS.length; i++) {
    g.start(i);
    const ch = c.CHAPTERS[i], plats = g.level.plats;
    // from where start() puts him to the line step() ends the chapter on: every
    // x he can be at, so every x a fall can be reported from
    const from = g.player.x;
    let checked = 0, bad = null;
    for (let x = from; x <= ch.length && !bad; x += step) {
      checked++;
      let spot = null, err = null;
      try { spot = g.recoverySpot(x); } catch (e) { err = String(e); }
      // ground he can stand on, that reaches far enough past the fall to hold him
      const on = spot && plats.find((s) => s.y === spot.y && spot.x >= s.x &&
                                    spot.x <= s.x + s.w && s.x + s.w >= x + m.PLAYER_W);
      if (!on) bad = { x, spot, err };
    }
    out.push({ id: ch.id, length: ch.length, plats: plats.length, from, checked, bad,
               end: Math.max(...plats.map((s) => s.x + s.w)) });
  }
  g.stop();   // nothing here draws, but `start()` set the loop painting (#182)
  return out;
}
"""


def test_every_fall_a_chapter_can_produce_has_ground_ahead_of_it(own_page):
    """`recoverySpot` always answers with a ledge, at every x of every chapter.

    It used to have a `if (!next) return this.lastSafe` fallback for "nothing
    ahead", and a `lastSafe` field updated on every single landing to feed it.
    Nothing could reach it — and had anything reached it, it would have put him
    down behind the water he fell into, which is the drown-forever bug of #176.
    So it is gone, and this is what holds it up: the invariant is that a chapter's
    ground outruns its finish line, checked over the whole course rather than
    over the handful of pits a play happens to find.

    Its own page: it restarts the engine on each chapter in turn.
    """
    step = 1
    rows = own_page.evaluate(GROUND_AHEAD_SWEEP, {"step": step})
    assert len(rows) == 5, f"only {len(rows)} chapters were swept"
    for r in rows:
        assert r["bad"] is None, (
            f"{r['id']}: a fall at x={r['bad'] and r['bad']['x']} of {r['length']} has "
            f"no ground ahead of it — recoverySpot answered {r['bad'] and r['bad']['spot']}"
            f"{' / ' + r['bad']['err'] if r['bad'] and r['bad']['err'] else ''}")
        want = (r["length"] - r["from"]) // step + 1
        assert r["checked"] == want, (
            f"{r['id']}: {r['checked']} of {want} positions were tried")
        assert r["end"] > r["length"], (
            f"{r['id']}: its ground stops at {r['end']}, short of the {r['length']} "
            "finish line — a fall near the end would have nowhere to be put down")


# Drown the player and watch the whole screen, frame by frame: the camera
# follows the player exactly, so the teleport to `recoverySpot` moves the sky and
# both parallax layers at once.
#
# Nobody decides here how far the player is moved — the probe lets the chapter
# drown him on its own and then goes looking at the frames either side of it, so
# the distance under test is one the game really produces rather than one chosen
# to suit the answer. It is a lift *forward* over the water since #176, which is
# why the measurements below are on the size of the jump and not its direction.
# The fall is found first without drawing, since the frame it happens on is what
# says which frames are worth the cost of reading back.
#
# The same fall is then played twice — once eased, once with the slack thrown
# away every step, which is the cut this is here to catch — with every random
# source of movement taken away, so the two runs are the same picture apart from
# where the camera is standing. That is what makes the third measurement
# possible: how far apart the two runs' pictures are on a frame is the camera's
# lag, and it has to be paid off over several frames rather than in one. The
# picture is sampled through a small canvas rather than read at full size:
# 160x100 is a low-pass filter, and reading back at device resolution is slow.
RESPAWN_PROBE = """
async ({ dt, chapter, before, after, tail, hunt, hurl }) => {
  const small = document.createElement('canvas');
  small.width = 160; small.height = 100;
  const sc = small.getContext('2d');
  const g = window.game;
  document.getElementById('overlay').classList.add('hidden');

  const begin = () => {
    g.start(chapter);
    g.level.tokens = [];
    g.level.secret.taken = true;
    g.balloon = null;
    g.toast = () => {};          // the banner is screen-space, and not the subject
    g.scuff = () => {};          // dust is random: it would differ between the runs
    g.puff = () => {};
    // a fall further than any chapter really produces, for the cap: the game's
    // own recovery is a short hop forward, so the long throw has to be asked for
    if (hurl) g.recoverySpot = (x) => ({ x: x - hurl, y: g.level.plats[0].y });
  };
  // The splash itself, rather than a guess from the player's x: `splashes` is
  // incremented by the water and by nothing else, so a stumble into a bush
  // cannot be mistaken for one and the direction of the lift does not matter.
  const fall = (i) => {
    const was = g.splashes;
    g.step(dt);
    return g.splashes > was ? i : -1;
  };

  begin();                       // no drawing: just find the frame he goes in on
  let at = -1;
  for (let i = 0; i < hunt && at < 0; i++) at = fall(i);
  if (at < 0) return { fellOn: -1 };
  const from = at - before, to = at + after;

  // Frames are drawn and read back only around the splash; the camera is
  // recorded for a good while longer, since the slack has to be seen running out
  // and that costs nothing to watch.
  const play = (hard) => {
    begin();
    const shots = [], cams = [], onScreen = [], gaps = [];
    for (let i = 0; i < to + tail; i++) {
      // one dunk only: this chapter walks the player straight back into the same
      // pit, and the camera has to be watched until it has spent all its slack
      if (i > at) { g.player.onGround = true; g.player.vy = 0; }
      const target = g.camTarget();
      if (fall(i) >= 0) gaps.push(Math.round(target - g.camTarget()));
      if (hard) g.camSlack = 0;
      if (i < from) continue;
      cams.push(Math.round(g.camAt()));
      onScreen.push(Math.round(g.player.x - g.camAt()));
      if (i >= to) continue;
      g.render();
      sc.drawImage(g.canvas, 0, 0, small.width, small.height);
      shots.push(sc.getImageData(0, 0, small.width, small.height).data);
    }
    return { shots, cams, onScreen, gaps };
  };

  const ink = (a, b) => {
    let n = 0;
    for (let p = 0; p < a.length; p += 4) {
      n += Math.abs(a[p] - b[p]) + Math.abs(a[p + 1] - b[p + 1]) +
           Math.abs(a[p + 2] - b[p + 2]);
    }
    return Math.round(n / 100);
  };
  const frameToFrame = (r) => r.shots.slice(1).map((s, i) => ink(r.shots[i], s));

  const eased = play(false), snap = play(true);
  const m = await import('/js/game.js');
  const c = await import('/js/chapters.js');
  // the stubs are instance properties: they shadow the prototype and would
  // outlive this probe, `start()` included
  delete g.toast; delete g.scuff; delete g.puff; delete g.recoverySpot;
  g.stop();   // `begin()` starts a chapter twice over, and each one starts the
              // loop painting; left running it is load on every page after (#182)
  return {
    moved: frameToFrame(eased), snapped: frameToFrame(snap),
    apart: eased.shots.map((s, i) => ink(s, snap.shots[i])),
    fellOn: before, gaps: eased.gaps, sameGaps: `${eased.gaps}` === `${snap.gaps}`,
    cams: eased.cams, hardCams: snap.cams, onScreen: eased.onScreen,
    blend: m.CAM_BLEND, slack: m.CAM_SLACK, world: c.WORLD_W,
  };
}
"""


def test_the_camera_catches_up_after_a_respawn_instead_of_cutting(own_page):
    """A splash teleports the player onto the ledge past the water. The camera
    follows the player exactly, so the whole background — sky, far layer, mid
    layer and the world itself — used to move a hundred-odd pixels between two
    frames: a cut, in the one moment of the game that is meant to be a friendly
    lift back up.

    Its own page: this drives the physics by hand and leaves the engine mid-
    chapter. Like the state-change test above this is a bound on the picture, and
    both sides of it are measured here rather than written down.
    """
    dt = 1 / 60
    r = own_page.evaluate(RESPAWN_PROBE, {"dt": dt, "chapter": 0, "before": 24,
                                          "after": 40, "tail": 150, "hunt": 60 * 30,
                                          "hurl": 0})
    cut = r["fellOn"]
    assert cut > 0, "the player never fell in the water, so nothing here ran"
    assert r["sameGaps"] and len(r["gaps"]) == 1, (
        f"the two runs did not play the same single fall: {r['gaps']}")
    assert r["apart"][cut - 1] == 0, (
        "the two runs had already drawn different pictures before the splash, so "
        "nothing below is about the camera")
    thrown = max(abs(g) for g in r["gaps"])
    assert thrown < r["slack"], (
        f"the chapter moved the player {thrown}px, past the {r['slack']}px cap on "
        "the slack — the cap, not the ease, is what this run would be measuring")

    # the frames the player spent falling move the picture as much as anything
    # here, so "ordinary" is taken from the running before he ever left the ledge
    steady = max(r["moved"][:cut // 2])
    moved, snapped = r["moved"][cut - 1:], r["snapped"][cut - 1:]

    # vacuity: the teleport has to move the picture, or there is nothing to ease
    assert max(snapped) > 2.5 * steady, (
        f"the cut moved {max(snapped)} against {steady} for an ordinary frame — too "
        "little for this test to be measuring anything")
    spike = max(moved) - steady
    worst = max(snapped) - steady
    assert spike < 0.35 * worst, (
        f"the worst frame after the splash moved {spike} over an ordinary frame, "
        f"{spike / worst:.0%} of the {worst} the cut moves — the camera is still "
        "jumping on one frame")

    # ...and spread rather than merely smaller. The camera moves on every frame
    # anyway, so a frame carrying catch-up is one where the eased picture is
    # still somewhere the cut run had already left: measured between the two runs
    # rather than against a level of movement thought up here.
    window = round(r["blend"] / dt) + 2
    lag = r["apart"][cut:]
    spent = sum(1 for d in lag[:window] if d > 0.05 * max(lag))
    assert spent >= 5, (
        f"the two runs were apart on only {spent} frames ({lag[:8]}); an ease of "
        f"{r['blend']}s at {1 / dt:.0f}fps should pay the teleport out over several")

    # the camera ends up where it always would have: slack is a detour, not an offset
    assert r["cams"][-1] == r["hardCams"][-1], (
        f"the eased camera settled at {r['cams'][-1]} and the hard one at "
        f"{r['hardCams'][-1]} — the slack never ran out")
    assert lag[-1] < 0.25 * max(lag), (
        f"the two pictures were still {lag[-1]} apart at the end of the window "
        f"against {max(lag)} at the splash — the camera is holding its lag, not "
        "spending it")
    # and while it is catching up the player it is lagging behind stays visible.
    # Both edges: the lift is forward now, so the camera trails him to the right.
    assert min(r["onScreen"]) > 40, (
        f"the player was drawn at x={min(r['onScreen'])} while the camera caught "
        f"up — CAM_SLACK ({r['slack']}) has to keep them on screen")
    assert max(r["onScreen"]) < r["world"] - 60, (
        f"the player was drawn at x={max(r['onScreen'])} of {r['world']} while the "
        "camera caught up with a lift forward over the water")


def test_a_camera_catching_up_never_leaves_the_player_off_the_screen(own_page):
    """The player does not move on screen while the camera is lagging behind — he
    *is* the lag — so a long enough teleport would slide him off the left edge
    and hold him there for a third of a second, which is worse than the cut.

    No chapter moves him more than a couple of hundred pixels today, well inside
    the cap, so this drives a teleport far past anything the game produces: the
    protection is otherwise the one piece of this that never runs. Backwards,
    because that is the direction that can strand him off the left edge.
    """
    hurl = 700
    r = own_page.evaluate(RESPAWN_PROBE, {"dt": 1 / 60, "chapter": 0, "before": 4,
                                          "after": 8, "tail": 150, "hunt": 60 * 30,
                                          "hurl": hurl})
    assert max(r["gaps"]) > r["slack"], (
        f"the forced fall was only {max(r['gaps'])}px, inside the {r['slack']}px cap "
        "— this run never reached the thing it is testing")
    assert min(r["onScreen"]) > 40, (
        f"the player was drawn at x={min(r['onScreen'])} while the camera caught up "
        f"from a {max(r['gaps'])}px teleport")
    # vacuity the other way: the cap must not have swallowed the ease entirely
    assert min(r["onScreen"]) < 300 - r["slack"] / 2, (
        f"the camera only ever fell {300 - min(r['onScreen'])}px behind a "
        f"{max(r['gaps'])}px teleport — it is cutting, not easing")


# --- the scenery: where the mid layer's props stand -------------------------

# A prop in the mid parallax layer has no fixed world x — the layer scrolls at
# 0.6, so which platform happens to be under a tree depends on where the camera
# is standing, and "the ground beneath it" is not a thing that exists. What does
# exist is the chapter's horizon: the line its far picture puts a surface on.
# This measures every prop against that surface *in the pixels*, rather than
# against the number in the file, over all five chapters.
#
# Per prop, in the chapter's own background with the props taken out of it:
#   clear    the rows just above the foot are sky, so it stands *on* the surface
#            rather than in it — the beach palm used to be 120px under the sea
#   edge     the nearest row where sky turns into something solid, i.e. the
#            surface: a foot far from every edge is standing in mid air, which
#            is what sleepytime's trees did in a dream sky with no ground at all
#   painted  the lowest row the prop really paints in that column, so a prop
#            that is not in the picture cannot pass by having nothing to measure
#
# The props are taken out by the chapter's own rule — `horizon: null` ships none
# — so the bare picture is one the game can really draw. Each prop is measured
# under the camera that brings it on screen, at a fixed t so the sea's sparkle
# and the clouds sit still, and `col` is where the 0.6 layer puts it on screen.
SCENERY_PROBE = r"""
async ({ clearRows }) => {
  const c = await import('/js/chapters.js');
  const g = window.game;
  const W = c.WORLD_W, H = c.WORLD_H;
  const mk = () => { const cv = document.createElement('canvas');
                     cv.width = W; cv.height = H; return cv.getContext('2d'); };
  const withCtx = mk(), bareCtx = mk(), skyCtx = mk();
  const strip = (ctx, col) => ctx.getImageData(col, 0, 1, H).data;
  const same = (a, b, i) => Math.abs(a[i] - b[i]) <= 6 && Math.abs(a[i + 1] - b[i + 1]) <= 6
                         && Math.abs(a[i + 2] - b[i + 2]) <= 6;
  // the picture as it would be with nothing standing in it, painted by the
  // chapter's own two sky colours the same way renderBackground lays them down
  const bareAt = (ch, camX, col) => {
    const gr = skyCtx.createLinearGradient(0, 0, 0, c.GROUND_Y);
    gr.addColorStop(0, ch.sky[0]); gr.addColorStop(1, ch.sky[1]);
    skyCtx.fillStyle = gr; skyCtx.fillRect(0, 0, W, H);
    const held = ch.horizon; ch.horizon = null;
    bareCtx.clearRect(0, 0, W, H); g.ch = ch; g.t = 0; g.renderBackground(bareCtx, camX);
    ch.horizon = held;
    const bare = strip(bareCtx, col), sky = strip(skyCtx, col), isSky = [];
    for (let y = 0; y < H; y++) isSky.push(same(bare, sky, y * 4));
    return { bare, isSky };
  };
  const look = (ch, base, camX, col) => {
    const { isSky } = bareAt(ch, camX, col);
    let clear = true, edge = null;
    for (let y = Math.max(0, base - clearRows); y < base; y++) if (!isSky[y]) clear = false;
    for (let y = 1; y < H; y++) {
      if (isSky[y] === isSky[y - 1]) continue;
      if (edge === null || Math.abs(y - base) < Math.abs(edge - base)) edge = y;
    }
    return { base, clear, edge, delta: edge === null ? null : edge - base };
  };
  const held = g.ch;
  try {
    const chapters = [];
    for (const ch of c.CHAPTERS) {
      const props = c.sceneryFor(ch), rows = [];
      for (const p of props) {
        const camX = Math.max(0, (p.x - W / 2) / 0.6);
        const col = Math.round(p.x - camX * 0.6);
        withCtx.clearRect(0, 0, W, H); g.ch = ch; g.t = 0;
        g.renderBackground(withCtx, camX);
        const wit = strip(withCtx, col), { bare } = bareAt(ch, camX, col);
        let painted = null;
        for (let y = 0; y < H; y++) if (!same(wit, bare, y * 4)) painted = y;
        rows.push(Object.assign({ x: p.x, kind: p.kind, base: p.y, col, painted },
                                look(ch, p.y, camX, col)));
      }
      chapters.push({ id: ch.id, horizon: ch.horizon, props: props.length,
                      checked: rows.length, rows });
    }
    // The two defects of #210, asked of the same pictures: a foot on the ground
    // line of a beach whose shore is 120px higher, and one on the ground line of
    // a chapter that paints no ground. Nothing is drawn for these — the question
    // is only what the picture holds at that y, which is the whole question.
    const of = (id) => c.CHAPTERS.find((x) => x.id === id);
    return { chapters, defects: {
      submerged: look(of('beach'), c.GROUND_Y, 300, 640),
      midair: look(of('sleepytime'), c.GROUND_Y, 300, 640),
    } };
  } finally {
    g.ch = held;
  }
}
"""

# How far a foot may sit from the surface it stands on. The measured slack is
# 10px — the backyard and creek hills bottom out at GROUND_Y-10 while their
# trees stand on GROUND_Y — and the beach's palms are exact.
SURFACE_TOL = 12
# and how much clear sky a foot needs above it. The same hills leave 10, so
# there is no room here for more; what this has to catch is a foot under 120px
# of sea, which it clears by twenty times over.
CLEAR_ROWS = 6


def scenery_complaint(row):
    """Why this foot is not standing on a surface, or None if it is.

    One function for the real props and for the two defects of #210 below: a
    rule that has quietly stopped meaning anything lets the defects through
    here first, where it is a failure, rather than in the picture.
    """
    if not row["clear"]:
        return (f"it is in the surface rather than on it — the {CLEAR_ROWS} rows above "
                f"y={row['base']} are not sky")
    if row["edge"] is None:
        return f"it is in mid air — nothing in its column at y={row['base']} is anything but sky"
    if abs(row["delta"]) > SURFACE_TOL:
        return (f"it is {abs(row['delta'])}px off the surface: its foot is at y={row['base']} "
                f"and the nearest edge in its column is y={row['edge']}")
    return None


def test_every_prop_stands_on_the_surface_its_chapter_paints(own_page):
    """Chapter 4 had a palm tree planted 120px under the sea and chapter 5 had
    tree trunks hanging in a dream sky, because both stood on a ground line the
    picture behind them does not have (#210). Every prop of every chapter is
    measured here against the surface its own background paints.

    Its own page: it borrows the engine to draw backgrounds off screen.
    """
    r = own_page.evaluate(SCENERY_PROBE, {"clearRows": CLEAR_ROWS})
    chapters = r["chapters"]
    assert len(chapters) == 5, f"only {len(chapters)} chapters were measured"

    for ch in chapters:
        assert ch["checked"] == ch["props"], (
            f"{ch['id']}: {ch['checked']} of {ch['props']} props were measured")
        for p in ch["rows"]:
            why = scenery_complaint(p)
            assert why is None, f"{ch['id']}: the {p['kind']} at x={p['x']} — {why}"
            assert p["painted"] is not None, (
                f"{ch['id']}: the {p['kind']} at x={p['x']} paints nothing in its own "
                f"column {p['col']} — this prop was not measured, it was missed")
            assert -2 <= p["painted"] - p["base"] <= 10, (
                f"{ch['id']}: the {p['kind']} at x={p['x']} stands on y={p['base']} but "
                f"paints down to y={p['painted']} — the art is not on its own foot")

    # what all that measured: a sceneryFor that returned nothing would pass every
    # line above without ever looking at a prop
    total = sum(c["checked"] for c in chapters)
    dressed = [c["id"] for c in chapters if c["checked"]]
    assert total >= 30 and len(dressed) >= 3, (
        f"only {total} props over {dressed} — there is not enough scenery here for "
        "this to have tested anything")
    bare = sorted(c["id"] for c in chapters if not c["checked"])
    assert bare == ["hammerbarn", "sleepytime"], (
        f"{bare} ship no scenery at all — hammerbarn's horizon is a shop floor and "
        "sleepytime's sky has no surface, but a chapter that has quietly lost its "
        "trees is a regression, not a decision")
    sleepy = next(c for c in chapters if c["id"] == "sleepytime")
    assert sleepy["horizon"] is None, (
        "sleepytime is a dream sky with no ground in it — declaring a horizon there "
        f"(y={sleepy['horizon']}) is what put trunks through the platforms")

    # the same rule, run against the two defects it was written for: both were in
    # the shipped picture, and both have to come back as complaints
    for name, want in [("submerged", "in the surface"), ("midair", "in mid air")]:
        why = scenery_complaint(r["defects"][name])
        assert why and want in why, (
            f"a foot placed the way #210's {name} prop was placed came back as "
            f"{why!r} — this check no longer catches the thing it is here for")


def test_no_console_errors_on_desktop(desktop):
    """Last, so it covers everything the tests above did."""
    assert not desktop.errors, str(desktop.errors[:3])


# --- phones: the only way it is really played -------------------------------

def test_the_menu_fits_without_scrolling(phone):
    assert phone.evaluate("document.body.scrollHeight <= window.innerHeight + 2")
    assert phone.locator("#rotate-hint").is_visible()


@pytest.mark.leaves_a_game_running(reason="test_a_tap_jumps_on_touch taps the player "
                                          "this starts, and stops it afterwards")
def test_it_plays_on_touch(phone):
    phone.click("#btn-play")
    phone.wait_for_selector("#btn-go")
    phone.click("#btn-go")
    phone.wait_for_timeout(400)
    assert phone.evaluate("window.game.mode") == "playing"
    assert phone.evaluate("window.game && !!document.querySelector('canvas')")


# Counted in the browser, over its own clock: a frame is a `requestAnimationFrame`
# callback, which is the same thing the game gets to move on.
FRAME_RATE = """
() => new Promise((done) => {
  let n = 0;
  const t0 = performance.now();
  const tick = () => {
    n++;
    const ms = performance.now() - t0;
    if (ms < 700) requestAnimationFrame(tick);
    else done(Math.round((n * 1000) / ms));
  };
  requestAnimationFrame(tick);
})
"""
PLAYABLE_FPS = 20


@pytest.mark.leaves_a_game_running(reason="it measures the chapter test_it_plays_on_touch "
                                          "started, and hands it on to the tap test")
def test_the_phone_gets_enough_frames_to_be_played(phone):
    """Above the tap test on purpose, because it is the same fault said plainly.

    A page starved of frames fails as a jump that did not happen — the player's y
    is unchanged and nothing in the message suggests the cause is elsewhere. That
    is what #182 cost: five abandoned pages animating results screens took this
    one to about three frames a second.

    The floor is a playability floor, not a benchmark. This page is backgrounded
    (the later tests took the foreground) and the browser throttles it on
    purpose, and it still measures 44-63 here — so 20 is what a *starved* page
    looks like, not what a busy machine does.
    """
    fps = phone.evaluate(FRAME_RATE)
    assert fps >= PLAYABLE_FPS, (
        f"{fps}fps on {phone.viewport_size['width']}px: this page is not getting "
        f"frames. Something else in this run is probably still animating — an "
        f"`own_page` that walked away from a running game loop is the one that has "
        f"happened (#182).")


def test_a_tap_jumps_on_touch(phone):
    """A tap gets him off the ground within the height of a jump.

    The state around it is reported because "y did not change" has more than one
    cause and only one of them is a broken jump: a page the browser has decided
    is hidden pauses the game, and then nothing moves at all.

    Waited for rather than slept through, for the same reason. A jump is a
    number of *frames*, and this page has been in the background since the
    desktop tests took the foreground — its rAF is throttled, so 220ms of wall
    clock was 12 frames on a quiet run and 2 on a busy one. The wait ends the
    moment he is off the ground, so the common case is still ~200ms.
    """
    vp = phone.viewport_size
    before = phone.evaluate("window.game.player.y")
    phone.touchscreen.tap(vp["width"] / 2, vp["height"] * 0.7)
    with contextlib.suppress(PlaywrightTimeout):
        phone.wait_for_function("(y0) => window.game.player.y < y0", arg=before, timeout=3000)
    after = phone.evaluate(
        "({ y: window.game.player.y, mode: window.game.mode, "
        " paused: window.game.paused, hidden: document.hidden, "
        " x: Math.round(window.game.player.x), t: window.game.t.toFixed(2) })")
    assert after["y"] < before, f"tapped at y={before}, then {after}"
    # last of the phone chain: nothing after this taps, so the loop stops here
    phone.evaluate("() => window.game.stop()")


def test_the_canvas_fills_the_viewport(phone):
    vp = phone.viewport_size
    width = phone.evaluate("document.getElementById('game').clientWidth")
    assert width >= vp["width"] - 2, f"{width} < {vp['width']}"


def test_no_console_errors_on_touch(phone):
    assert not phone.errors, str(phone.errors[:3])


# --- the suite's own guard --------------------------------------------------
# `own_page` fails a test that walks away from a running game loop (#182). That
# is a claim about pytest's report, not about the game, so it is asked the only
# way it can honestly be answered: run a test that leaks and read the verdict.

DRILLS = Path(__file__).resolve().parent / "drills"


def run_drill(base_url, name):
    """Run one drill file in a pytest of its own and hand back what it printed."""
    run = subprocess.run(
        [sys.executable, "-m", "pytest", str(DRILLS / name), "-q", "--base-url", base_url],
        cwd=APP, capture_output=True, text=True, timeout=300)
    return run.returncode, run.stdout + run.stderr


def test_a_leaked_game_loop_fails_the_test_that_left_it(base_url):
    """The failure it is here to prevent lands somewhere else entirely: five
    results screens animating on abandoned pages dropped the phone page to about
    three frames a second, and what failed was a tap test two hundred lines away,
    reporting a player who had not moved (#182).

    A subprocess, because the guard is a *teardown*: it cannot fail the test it
    is guarding from inside the same run. Its own browser too — this is the whole
    machinery, conftest included, not a hand-assembled copy of it.
    """
    code, out = run_drill(base_url, "leaks_on_purpose.py")
    assert code != 0, f"a leaked game loop was reported as a clean run:\n{out}"
    # pytest calls a teardown failure an ERROR and still prints the test as
    # passed, so the exit code above is the signal — and the line has to name
    # both the test and the leak, or the reader is back to guessing
    named = [ln for ln in out.splitlines() if "leaks_on_purpose.py::" in ln
             and "left the game loop running" in ln]
    assert named, f"nothing named the test that leaked:\n{out}"
    assert "g.stop()" in out, f"the message does not say what to do about it:\n{out}"


def test_a_probe_that_puts_the_loop_down_is_left_alone(base_url):
    """The direction that decides whether the guard survives contact with people.

    A leak check that also fires on the tests doing it right is noise, and noise
    gets switched off — so the correct probe, one line different from the drill
    above, has to come back green.
    """
    code, out = run_drill(base_url, "stops_the_game.py")
    assert code == 0, f"stopping the loop was reported as a leak anyway:\n{out}"
    assert "1 passed" in out, f"the drill did not run:\n{out}"


def test_a_leak_on_a_shared_page_is_blamed_once_and_put_down(base_url):
    """The case `own_page` cannot cover, and the one that actually happened.

    `own_page` closes its page at teardown, so a loop left there dies with it.
    `desktop` and the phones are opened once and used by every test after —
    that is where a walked-away loop keeps painting for the rest of the session
    (#182), and the autouse guard is what watches them.

    Four claims in one run, because they only mean anything together: the leaker
    is named; the test *after* it comes back clean, because the guard stops the
    loop before failing rather than re-reporting it on everything downstream; a
    declared handoff is not reported at all, which is what stops the marker being
    a nuisance; and a declaration with no reason is refused, which is what stops
    the marker being a way to wave leaks through.
    """
    code, out = run_drill(base_url, "leaks_on_a_shared_page.py")
    assert code != 0, f"a game left running on a shared page passed:\n{out}"
    blamed = [ln for ln in out.splitlines() if "left the game loop running" in ln]
    assert len(blamed) == 1 and "walks_away_from_a_running_game" in blamed[0], (
        f"the leak was not blamed on exactly the test that left it:\n{out}")
    unexplained = [ln for ln in out.splitlines() if "with no reason" in ln]
    assert len(unexplained) == 1 and "no_reason_is_refused" in unexplained[0], (
        f"an exemption with no reason was accepted:\n{out}")
    # a teardown failure is an ERROR and the test still prints as passed, so the
    # count to read is the errors: the leaker and the empty exemption, and
    # nothing for the test that declared its handoff or the one that followed
    assert "4 passed, 2 errors" in out, f"the blame did not land where it should:\n{out}"
