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
import json
import math
import re
import urllib.request
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent

# The game's name is authored once, in fetch_assets.py. `--check` proves the
# static copies agree with it; this suite is the only thing that can say the
# *rendered* page does, which is the copy anyone actually reads.
GAME_NAME = re.search(r'^GAME_NAME = "(.*)"$',
                      (APP / "scripts" / "fetch_assets.py").read_text(), re.M).group(1)

DESKTOP = {"width": 1280, "height": 800}
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


@pytest.fixture(scope="module")
def desktop(make_page):
    return make_page(DESKTOP)


@pytest.fixture(scope="module", params=[IPHONE, PIXEL], ids=["iphone", "pixel"])
def phone(request, make_page):
    return make_page(request.param, touch=True)


@pytest.fixture
def own_page(make_page):
    """A page of this test's own, for the tests that must not inherit a screen.

    Everything else here shares ``desktop`` on purpose. Two things cannot: a page
    whose artwork is blocked (the route would break every test after it) and
    anything asserting a sprite has *not* been drawn yet, since by the end of the
    file the gallery has drawn all twenty-five.
    """
    return make_page(DESKTOP)


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
    flop = rig_diff(desktop, cid, "run", [0.2064, 0.2064], "ears", 0.2, nudge=("ears", 0.15, 0))
    rest = rig_diff(desktop, cid, "run", [0.0636, 0.0636], "ears", 0.2, nudge=("ears", 0.15, 0))
    assert flop["drawn"] == "rig", flop
    # not zero: the cut edge lands on different subpixels when the pivot moves,
    # which is single digits of antialiasing next to hundreds for a real swing
    assert rest["inside"] + rest["outside"] < 10, f"rotated at rest? {rest}"
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


# --- the footfalls: what makes a still read as a run ------------------------

FOOTFALL = """
async ({ steps }) => {
  const s = await import('/js/sprites.js');
  let t = 0, hits = 0;
  for (const dt of steps) { if (s.footfall(t, t + dt)) hits++; t += dt; }
  return { hits, stride: s.STRIDE, t };
}
"""


@pytest.mark.parametrize("dt,ids", [(1 / 240, "240fps"), (1 / 60, "60fps"), (1 / 12, "12fps")],
                         ids=["240fps", "60fps", "12fps"])
def test_the_same_footfalls_are_reported_at_every_frame_rate(desktop, dt, ids):
    """Contacts are a property of the clock, not of how often it is read. A
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
            const { STRIDE } = await import('/js/sprites.js');
            const g = window.game;
            document.getElementById('overlay').classList.add('hidden');
            g.start(0);
            g.level.tokens = [];
            g.level.secret.taken = true;
            g.balloon = null;
            g.particles.length = 0;
            const from = g.t;
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
            return { bursts, from, to: g.t, stride: STRIDE, state: g.player.state };
        }"""
    )
    assert r["state"] == "run", f"the player was not running: {r}"
    beat = math.pi / r["stride"]
    want = math.floor(r["to"] / beat) - math.floor(r["from"] / beat)
    assert r["bursts"] == want, (
        f"{r['bursts']} dust puffs over {r['to'] - r['from']:.2f}s of running, expected "
        f"{want} — one per footfall, and no step may claim a contact its neighbour "
        "already reported")


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
    }
    return out;
}
"""


def particles_seen(page, chapter, source, frames=8):
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
        # Measured over all five chapters, a particle is worth 49 pixels (the
        # smallest: dust on Chapter 1's pale path) to 178 (a landing puff on
        # Chapter 5). 40 sits under every one of them, and is the same bound the
        # single-chapter dust test this grew out of used.
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


# Drown the player and watch the whole screen, frame by frame: the camera
# follows the player exactly, so the teleport back to `lastSafe` moves the sky
# and both parallax layers at once.
#
# Nobody decides here how far the player is thrown back — the probe lets the
# chapter drown him on its own and then goes looking at the frames either side of
# it, so the distance under test is one the game really produces (~130px in every
# chapter) rather than one chosen to suit the answer. The fall is found first
# without drawing, since the frame it happens on is what says which frames are
# worth the cost of reading back.
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
  };
  // The teleport, told apart from the other thing that moves x backwards: a
  // stumble into an obstacle knocks the player back a few pixels, and the run
  // asserts below that what it found is the size of a real fall, not a bump.
  const fall = (i) => {
    const wasX = g.player.x;
    g.step(dt);
    return g.player.x < wasX - 50 ? i : -1;
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
      // a fall further back than any chapter really produces, for the cap
      if (hurl && i === at) g.lastSafe = { x: g.player.x - hurl, y: g.lastSafe.y };
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
  return {
    moved: frameToFrame(eased), snapped: frameToFrame(snap),
    apart: eased.shots.map((s, i) => ink(s, snap.shots[i])),
    fellOn: before, gaps: eased.gaps, sameGaps: `${eased.gaps}` === `${snap.gaps}`,
    cams: eased.cams, hardCams: snap.cams, onScreen: eased.onScreen,
    blend: m.CAM_BLEND, slack: m.CAM_SLACK,
  };
}
"""


def test_the_camera_catches_up_after_a_respawn_instead_of_cutting(own_page):
    """A splash teleports the player back to the last safe ledge. The camera
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
    assert max(r["gaps"]) < r["slack"], (
        f"the chapter threw the player {max(r['gaps'])}px back, past the "
        f"{r['slack']}px cap on the slack — the cap, not the ease, is what this run "
        "would be measuring")

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
    # and while it is catching up the player it is lagging behind stays visible
    assert min(r["onScreen"]) > 40, (
        f"the player was drawn at x={min(r['onScreen'])} while the camera caught "
        f"up — CAM_SLACK ({r['slack']}) has to keep them on screen")


def test_a_camera_catching_up_never_leaves_the_player_off_the_screen(own_page):
    """The player does not move on screen while the camera is lagging behind — he
    *is* the lag — so a long enough teleport would slide him off the left edge
    and hold him there for a third of a second, which is worse than the cut.

    No chapter throws him back more than about 130px today, which is well inside
    the cap, so this drives a teleport far past anything the game produces: the
    protection is otherwise the one piece of this that never runs.
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
