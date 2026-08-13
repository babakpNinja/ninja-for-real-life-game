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
    never painted. What can be checked is that they *could* not fall back —
    covered by the next test.
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


RIG_DIFF = """
async ({ id, state, times, drop, dropOnce, nudge, region, pad }) => {
  const s = await import('/js/sprites.js');
  const art = await s.loadArt();
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


def test_the_ears_flop(desktop):
    """The same trick as the tail, on the pair of ears, which swing mirrored and
    a third as far. Their zero is not at t=0 — the run pose runs them a little
    behind the legs — so both moments are read off `ear: Math.sin(step - 0.7)`
    with step = t * 11.
    """
    flop = rig_diff(desktop, "bluey", "run", [0.2064, 0.2064], "ears", 0.2, nudge=("ears", 0.15, 0))
    rest = rig_diff(desktop, "bluey", "run", [0.0636, 0.0636], "ears", 0.2, nudge=("ears", 0.15, 0))
    assert flop["drawn"] == "rig", flop
    assert rest["inside"] + rest["outside"] < 3, f"rotated at rest? {rest}"
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


def test_the_eyes_shut_for_a_blink(desktop):
    """A still pose sampled off a blink and again at its peak. Nothing else in
    the pose moves — the check below proves that by replaying the same two
    moments with the eye boxes removed and getting an identical picture — so
    every pixel that changes is the blink, and it must land on the eyes.
    """
    r = rig_diff(desktop, "bluey", "jump", [0, "blink"], "eyes", 0.06)
    blind = rig_diff(desktop, "bluey", "jump", [0, "blink"], "eyes", 0.06, drop=["eyes"])
    assert r["drawn"] == "rig", r
    assert blind["inside"] + blind["outside"] == 0, f"the pose is not still: {blind}"
    assert r["inside"] > 200, f"the eyes did not close: {r}"
    # a ratio, not zero: the pose leans and squashes the body, so a box measured
    # on the flat artwork lands a few degrees off once it is drawn
    assert r["outside"] < r["inside"] * 0.05, f"the blink painted across the face: {r}"


def test_a_rig_with_no_extras_still_draws_its_character(desktop):
    """Most of the twenty-five have no tail, ears or eyes measured. A rig
    without them must still draw from the artwork rather than falling back to
    the procedural dog."""
    r = rig_diff(desktop, "bluey", "jump", [0, 0], drop_once=["tail", "ears", "eyes"])
    assert r["drawn"] == "rig", f"drew as {r['drawn']} without its extras"
    assert r["outside"] > 0, "dropping every part changed nothing — was anything drawn?"


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
