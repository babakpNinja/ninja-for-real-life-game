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
import itertools
import json
import math
import re
import subprocess
import sys
import time
import urllib.request
import warnings
from pathlib import Path

import pytest
from conftest import DESKTOP        # the viewport `own_page` opens too, authored there
from playwright.sync_api import TimeoutError as PlaywrightTimeout

APP = Path(__file__).resolve().parent.parent

# How this file reads facts out of the game's JavaScript: comments blanked
# first, regions bounded, one implementation shared with scripts/ (#233).
sys.path.insert(0, str(APP / "scripts"))
from js_source import code_only, function_body, object_literal  # noqa: E402

# The two tables that say what is *meant* to be missing: which (character,
# state) the rig is allowed to draw, and which states are strides. Imported
# rather than restated, and — since they are the declarations these tests
# check the artwork against — imported rather than read back out of the files
# they produce, which is the whole of #244.
import build_pose_joints  # noqa: E402
import fetch_assets  # noqa: E402

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

# Which state a character borrows from when the wiki never drew it (#215).
# Read out of sprites.js because that table is the author of the behaviour;
# restating it here would let the two drift and still agree with themselves.
SPRITES_SRC = (APP / "public" / "js" / "sprites.js").read_text()
POSE_FALLBACK = object_literal(SPRITES_SRC, "POSE_FALLBACK")


def resolved_pose(cid, state):
    """The file `poseFile` ends up at, walking the fallback chain. None when
    the chain runs out, which is the character the rig still has to carry."""
    seen = set()
    want = state
    while want and want not in seen:
        seen.add(want)
        frames = POSES.get(cid, {}).get(want)
        if frames:
            return frames[0]
        want = POSE_FALLBACK.get(want)
    return None


# The states a hero is drawn in while the game is running. `idle` is left out
# on purpose: nobody has an idle render, so every character is the rig there
# and fetch_assets.py declares that correct.
ACTION = ("run", "jump", "float", "cheer")

# The stride renders, which are the ones cut at the hip so their legs can swing
# (#212). The subjects come from the *declaration* — build_pose_joints.SWUNG is
# the list of states whose artwork is a stride — and not from the joints file it
# writes, because that file is the thing under test: a frame that stopped being
# cut used to drop out of this list and take its motion test with it, leaving a
# shorter green run (#244). Now it stays, and the guard below fails.
POSE_JOINTS = json.loads(
    (APP / "public" / "data" / "pose-joints.json").read_text())["joints"]
SWUNG = sorted((cid, state) for cid, state in POSED
               if state in build_pose_joints.SWUNG)
# How far above the hip a swing may still change a pixel, as a fraction of the
# drawn body. The swung band is clipped to below the hip line, so in principle
# the answer is none; the line is the *body's*, and a run is tilted 0.03rad
# about the feet, so it is not horizontal on the canvas. Measured across the
# four renders: the worst is 2.3px of a 247px body (0.009). This is 5px, and it
# is deliberately not more — before the clip existed the same measurement was
# 13px, so anything around 0.05 would pass the bug it was written for.
HIP_SLACK = 0.02
# Pixels in a row that a difference has to beat before the row counts as having
# moved. Lowering the hip on Bluey lit up one pixel at the tip of her tail, 35px
# above the joint — an outline rasterised a shade differently, not a limb.
NOISE_ROW = 3

# Who the rig still has to carry: a character the player can pick with no
# running render anywhere. Read the same way, so the day one is drawn for her
# the rig test below loses its subject and says so.
PLAYABLE = [c["id"] for c in
            json.loads((APP / "public" / "data" / "characters.json").read_text())["characters"]
            if c.get("playable")]
RIGGED_RUN = sorted(cid for cid in PLAYABLE if "run" not in POSES.get(cid, {}))


def rig_carries(cid, state):
    """Does fetch_assets declare the rig right here, and say why? '*' is everyone."""
    return bool(fetch_assets.RIG_OK.get((cid, state))
                or fetch_assets.RIG_OK.get(("*", state)))


# The three ways a state a player can reach gets drawn, which between them have
# to cover every (character, state) — see the partition guard further down.
OWN_RENDER = sorted((cid, state) for cid in PLAYABLE for state in ACTION
                    if state in POSES.get(cid, {}))
RIG_CARRIED = sorted((cid, state) for cid in PLAYABLE for state in ACTION
                     if rig_carries(cid, state))
# ...and the leftovers: the states with no drawing of their own that reach one
# anyway. This is where #215 lived: Bandit had a run render and nothing else, so
# the moment he jumped he turned into a front-facing standing dog. Empty the day
# every character is drawn in every state, and these tests disappear with the
# subject.
#
# A pair is in this list because it is neither drawn nor excused — deliberately
# *not* because `resolved_pose` finds it something, which is what the tests
# below assert. Asking the fallback chain here meant breaking the chain quietly
# emptied the list: five of seven subjects stopped existing and the two that
# were left passed (#244). Now a broken chain fails the test that says these
# borrow, and the guard below names the pair nothing draws.
BORROWED = sorted((cid, state) for cid in PLAYABLE for state in ACTION
                  if state not in POSES.get(cid, {}) and not rig_carries(cid, state))

# The states `frameMotion` turns the other way, read out of its switch for the
# same reason as POSE_FALLBACK above: a third one adopting JUMP_SWING arrives
# with a test rather than without one.
# Comments blanked before the split, because a case body runs to the next
# `case` and so picks up whatever is written above it: the reasoning for the
# cheer's swing is a comment between two cases (#231), and it quotes the very
# thing this looks for. A parse that reads the prose is answering about the
# prose — it put `float` in the list below until this line existed. The
# blanking and the bounding are `js_source`'s, shared with the three other
# places that read this file with a regex (#233).
_MOTION_CASES = re.split(r'case "(\w+)":', function_body(SPRITES_SRC, "frameMotion"))
JUMP_SWUNG = tuple(state for state, body in zip(_MOTION_CASES[1::2], _MOTION_CASES[2::2])
                   if "swing: JUMP_SWING" in body)
# ...and the borrowed states that land on a render cut at the hip, which are the
# only ones a swing can move at all: Bandit and Chilli leaping in their own
# running drawing (#219). Empty the day both are drawn a jump, and these tests
# go with the subject.
BORROWED_STRIDE = sorted((cid, state) for cid, state in BORROWED
                         if state in JUMP_SWUNG and resolved_pose(cid, state) in POSE_JOINTS)

# The other arm of the same switch: the states that leave a borrowed drawing's
# legs alone. `cheer` is the one that matters and it is a decision rather than an
# omission — see the comment on the case itself (#231) — so it is read from the
# source for the same reason as the two above: the day somebody swings a cheer,
# it changes here too instead of quietly losing the test that says it does not.
STILL_SWUNG = tuple(state for state, body in zip(_MOTION_CASES[1::2], _MOTION_CASES[2::2])
                    if "swing: 0" in body)
BORROWED_STILL = sorted((cid, state) for cid, state in BORROWED
                        if state in STILL_SWUNG and resolved_pose(cid, state) in POSE_JOINTS)


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


def test_every_character_ships_a_smaller_copy_too(base_url, art_credits):
    """The WebP beside each PNG is what nearly every browser is actually sent.

    Three ways it can be shipped and still be no use: absent, served as
    octet-stream (which no <img> will decode), or a different number of bytes
    from the one the credits — and the budget below — are counted in.
    """
    bad = []
    for cid, entry in sorted(art_credits["assets"].items()):
        small = entry.get("webp")
        if not small:
            bad.append(f"{cid}: no webp credited")
            continue
        try:
            # GET, not HEAD: the bytes are the point. A HEAD would be answered
            # by the same code without ever reading the file, and the length is
            # counted off the body rather than a header no hop has to send.
            with urllib.request.urlopen(f"{base_url}/{small}", timeout=15) as resp:
                body = resp.read()
                if resp.status != 200:
                    bad.append(f"{cid}:{resp.status}")
                elif resp.headers.get("content-type") != "image/webp":
                    bad.append(f"{cid}: served as {resp.headers.get('content-type')}")
                elif len(body) != entry.get("webp_bytes"):
                    bad.append(f"{cid}: {len(body)} bytes, "
                               f"credited as {entry.get('webp_bytes')}")
                elif not body.startswith(b"RIFF") or body[8:12] != b"WEBP":
                    bad.append(f"{cid}: served {body[:12]!r}, which is not a webp")
        except Exception as exc:
            bad.append(f"{cid}:{exc}")
    assert not bad, f"small copies not served: {bad}"


# What the gallery is allowed to pull down. Measured, not chosen: the 25 WebPs
# are 593KB on disk and ~610KB with their headers, against 2224KB of PNG (run
# `scripts/fetch_assets.py --webp` to print both totals). Each character that
# regressed to its PNG would add ~65KB, so this trips once about seven of them
# have — well before the whole page is back where it started, and well clear of
# the artwork growing by a character or two.
GALLERY_BUDGET = 1_000_000


def test_the_gallery_stays_inside_its_transfer_budget(own_page):
    """25 characters at once, on a phone, in a car. Nothing else measures this:
    every other test here asks whether the artwork arrived, and a page that
    pulls 2.2 MB to show the same pictures passes all of them (#138)."""
    page = own_page
    page.click("#btn-gallery")
    page.wait_for_selector(".char-card")
    page.wait_for_function("() => window.__art().loaded.length >= 25", timeout=30000)
    got = page.evaluate(
        """() => performance.getEntriesByType('resource')
             .filter((e) => e.name.includes('/assets/characters/'))
             .map((e) => ({ name: e.name.split('/').pop(), size: e.transferSize }))"""
    )
    page.click("#btn-back")
    # A page whose cache served everything reports transferSize 0 and would pass
    # an empty budget for free; this page is opened for this test and has none.
    assert len(got) >= 25, f"only {len(got)} character files were fetched at all"
    assert all(e["size"] > 0 for e in got), (
        f"transferSize is not being reported: {[e for e in got if not e['size']][:3]}")
    total = sum(e["size"] for e in got)
    assert total <= GALLERY_BUDGET, (
        f"the gallery pulled {total // 1024}KB over {GALLERY_BUDGET // 1024}KB in "
        f"{len(got)} files; the biggest are "
        f"{sorted(got, key=lambda e: -e['size'])[:5]}")


def test_every_pose_frame_ships_a_smaller_copy_too(base_url, art_credits):
    """The same three ways as the characters, over the other block of credits.

    Separate entries, separate directory, separate encode: a deploy can ship all
    25 character WebPs and none of the nine pose ones, and every test above still
    passes (#238).
    """
    bad = []
    for key, entry in sorted(art_credits["poses"].items()):
        small = entry.get("webp")
        if not small:
            bad.append(f"{key}: no webp credited")
            continue
        try:
            with urllib.request.urlopen(f"{base_url}/{small}", timeout=15) as resp:
                body = resp.read()
                if resp.status != 200:
                    bad.append(f"{key}:{resp.status}")
                elif resp.headers.get("content-type") != "image/webp":
                    bad.append(f"{key}: served as {resp.headers.get('content-type')}")
                elif len(body) != entry.get("webp_bytes"):
                    bad.append(f"{key}: {len(body)} bytes, "
                               f"credited as {entry.get('webp_bytes')}")
                elif not body.startswith(b"RIFF") or body[8:12] != b"WEBP":
                    bad.append(f"{key}: served {body[:12]!r}, which is not a webp")
        except Exception as exc:
            bad.append(f"{key}:{exc}")
    assert not bad, f"small copies not served: {bad}"


# What playing a chapter is allowed to pull down. Measured the same way as the
# gallery's: the nine pose renders are 860KB of PNG against 245KB of WebP (run
# `scripts/fetch_assets.py --webp` for both totals), so ~258KB with headers. A
# frame that regressed to its PNG adds 60-115KB, so this trips once about two of
# them have, and leaves room for three or four more poses being drawn.
#
# This budget is the one a player waits through. The gallery's is spent when a
# menu is opened, and can be spent while somebody reads a screen; these nine are
# fetched at boot for the cast that is about to race, so the cost lands between
# pressing play and seeing a dog that is really Bluey.
RACE_BUDGET = 450_000


def test_the_race_stays_inside_its_transfer_budget(own_page):
    """The gallery budget covers a third of the artwork and reads as covering
    the artwork (#238). This is the other two thirds, and the ones that arrive
    while a chapter is starting rather than while a menu is open."""
    page = own_page
    page.click("#btn-chapters")
    page.wait_for_selector(".chapter-card")
    page.click(".chapter-card[data-ch='0']")
    page.wait_for_selector("#btn-go")
    page.click("#btn-go")
    # Everything the run needs, arrived: `posePending` empties whether a frame
    # loaded or gave up, so this is "the fetching has stopped", and the count
    # below is what says the fetching happened at all.
    page.wait_for_function("() => window.__art().posePending.length === 0", timeout=30000)
    got = page.evaluate(
        """() => performance.getEntriesByType('resource')
             .filter((e) => e.name.includes('/assets/poses/'))
             .map((e) => ({ name: e.name.split('/').pop(), size: e.transferSize }))"""
    )
    art = page.evaluate("window.__art()")
    page.evaluate("() => window.game.stop()")   # #btn-go started the render loop
    assert len(got) >= 9, f"only {len(got)} pose frames were fetched at all: {got}"
    # A page whose cache served everything reports transferSize 0 and would pass
    # an empty budget for free; this page is opened for this test and has none.
    assert all(e["size"] > 0 for e in got), (
        f"transferSize is not being reported: {[e for e in got if not e['size']][:3]}")
    assert len(art["poseFrames"]) >= 9, (
        f"only {len(art['poseFrames'])} of the nine frames decoded, so this measured "
        f"a race drawn from the rig: {sorted(art['poseFrames'])}")
    total = sum(e["size"] for e in got)
    assert total <= RACE_BUDGET, (
        f"the race pulled {total // 1024}KB over {RACE_BUDGET // 1024}KB in "
        f"{len(got)} files; the biggest are "
        f"{sorted(got, key=lambda e: -e['size'])[:5]}")


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


# --- the story, and getting past it (#255) -----------------------------------
# The player this is built for is three. Between the menu and a moving dog there
# used to be two taps and three paragraphs of grey text she cannot read, so the
# card moved out of the way and learned to read itself out loud. These four say
# both halves of that: nothing to read on the way in, and the story still there
# — spoken — for whoever wants it.

# Speech is stubbed rather than listened to: a headless browser has the API and
# no voices, so `speak()` is a call that makes no sound and fires no events. What
# is asserted is what the game asked for, which is the part the game controls.
#
# The stub also *plays* the two endings a real voice has, because the button that
# says what the game is doing is driven by them and nothing else here can produce
# one: `__finish()` for the queue running out, `__fail()` for the
# 'synthesis-failed' a device with no voice answers with (#289, #290).
SPY_ON_SPEECH = """
() => {
  window.__said = [];
  window.__hushes = 0;
  window.__queued = [];
  const synth = window.speechSynthesis;
  synth.speak = (u) => { window.__said.push(u.text); window.__queued.push(u); };
  synth.cancel = () => { window.__hushes += 1; };
  const drain = (ev, arg, n) => {
    const q = window.__queued.splice(0, n === undefined ? window.__queued.length : n);
    q.forEach((u) => { if (u[ev]) u[ev](arg); });
    return q.length;
  };
  window.__finish = (n) => drain("onend", {}, n);
  window.__fail = (n) => drain("onerror", { error: "synthesis-failed" }, n);
}
"""


def test_play_puts_nothing_to_read_between_the_menu_and_the_game(own_page):
    """The complaint itself: one tap, and the dog is moving."""
    page = own_page
    assert page.locator("p.story").count() == 0, "the menu is showing story text"
    page.click("#btn-play")
    page.wait_for_timeout(400)
    assert page.evaluate("window.game.mode") == "playing", (
        "▶ Play did not start a chapter")
    assert page.locator("p.story").count() == 0, (
        "there is still story text on screen after ▶ Play — it is a stop on the "
        "way in again (#255)")
    assert page.locator("#overlay").is_hidden(), "an overlay is still up over the game"
    page.evaluate("() => window.game.stop()")


def test_the_story_is_still_one_tap_away_and_still_leads_into_the_chapter(own_page):
    """Moved, not deleted. The card is what the chapters mean to whoever reads
    them, and it is reached from the menu and from chapter select."""
    page = own_page
    page.click("#btn-story")
    page.wait_for_selector("#btn-go")
    assert "Keepy Uppy" in page.locator("h2").inner_text()
    assert page.locator("p.story").count() >= 2, "the story text itself went missing"
    page.click("#btn-go")
    page.wait_for_timeout(400)
    assert page.evaluate("window.game.mode") == "playing"
    page.evaluate("() => window.game.stop()")


def test_the_story_card_reads_itself_out_loud(own_page):
    """The other half of the fix, and the one that serves the reason: a card
    nobody in the room can read is worth nothing sitting there silently."""
    page = own_page
    page.evaluate(SPY_ON_SPEECH)
    page.click("#btn-story")
    page.wait_for_selector("#btn-go")
    said = page.evaluate("() => window.__said")
    assert said, "the story card said nothing out loud"
    shown = [t.strip() for t in page.locator("p.story, p.joke").all_inner_texts()]
    spoken = " ".join(said)
    assert "Keepy Uppy" in spoken, f"the chapter was never named: {said}"
    missing = [t for t in shown if t and t not in spoken]
    assert not missing, f"on the card and never read out: {missing}"
    # a queued utterance outlives the screen that asked for it. Counted from
    # *here*, not from zero: `read()` clears the queue before it starts talking,
    # so a card that has been opened has already hushed once and `>= 1` would be
    # true whatever leaving the card does
    hushed = page.evaluate("() => window.__hushes")
    page.click("#btn-go")
    page.wait_for_timeout(300)
    assert page.evaluate("() => window.__hushes") > hushed, (
        "leaving the card left the story talking over the chapter")
    page.evaluate("() => window.game.stop()")


def test_a_muted_game_reads_nothing_out_loud(own_page):
    """The speaker button is on the HUD and means the whole game, speech
    included — speech does not go through the mixer, so muting has to reach it
    by hand."""
    page = own_page
    page.evaluate(SPY_ON_SPEECH)
    # the HUD is hidden on the menu; this is still its own listener, clicked
    page.evaluate("() => document.getElementById('btn-mute').click()")
    assert page.evaluate("() => document.getElementById('btn-mute').textContent") == "🔇"
    page.click("#btn-story")
    page.wait_for_selector("#btn-go")
    assert page.evaluate("() => window.__said") == [], (
        "a muted game read the story out loud anyway")
    page.evaluate("() => document.getElementById('btn-mute').click()")


def test_muting_while_the_story_is_being_read_stops_it(own_page):
    """Mute is pressed *because* something is talking, and the thing talking is
    the one sound in the game that is not on the mixer — silencing the master
    gain leaves the story reading on over a quiet game."""
    page = own_page
    page.evaluate(SPY_ON_SPEECH)
    page.click("#btn-story")
    page.wait_for_selector("#btn-go")
    assert page.evaluate("() => window.__said"), "nothing was being read to interrupt"
    before = page.evaluate("() => window.__hushes")
    page.evaluate("() => document.getElementById('btn-mute').click()")
    assert page.evaluate("() => window.__hushes") > before, (
        "the mute button silenced the game and left the story talking")
    page.evaluate("() => document.getElementById('btn-mute').click()")


# --- hearing it again, and being told what is happening (#290) ---------------
# The read above happens once, when the card opens, and there is nothing on the
# card about it: a three-year-old who misses the start cannot ask for it back,
# and a muted game answers the same way a talking one does — by looking exactly
# like a card with a button that does nothing.


def story_button(page):
    return page.locator("#btn-read").inner_text().strip()


def test_the_story_can_be_read_again_from_the_card(own_page):
    """The replay itself: the same lines, asked for a second time."""
    page = own_page
    page.evaluate(SPY_ON_SPEECH)
    page.click("#btn-story")
    page.wait_for_selector("#btn-read")
    first = page.evaluate("() => window.__said")
    assert first, "the story card said nothing out loud"
    page.evaluate("() => { window.__said = []; }")
    page.click("#btn-read")
    page.wait_for_timeout(200)
    again = page.evaluate("() => window.__said")
    assert again == first, (
        f"tapping the speaker did not read the same story again:\n"
        f"  first: {first}\n  again: {again}")
    shown = [t.strip() for t in page.locator("p.story, p.joke").all_inner_texts()]
    missing = [t for t in shown if t and t not in " ".join(again)]
    assert not missing, f"on the card and not read out on the replay: {missing}"


def test_the_speaker_says_it_is_reading_and_stops_saying_so_at_the_end(own_page):
    """The state, both times it changes. `end` is the queue's, not the first
    paragraph's — the label going back while three paragraphs are still to come
    is the same lie as never going back."""
    page = own_page
    page.evaluate(SPY_ON_SPEECH)
    page.click("#btn-story")
    page.wait_for_selector("#btn-read")
    assert story_button(page) == "🔊 Reading…", (
        f"the card opened reading the story and the button said {story_button(page)!r}")
    assert page.evaluate("() => window.__queued.length") > 1, (
        "the story went out as one utterance — this test cannot tell the queue's "
        "end from its first line any more")
    assert page.evaluate("() => window.__finish(1)") == 1
    assert story_button(page) == "🔊 Reading…", (
        f"the button stopped saying it was reading after the first line, with the "
        f"rest of the story still to come: {story_button(page)!r}")
    assert page.evaluate("() => window.__finish()") > 0, "nothing was queued to finish"
    assert story_button(page) == "🔊 Read it again", (
        f"the story ended and the button still says {story_button(page)!r}")
    page.click("#btn-read")
    assert story_button(page) == "🔊 Reading…", "the replay said nothing about itself"
    page.evaluate("() => window.__finish()")
    assert story_button(page) == "🔊 Read it again"


def test_a_device_with_no_voice_says_so_instead_of_reading_forever(own_page):
    """The case #289 measured: the API is there, `speak()` returns, and the only
    thing that ever happens is 'synthesis-failed'. A button left on "Reading…"
    for the rest of the card is a worse answer than the silence it replaced."""
    page = own_page
    page.evaluate(SPY_ON_SPEECH)
    page.click("#btn-story")
    page.wait_for_selector("#btn-read")
    assert page.evaluate("() => window.__fail()") > 0, "nothing was queued to fail"
    assert story_button(page) == "🔇 No voice here", (
        f"speech failed and the button says {story_button(page)!r}")


def test_a_game_saved_muted_says_so_on_the_button_and_reads_nothing(own_page):
    """The muted save, from the reload it survives — not from a mute pressed in
    this session. The mixer was only told about a saved mute on the way into a
    chapter, and the story card is reached first, so this card used to read
    itself out loud on a game that had been silenced days ago."""
    page = own_page
    page.evaluate("() => localStorage.setItem('forreallife.save.v1',"
                  " JSON.stringify({muted: true}))")
    page.reload(wait_until="domcontentloaded")
    page.wait_for_function("window.__ready === true", timeout=20000)
    page.evaluate(SPY_ON_SPEECH)
    page.click("#btn-story")
    page.wait_for_selector("#btn-read")
    assert page.evaluate("() => window.__said") == [], (
        "a game saved muted read the story out loud after a reload")
    assert story_button(page) == "🔇 Sound is off", (
        f"the muted card says {story_button(page)!r} — a speaker button that does "
        f"nothing and does not say why")
    page.click("#btn-read")
    page.wait_for_timeout(200)
    assert page.evaluate("() => window.__said") == []
    assert story_button(page) == "🔇 Sound is off"


def test_muting_mid_read_leaves_the_button_muted_not_broken(own_page):
    """Mute cancels the queue, and a cancelled utterance ends with an error. It
    belongs to the read that was silenced, not to the device: reporting it as
    "no voice on this device" would libel a phone that speaks perfectly well."""
    page = own_page
    page.evaluate(SPY_ON_SPEECH)
    page.click("#btn-story")
    page.wait_for_selector("#btn-read")
    assert story_button(page) == "🔊 Reading…"
    page.evaluate("() => document.getElementById('btn-mute').click()")
    assert story_button(page) == "🔇 Sound is off", (
        f"muting mid-story left the button saying {story_button(page)!r}")
    page.evaluate("() => window.__fail()")     # the cancelled utterances, arriving late
    assert story_button(page) == "🔇 Sound is off", (
        f"a cancelled read was reported as a device with no voice: "
        f"{story_button(page)!r}")
    page.evaluate("() => document.getElementById('btn-mute').click()")


def test_this_browser_cannot_speak(own_page):
    """The gap the tests above cannot cover, pinned so it stays visible (#289).

    Every speech assertion in this file stubs `speak`/`cancel` and is about what
    the game *asked for*. That is not a choice: this browser has no voice, so
    nothing here would notice if a real one rejected the utterances. The numbers
    are in `audio.js` beside `read()`, along with what it took to get a voice
    into the container at all (espeak-ng + speech-dispatcher + a headed Chromium
    under Xvfb + SPEECHD_ADDRESS — and `speak()` still failed).

    Asserted rather than written down, because a documented gap goes stale
    silently. If this test fails, the environment has changed in the one way
    that matters: reopen #289 and write the real check — drive `read()` and wait
    for a genuine `onstart` — then delete this.
    """
    page = own_page
    got = page.evaluate("""() => new Promise(res => {
        const ev = [];
        const u = new SpeechSynthesisUtterance('Chapter one. The great cardboard box.');
        u.onstart = () => ev.push('start');
        u.onend = () => ev.push('end');
        u.onerror = e => ev.push('error:' + e.error);
        speechSynthesis.speak(u);
        setTimeout(() => res({voices: speechSynthesis.getVoices().length, ev,
                              speaking: speechSynthesis.speaking}), 1500);
    })""")
    assert got["voices"] == 0, (
        f"this browser now has {got['voices']} voice(s) — the stubbed speech tests are no "
        f"longer the best available check. Write the real one (#289) and delete this test")
    assert "start" not in got["ev"], (
        f"an utterance actually started here: {got['ev']} — a real voice can be driven "
        f"after all (#289)")
    assert got["speaking"] is False, "speechSynthesis reports it is speaking with no voices"


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
def test_play_leads_straight_into_chapter_one(desktop):
    """One tap from the menu to a moving dog (#255).

    This used to go via the story card and assert its heading, and the player it
    is for is three: three paragraphs she cannot read stood between ▶ Play and
    the game. The card is still there — the test below opens it — it is just no
    longer the gate."""
    desktop.click("#btn-play")
    desktop.wait_for_timeout(500)
    assert desktop.locator("#hud").is_visible()
    assert desktop.evaluate("window.game.mode") == "playing"
    assert desktop.evaluate("window.game.ch.title") == "Keepy Uppy"


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


# --- the other copy of the artwork ------------------------------------------
# Everything above runs in headless Chromium, which reads WebP, so every one of
# those tests covers exactly one of the two files each character ships. These
# two are the other one: the browser that cannot read the small copy, and the
# small copy that is not there.

NO_WEBP = """
// what Safari before 14 did: asked for a WebP data URL, it hands back a PNG one
const real = HTMLCanvasElement.prototype.toDataURL;
HTMLCanvasElement.prototype.toDataURL = function (type, ...rest) {
  return String(type).includes("webp") ? real.call(this, "image/png")
                                       : real.call(this, type, ...rest);
};
"""


def test_a_browser_that_cannot_read_webp_gets_the_pngs(own_page):
    """The fallback is the whole reason the PNGs are still shipped, and it is
    the branch this suite's own browser can never take."""
    page = own_page
    asked, asked_poses = [], []
    page.on("request", lambda r: asked.append(r.url) if "/assets/characters/" in r.url else None)
    page.on("request", lambda r: asked_poses.append(r.url) if "/assets/poses/" in r.url else None)
    page.add_init_script(NO_WEBP)
    page.reload(wait_until="domcontentloaded")
    page.wait_for_function("window.__ready === true", timeout=20000)
    page.click("#btn-gallery")
    page.wait_for_selector(".char-card")
    page.wait_for_function("() => window.__art().loaded.length >= 25", timeout=30000)
    art = page.evaluate("window.__art()")
    page.click("#btn-back")
    assert art["webp"] is False, "the page still thinks it can read webp; test proves nothing"
    assert not art["failed"], f"failed to load: {art['failed']}"
    fell_back = [k for k, v in art["drawn"].items() if v != "rig"]
    assert not fell_back, f"drawn without artwork: {fell_back}"
    webp = [u for u in asked if u.endswith(".webp")]
    assert not webp, f"asked for {len(webp)} files it cannot decode: {webp[:3]}"
    assert len(asked) >= 25, f"only {len(asked)} character files were asked for"
    # ...and the pose frames, which boot fetches for the racing cast off the same
    # switch. A browser sent nine files it cannot decode has no artwork to run in.
    poses = page.evaluate("window.__art()")["poseFrames"]
    assert len(poses) >= 9, f"only {len(poses)} pose frames decoded: {sorted(poses)}"
    bad = [u for u in asked_poses if u.endswith(".webp")]
    assert not bad, f"asked for {len(bad)} pose webps it cannot decode: {bad[:3]}"


def test_a_missing_small_copy_falls_back_to_the_png(own_page):
    """A deploy that ships the WebPs badly — or drops them — takes the artwork
    away from nearly every browser, while the PNG beside it is fine and every
    other test in this file passes. So the give-up is per file, not per
    character: spend the retries on the small copy, then ask for the big one."""
    page = own_page
    # ...and every retry of it: `load()` asks again as `<file>.webp?retry=N`, so
    # a pattern anchored at .webp blocks the first attempt and lets the second
    # through — which looks exactly like a working fallback and proves nothing.
    page.route("**/assets/characters/*.webp*", lambda route: route.abort())
    page.reload(wait_until="domcontentloaded")
    page.wait_for_function("window.__ready === true", timeout=20000)
    page.click("#btn-gallery")
    page.wait_for_selector(".char-card")
    try:
        # ~5s of retries, then the switch. Waited for by the mechanism rather
        # than by `loaded`, so a fallback that never happens is reported as
        # itself instead of as a page that timed out with nothing to say.
        page.wait_for_function(
            "() => window.__art().webpFellBack.length >= 25", timeout=20000)
    except PlaywrightTimeout:
        art = page.evaluate("window.__art()")
        pytest.fail(f"only {len(art['webpFellBack'])} of 25 characters moved to their "
                    f"png when the webp would not come: {len(art['gaveUp'])} gave up "
                    f"altogether and {len(art['loaded'])} are showing artwork",
                    pytrace=False)
    page.wait_for_function("() => window.__art().loaded.length >= 25", timeout=20000)
    art = page.evaluate("window.__art()")
    page.click("#btn-back")
    assert art["webp"] is True, "this browser never asked for a webp; test proves nothing"
    assert not art["failed"], f"still showing nothing: {art['failed']}"
    fell_back = [k for k, v in art["drawn"].items() if v != "rig"]
    assert not fell_back, f"drawn without artwork: {fell_back}"


# Keeping a pose frame asked for, the way the game keeps it asked for.
#
# The retries and the give-up live in `load()`, which only runs when something
# calls for the picture: a character is drawn 60 times a second, so five tries
# take about five seconds. `preload` calls once, at boot, so a pose frame that
# fails there stays failed until a chapter draws that state — which is the whole
# reason this is done through `drawCharacter` rather than by waiting on the menu.
# It is the call `game.js` makes for the hero on every frame of a race.
POSE_RECOVERY = """
async ({ frame, seconds }) => {
  const s = await import('/js/sprites.js');
  await s.loadArt();
  const ctx = document.createElement('canvas').getContext('2d');
  const until = performance.now() + seconds * 1000;
  while (performance.now() < until
         && !(s.artState().poseWebpFellBack.includes(frame)
              && s.artState().poseFrames.includes(frame))) {
    s.drawCharacter(ctx, 'bluey', 60, 110, 90, null, performance.now() / 1000, 'run');
    await new Promise((r) => requestAnimationFrame(r));
  }
  // One more, after: the loop leaves the moment the png is *there*, and how the
  // character is drawn is only recorded by a draw that had it.
  s.drawCharacter(ctx, 'bluey', 60, 110, 90, null, performance.now() / 1000, 'run');
  return s.artState();
}
"""
BLUEY_RUN = "assets/poses/bluey-run-0.png"


def test_a_missing_small_pose_copy_falls_back_to_the_png(own_page):
    """The same give-up, one directory over, and the one that would be noticed
    last: a chapter whose pose frames never arrive still runs — every character
    drops to the rig — so the game looks fine and quietly stops being drawn the
    way it is meant to be drawn.
    """
    page = own_page
    # every attempt, including the retries `load()` asks for as `<file>?retry=N`
    page.route("**/assets/poses/*.webp*", lambda route: route.abort())
    page.reload(wait_until="domcontentloaded")
    page.wait_for_function("window.__ready === true", timeout=20000)
    art = page.evaluate(POSE_RECOVERY, {"frame": BLUEY_RUN, "seconds": 15})
    assert art["webp"] is True, "this browser never asked for a webp; test proves nothing"
    assert BLUEY_RUN in art["poseWebpFellBack"], (
        f"the run frame never moved to its png when the webp would not come: "
        f"{art['poseWebpFellBack']}")
    assert BLUEY_RUN in art["poseFrames"], (
        f"it gave up on the webp and the png did not arrive either: "
        f"{sorted(art['poseFrames'])}")
    assert art["drawn"].get("bluey") == "pose", (
        f"bluey is being drawn as {art['drawn'].get('bluey')!r} — the fallback got the "
        "bytes and the race is still not drawn from the artwork")
    assert not art["webpFellBack"], (
        f"the characters gave up on their small copies too ({art['webpFellBack'][:3]}), "
        "so the give-up is per directory rather than per file")


def test_one_missing_pose_webp_does_not_move_the_others(own_page):
    """The failure the per-file give-up exists to prevent, stated on its own: a
    deploy that drops one frame must cost that frame's PNG, not all nine — which
    is 800KB spent to recover from 30KB going missing."""
    page = own_page
    page.route(f"**/{BLUEY_RUN[:-4]}.webp*", lambda route: route.abort())
    page.reload(wait_until="domcontentloaded")
    page.wait_for_function("window.__ready === true", timeout=20000)
    art = page.evaluate(POSE_RECOVERY, {"frame": BLUEY_RUN, "seconds": 15})
    assert art["poseWebpFellBack"] == [BLUEY_RUN], (
        f"blocking one frame moved {len(art['poseWebpFellBack'])} of them: "
        f"{sorted(art['poseWebpFellBack'])}")
    assert len(art["poseFrames"]) >= 9, (
        f"only {len(art['poseFrames'])} frames are showing, so the other eight are "
        f"not the proof they look like: {sorted(art['poseFrames'])}")


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


# Waits for its own artwork rather than for the page (`art_settled`, below): it
# calls `preload` for a character the gallery may never have drawn, so the fetch
# it has to wait for does not exist until it starts. Then it decodes the render
# itself before the first shot, and the pose art is deleted for the duration, so
# neither drawing can change between the two frames it compares (#224).
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


FAMILY = """
async ({ id, states }) => {
  const s = await import('/js/sprites.js');
  const art = await s.loadArt();
  s.preload([id]);
  const want = Object.values(art.poses[id] || {}).flat();
  for (let i = 0; i < 100; i++) {
    const have = s.artState().poseFrames;
    if (want.every((f) => have.includes(f))) break;
    await new Promise((r) => setTimeout(r, 50));
  }
  const size = 320;
  const out = {};
  for (const state of states) {
    const c = document.createElement('canvas');
    c.width = size; c.height = size + 4;
    const ctx = c.getContext('2d');
    s.drawCharacter(ctx, id, size / 2, size, size * 0.9, null, 0.31, state, 1);
    const d = ctx.getImageData(0, 0, c.width, c.height).data;
    let opaque = 0;
    for (let i = 3; i < d.length; i += 4) if (d[i] > 8) opaque++;
    out[state] = { drawn: s.artState().drawn[id], opaque };
  }
  return out;
}
"""


@pytest.mark.parametrize("cid", PLAYABLE)
def test_a_hero_is_the_same_kind_of_drawing_all_the_way_through(desktop, cid):
    """#215: Bandit ran as the artist's Bandit and landed as a different dog.

    What changed was not the pose but the *source* — a hand-drawn render for
    run, a rig assembled out of shapes for jump — and swapping between two
    drawing styles mid-leap reads as the character being replaced.

    The issue proposed measuring the silhouette instead, failing when the
    width/height ratio jumps too far between states. Measured, that cannot
    separate this: Bluey's run to her own hand-drawn cheer moves the ratio by
    0.252, more than any of the three defects (0.170, 0.218, 0.230). A bound
    loose enough for the real artwork passes every costume change. So the thing
    asserted is the thing that was actually wrong: one family, all four states.

    Muffin has no artwork at all and is the rig throughout, which is uniform and
    correct — the `opaque` floor is what stops "uniformly nothing" passing.
    """
    got = desktop.evaluate(FAMILY, {"id": cid, "states": list(ACTION)})
    for state, r in got.items():
        assert r["opaque"] > 1000, f"{cid} in {state} drew almost nothing: {got}"
    families = {r["drawn"] for r in got.values()}
    assert len(families) == 1, (
        f"{cid} changes what kind of drawing it is mid-play: "
        + ", ".join(f"{s}={r['drawn']}" for s, r in sorted(got.items())))


def test_every_playable_action_state_is_drawn_on_purpose():
    """The three buckets have to cover the grid, or the tests leave with it (#244).

    A state the player can reach in two taps is drawn one of three ways: the
    character's own render, a render borrowed through POSE_FALLBACK, or the rig
    — and the rig is only allowed where `fetch_assets.RIG_OK` names the pair and
    says why. The buckets are read from three different places, so this is the
    one line that says they add up: nothing uncovered, nothing counted twice.

    It is a claim about the whole grid rather than a count, which is the point.
    The count could be satisfied by the list getting shorter, and that is exactly
    what used to happen: cutting POSE_FALLBACK down took five of `BORROWED`'s
    seven cases away and the run went green on the two left over.

    `fetch_assets.coverage_problems()` asks the same question of the wider
    `states()` grid and test_prose.py runs it through `--check`. It is asked
    again here because it has to be in *this* collection — a guard in another
    file cannot notice that this one got shorter.
    """
    grid = sorted((cid, state) for cid in PLAYABLE for state in ACTION)
    assert grid, "no playable character has an action state, so this guards nothing"

    counted = sorted(OWN_RENDER + BORROWED + RIG_CARRIED)
    assert counted == grid, (
        "the three buckets do not partition the grid, so a state is drawn by "
        "nobody or claimed by two:\n"
        f"  in none: {sorted(set(grid) - set(counted))}\n"
        f"  in two: {sorted({p for p in counted if counted.count(p) > 1})}")

    unreachable = [(cid, state) for cid, state in BORROWED
                   if not resolved_pose(cid, state)]
    assert not unreachable, (
        "these states have no render of their own, are not named in RIG_OK, and "
        "the POSE_FALLBACK chain does not reach a drawing for them either — so "
        "the rig draws them and nothing says that was meant: "
        f"{', '.join(f'{c}/{s}' for c, s in unreachable)}.\n"
        "Fetch a render, point POSE_FALLBACK at one, or say why the rig is right "
        "here in fetch_assets.RIG_OK. This is the #215 bug: a hero who turns into "
        "a front-facing standing dog the moment they leave the ground.")


@pytest.mark.parametrize("cid,state", BORROWED, ids=[f"{c}-{s}" for c, s in BORROWED])
def test_a_state_with_no_render_of_its_own_borrows_one(desktop, cid, state):
    """The half of #215 the uniformity check above cannot see: it would also be
    satisfied by giving up and drawing everyone as the rig.

    So, for each state that has no drawing of its own, the picture is a pose and
    it is not the rig's version of the same moment.
    """
    r = pose_diff(desktop,
                  {"id": cid, "state": state, "t": 0.31},
                  {"id": cid, "state": state, "t": 0.31, "frames": {}})
    assert r["a"] == "pose", f"{cid} in {state} drew as {r['a']} — no fallback reached"
    assert r["b"] == "rig", f"with no pose frames at all {cid} drew as {r['b']}"
    assert r["opaqueA"] > 1000, f"the borrowed frame painted almost nothing: {r}"
    assert r["changed"] > r["opaqueA"] * 0.1, (
        f"the borrowed render is the same picture as the rig's: {r}")


@pytest.mark.parametrize("cid,state", BORROWED, ids=[f"{c}-{s}" for c, s in BORROWED])
def test_a_borrowed_state_draws_the_frame_the_chain_lands_on(desktop, cid, state):
    """Borrowing *a* drawing is not the same as borrowing the right one: with a
    chain (float falls to jump falls to run) an off-by-one hop still shows a dog.

    The frame the chain says it ends at is replaced with a different character's
    render and the two draws compared. If the picture does not change, the state
    that was overridden is not the one being drawn from.
    """
    landed = resolved_pose(cid, state)
    assert landed, (
        f"{cid}/{state} reaches no drawing at all, so there is no frame to repoint "
        "— test_every_playable_action_state_is_drawn_on_purpose says why")
    owner = next(s for s, f in POSES[cid].items() if f[0] == landed)
    other = "assets/poses/bluey-cheer-0.png" if cid != "bluey" else "assets/poses/bingo-cheer-0.png"
    r = pose_diff(desktop,
                  {"id": cid, "state": state, "t": 0.31},
                  {"id": cid, "state": state, "t": 0.31,
                   "frames": dict(POSES[cid], **{owner: [other]})})
    assert r["a"] == "pose" and r["b"] == "pose", r
    assert r["opaqueA"] > 1000 and r["opaqueB"] > 1000, f"one of them is blank: {r}"
    assert r["changed"] > r["opaqueA"] * 0.2, (
        f"{cid}/{state} did not change when {owner} was repointed — it is not "
        f"drawing through {landed}: {r}")


# Same as RIG_DIFF on the loading question (#224): it preloads the character
# itself and then waits for the one pose file it is about to draw, by path, so
# `art_settled` in the test would be waiting for the wrong moment — before the
# request it cares about has been made.
SWING_PROBE = """
async ({ id, state, size, t, half, noise }) => {
  const s = await import('/js/sprites.js');
  const art = await s.loadArt();
  const path = art.poses[id][state][0];
  s.preload([id]);
  for (let i = 0; i < 100; i++) {
    if (s.artState().poseFrames.includes(path)) break;
    await new Promise((r) => setTimeout(r, 50));
  }
  const W = size, H = size + 90; // room below the feet: a swung foot reaches past them

  // One draw, with the frame's joint replaced for the duration. `null` takes it
  // out altogether, which is how the pose was drawn before #212 — same motion,
  // same instant, legs where the artist put them.
  const shot = (phase, joint) => {
    const held = art.poseJoints[path];
    if (joint === undefined) art.poseJoints[path] = held;
    else if (joint === null) delete art.poseJoints[path];
    else art.poseJoints[path] = joint;
    const c = document.createElement('canvas');
    c.width = W; c.height = H;
    const ctx = c.getContext('2d');
    s.drawCharacter(ctx, id, W / 2, size, size * 0.9, null, t, state, 1, null, phase);
    if (held === undefined) delete art.poseJoints[path]; else art.poseJoints[path] = held;
    return ctx.getImageData(0, 0, W, H).data;
  };

  // The drawing's own box. Solid pixels only: the contact shadow under the feet
  // is painted at about 14% alpha, and counting it would put the bottom of the
  // "body" 30px below the feet and drag the hip line down with it.
  const box = (d) => {
    let top = null, bottom = null;
    for (let y = 0; y < H; y++) {
      for (let x = 0; x < W; x++) {
        if (d[(y * W + x) * 4 + 3] > 128) { if (top === null) top = y; bottom = y; break; }
      }
    }
    return { top, bottom };
  };

  // Which rows the two pictures disagree on, and by how much. A row needs more
  // than `noise` pixels to count as a row that moved: a couple of antialiased
  // pixels on an outline is not a body part, and taking the first of those as
  // the top of the difference makes this measure the rasteriser.
  const diff = (a, b, noise) => {
    const per = [];
    let changed = 0;
    for (let y = 0; y < H; y++) {
      let n = 0;
      for (let x = 0; x < W; x++) {
        const i = (y * W + x) * 4;
        let m = 0;
        for (let k = 0; k < 4; k++) m = Math.max(m, Math.abs(a[i + k] - b[i + k]));
        if (m > 12) n++;
      }
      per.push(n);
      changed += n;
    }
    const rows = per.map((n, y) => [n, y]).filter(([n]) => n > noise).map(([, y]) => y);
    return {
      changed,
      stray: per.filter((n) => n <= noise).reduce((s, n) => s + n, 0),
      top: rows.length ? rows[0] : null,
      bottom: rows.length ? rows[rows.length - 1] : null,
    };
  };

  const joint = s.poseJoint(id, state);
  const forward = shot(half);          // a quarter turn past a contact
  const back = shot(half + Math.PI);   // ...and the matching one half a cycle later
  const still = shot(half, null);      // the same instant with nothing cut
  // the same instant with the joint moved. Both are still cut and still swung,
  // so these are two pictures out of one code path; `still` is not, which is why
  // it is only measured and never diffed — a clip rasterises an outline a shade
  // differently from a plain drawImage, all round the dog.
  const lowered = shot(half, { hip: joint.hip + 0.06, pivot: joint.pivot });
  const moved = shot(half, { hip: joint.hip, pivot: joint.pivot + 0.25 });
  const b = box(still);
  return {
    joint,
    // the body the joint is a fraction of: `still` is the whole frame drawn at
    // this instant, and the artwork is cropped to its own outline, so its box is
    // the image rect on the canvas
    body: b,
    hipY: b.top + joint.hip * (b.bottom - b.top),
    opaque: (() => { let n = 0; for (let i = 3; i < still.length; i += 4) if (still[i] > 8) n++; return n; })(),
    swing: diff(forward, back, noise),
    hip: diff(forward, lowered, noise),
    pivot: diff(forward, moved, noise),
  };
}
"""


def test_every_posed_frame_is_cut_at_the_hip_or_declared_not_to_be():
    """The same shape as the grid guard above, for the joints (#244).

    `build_pose_joints.SWUNG` is the declaration — the states whose artwork is a
    stride — and pose-joints.json is what building from it produced. Deciding
    the subjects from the produced file meant a frame that stopped being cut
    stopped being tested; deciding them from the declaration means it stays a
    subject and this says the file disagrees. Both directions, because a joint
    written for a state nobody calls a stride is the same drift facing the other
    way.
    """
    assert POSED, "poses.json claims no frames at all, so this guards nothing"
    for cid, state in POSED:
        frame = POSES[cid][state][0]
        cut = frame in POSE_JOINTS
        declared = state in build_pose_joints.SWUNG
        assert cut == declared, (
            f"{cid}/{state} ({frame}) is "
            + ("declared a stride in build_pose_joints.SWUNG but has no joint in "
               "pose-joints.json — re-run scripts/build_pose_joints.py, and if it "
               f"still writes none, {cid} has no hip in that file's HIPS table"
               if declared else
               "cut at the hip in pose-joints.json, but no state it is drawn in is "
               "declared a stride — a swing on a drawing that has no stride phase "
               "under it is the #164 look coming back"))


@pytest.mark.parametrize("cid,state", SWUNG, ids=[f"{c}-{s}" for c, s in SWUNG])
def test_the_legs_swing_and_only_the_legs(desktop, cid, state):
    """#212: the run is a cycle now, made out of the one drawing there is.

    Two samples half a stride apart, at the *same* simulation time. That pair is
    chosen: at a quarter turn past a contact the whole-body terms of
    `frameMotion` — lift, squash and tilt — are identical either side of the
    half cycle, so the only thing that can differ between these two pictures is
    the legs. Anything the swing moved that is not a leg shows up as a
    disagreement above the hip line.

    A two-frame diff on its own would pass a frozen sprite, so the joint is
    perturbed as well: the same instant drawn with the hip moved down a
    sixteenth of the body, and again with the pivot slid a quarter of the way
    across it. Both have to change the picture, which is what says
    pose-joints.json is being read rather than being decoration next to a
    hardcoded cut.
    """
    r = desktop.evaluate(SWING_PROBE,
                         {"id": cid, "state": state, "size": 320, "t": 0.31,
                          "half": math.pi / 2, "noise": NOISE_ROW})
    body = r["body"]["bottom"] - r["body"]["top"]
    assert r["opaque"] > 1000, f"{cid}: nothing was drawn — {r}"
    assert body > 100, f"{cid}: the body is only {body}px tall — {r}"

    swing = r["swing"]
    assert swing["changed"] > r["opaque"] * 0.04, (
        f"{cid}: half a stride apart moved {swing['changed']}px of a {r['opaque']}px dog "
        f"— that is a twitch, not a cycle: {swing}")
    # ...and it moved the legs, and nothing else. The band carries a seam of
    # belly above the hip so a swing cannot open a gap there, and `drawPose`
    # clips that seam away again on the way out, so a stride cannot paint above
    # its own hip.
    ceiling = r["hipY"] - HIP_SLACK * body
    assert swing["top"] >= ceiling, (
        f"{cid}: the swing changed pixels at y={swing['top']}, above the hip at "
        f"y={r['hipY']:.0f} — something that is not a leg is being swung: {swing}")
    assert swing["bottom"] > r["hipY"], f"{cid}: nothing below the hip moved: {swing}"

    # both perturbations move the cut *down* or across, never up, so their
    # differences are bounded by the same hip line as the swing's
    for name, want in (("hip", "moving the hip down a sixteenth of the body"),
                       ("pivot", "sliding the pivot a quarter of the way across it")):
        d = r[name]
        assert d["changed"] > r["opaque"] * 0.01, (
            f"{cid}: {want} changed {d['changed']}px of a {r['opaque']}px dog — "
            f"pose-joints.json is not being read: {d}")
        assert d["top"] >= ceiling, (
            f"{cid}: {want} changed pixels at y={d['top']}, above the hip at "
            f"y={r['hipY']:.0f}: {d}")


# Same loading dance as SWING_PROBE, and the same `shot`. What differs is the
# measurement: not *which rows* moved but *which way* the leg mass went, which
# is the only thing that separates a leap from a run in mid-air (#219).
SWING_SIGN_PROBE = """
async ({ id, state, path, size, t, phase }) => {
  const s = await import('/js/sprites.js');
  const art = await s.loadArt();
  s.preload([id]);
  for (let i = 0; i < 100; i++) {
    if (s.artState().poseFrames.includes(path)) break;
    await new Promise((r) => setTimeout(r, 50));
  }
  const W = size, H = size + 90; // room below the feet: a swung foot reaches past them

  // One draw. `cut` false takes the joint out for the duration, which draws the
  // render whole — the legs exactly where the artist put them.
  const shot = (drawState, ph, cut) => {
    const held = art.poseJoints[path];
    if (!cut) delete art.poseJoints[path];
    const c = document.createElement('canvas');
    c.width = W; c.height = H;
    const ctx = c.getContext('2d');
    s.drawCharacter(ctx, id, W / 2, size, size * 0.9, null, t, drawState, 1, null, ph);
    if (held === undefined) delete art.poseJoints[path]; else art.poseJoints[path] = held;
    return ctx.getImageData(0, 0, W, H).data;
  };

  const box = (d) => {
    let top = null, bottom = null;
    for (let y = 0; y < H; y++) {
      for (let x = 0; x < W; x++) {
        if (d[(y * W + x) * 4 + 3] > 128) { if (top === null) top = y; bottom = y; break; }
      }
    }
    return { top, bottom };
  };

  // Where the leg mass sits, left to right. A centroid rather than an edge:
  // an edge is one pixel and moves with the rasteriser, and the swing carries
  // the whole band.
  const legs = (d, from) => {
    let sum = 0, n = 0;
    for (let y = Math.max(0, Math.round(from)); y < H; y++) {
      for (let x = 0; x < W; x++) {
        if (d[(y * W + x) * 4 + 3] > 128) { sum += x; n++; }
      }
    }
    return { x: n ? sum / n : null, n };
  };

  const joint = s.poseJoint(id, state);
  // Both pictures are the same state at the same instant, so lift, squash and
  // tilt are identical in them and cancel; what is left is the swing, plus the
  // seam the cut clips off the belly, which the caller removes.
  const measure = (drawState, ph) => {
    const swung = shot(drawState, ph, true);
    const whole = shot(drawState, ph, false);
    const b = box(whole);
    const hipY = b.top + joint.hip * (b.bottom - b.top);
    const from = hipY + 0.5 * (b.bottom - hipY);
    const a = legs(swung, from), o = legs(whole, from);
    return { hipY, from, body: b.bottom - b.top, n: o.n,
             dx: (a.x === null || o.x === null) ? null : a.x - o.x };
  };
  return { joint, path, air: measure(state, phase),
           run: measure('run', phase), back: measure('run', phase + Math.PI) };
}
"""

# How far apart the run's own two extremes have to sit before either can be a
# reference, as a fraction of the drawn body; and how much of one extreme the
# borrowed jump has to be worth, and how much nearer it has to sit to that one
# than to the other. All read off this probe (t=0.31, phase=pi/2, 320px, the
# lower half of the band): the run's extremes are 16.3px apart on a 247px Bandit
# and 4.6px on a 248px Chilli, and each borrowed jump lands 0.03-1.1px from the
# far one and 4.1-16.3px from the near one — at most 0.11 of the way back. Turn
# the swing off and the legs do not move from the drawing at all, which is 0.98
# (Bandit) to 2.8 (Chilli) of the way back, and swing them the run's way and
# they land on the near extreme, which is all of it.
MIN_TRAVEL = 0.008
MIN_SHARE = 0.5
# ...and how little of that far stretch a state whose swing is 0 may move, for
# the opposite question (#231). Off the same probe and the same instant: the
# borrowed cheer moves its legs 0.0px, and the mutation that puts the jump's
# swing on the cheer moves them 7.76px — 0.96 of Bandit's 8.07px far stretch,
# read off that mutation's own run. A tenth of the stretch is 0.81px: room for a
# rasteriser, none for a limb.
STILL_SHARE = 0.1


def test_there_is_a_borrowed_jump_to_measure():
    """The direction test below is parametrised, and an empty list of cases is a
    green run that asked nothing. Both halves can empty honestly — `frameMotion`
    could stop turning any state the other way, and Bandit and Chilli could each
    be drawn a leap of their own — but neither may happen quietly: delete the
    test and its mutations (`tests/mutations/sprites.json`) with the subject.
    """
    assert JUMP_SWUNG, (
        "no state in frameMotion carries `swing: JUMP_SWING` — either the switch no "
        "longer turns a borrowed run the other way, or the parse above stopped reading it")
    assert BORROWED_STRIDE, (
        f"no borrowed {'/'.join(JUMP_SWUNG)} lands on a render with a hip in "
        "pose-joints.json, so there is nothing left for the swing to move")


@pytest.mark.parametrize("cid,state", BORROWED_STRIDE,
                         ids=[f"{c}-{s}" for c, s in BORROWED_STRIDE])
def test_a_borrowed_jump_swings_the_legs_the_other_way(desktop, cid, state):
    """#219: Bandit and Chilli leap in their running drawing.

    The artwork's own jumps are stretched — the legs trail behind a body pitched
    forward — so a borrowed run is turned the other way (`JUMP_SWING`) to read as
    a leap instead of as a dog running through the air. Nothing asserted the
    sign: the swing tests only cover states with artwork of their own, and a
    borrowed state by definition has none, so `JUMP_SWING = LEG_SWING` passed.

    Each state is measured against *itself* drawn whole — the same instant, the
    same lift, squash and tilt, the joint taken out — so the difference is the
    swing alone and nothing else has to be held equal between a jump and a run.
    That difference is not symmetric about zero, though, and cannot be read as a
    signed distance from the drawing: the band rotates leg mass out of the rows
    being measured as well as sideways, so Chilli's +LEG_SWING moves her 1.2px
    one way where -LEG_SWING moves her 3.4px the other. The question is asked as
    a comparison instead. The run's two extremes are both measured, and the
    borrowed jump has to land on the *far* one — the legs where this stride puts
    them half a turn from now, not where it puts them at this instant.
    """
    r = desktop.evaluate(SWING_SIGN_PROBE,
                         {"id": cid, "state": state, "path": resolved_pose(cid, state),
                          "size": 320, "t": 0.31, "phase": math.pi / 2})
    air, run, back = r["air"], r["run"], r["back"]
    assert run["n"] > 300 and air["n"] > 300, f"{cid}: almost no leg was drawn — {r}"
    assert None not in (air["dx"], run["dx"], back["dx"]), f"{cid}: nothing below the hip — {r}"

    # A quarter turn past a contact is the run's fullest stretch, and half a turn
    # on is the other one, so these are the two places the stride puts the legs
    # at its whole size: +LEG_SWING and -LEG_SWING, with every other term of
    # `frameMotion` identical between them.
    travel = abs(run["dx"] - back["dx"])
    near, far = abs(air["dx"] - run["dx"]), abs(air["dx"] - back["dx"])
    assert travel > MIN_TRAVEL * run["body"], (
        f"{cid}: between its two extremes the run moved its legs {travel:.1f}px of a "
        f"{run['body']}px body — the reference this is measured against is not moving: {r}")
    assert abs(air["dx"]) > MIN_SHARE * abs(back["dx"]), (
        f"{cid}/{state}: the legs sit {air['dx']:.1f}px from where they were drawn, against "
        f"{back['dx']:.1f}px at the run's own far stretch — a borrowed run render with no "
        f"swing on it is a dog running in mid-air: {r}")
    assert far < MIN_SHARE * near, (
        f"{cid}/{state}: the legs sit {near:.1f}px from where this stride phase puts them "
        f"and {far:.1f}px from where the other one does — a leap has to read as the far "
        f"stretch, and this reads as the near one: that is the running drawing, airborne: {r}")


def test_there_is_a_borrowed_still_to_measure():
    """The same guard for the opposite decision. Both halves can empty honestly
    — `frameMotion` could stop leaving any state's legs where they were drawn,
    and Bandit could be drawn a cheer of his own — but neither may happen
    quietly: delete the test and its mutation with the subject.
    """
    assert STILL_SWUNG, (
        "no state in frameMotion carries `swing: 0` — either nothing leaves a borrowed "
        "drawing's legs alone any more, or the parse above stopped reading it")
    assert BORROWED_STILL, (
        f"no borrowed {'/'.join(STILL_SWUNG)} lands on a render with a hip in "
        "pose-joints.json, so there is nothing a swing could have moved and nothing "
        "to say it was left alone on purpose (#231)")


@pytest.mark.parametrize("cid,state", BORROWED_STILL,
                         ids=[f"{c}-{s}" for c, s in BORROWED_STILL])
def test_a_borrowed_cheer_keeps_the_legs_the_artist_drew(desktop, cid, state):
    """#231: Bandit celebrates in his running drawing, and stays in it.

    A cheer on a borrowed stride is the one place `swing: 0` reads as an unasked
    question rather than an answer, because the picture is a dog frozen
    mid-stride bobbing on the spot. It was asked: `swing` rotates the whole band
    below the hip, so it can point that spread somewhere else — which is what the
    jump above does — but it cannot close it, and the only other way out is the
    rig, which is #215 moved to the finish line. The comment on the case carries
    the reasoning; this is the half of it a future edit trips over.

    Measured the same way as the jump, against the same reference: the state's
    own drawing at the same instant with the joint taken out. Zero here means the
    legs are exactly where the artist put them, and the run's own two extremes
    are measured alongside so that "it did not move" cannot be satisfied by a
    probe that moves nothing.
    """
    r = desktop.evaluate(SWING_SIGN_PROBE,
                         {"id": cid, "state": state, "path": resolved_pose(cid, state),
                          "size": 320, "t": 0.31, "phase": math.pi / 2})
    still, run, back = r["air"], r["run"], r["back"]
    assert run["n"] > 300 and still["n"] > 300, f"{cid}: almost no leg was drawn — {r}"
    assert None not in (still["dx"], run["dx"], back["dx"]), f"{cid}: nothing below the hip — {r}"

    travel = abs(run["dx"] - back["dx"])
    assert travel > MIN_TRAVEL * run["body"], (
        f"{cid}: between its two extremes the run moved its legs {travel:.1f}px of a "
        f"{run['body']}px body — the reference this is measured against is not moving: {r}")
    assert abs(still["dx"]) < STILL_SHARE * abs(back["dx"]), (
        f"{cid}/{state}: the legs sit {still['dx']:.1f}px from where the artist drew "
        f"them, against {back['dx']:.1f}px at the run's own far stretch — a borrowed "
        f"cheer is not swung anywhere (#231): a stride cannot be brought to a stand by "
        f"rotating it, so the hop carries the celebration and the drawing is left alone")


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
#
# ...and measured on a burst that is the same burst every time. Every particle
# is built from `Math.random`: `r: 3 + Math.random() * 4` is a disc of 28 to 154
# pixels, so three unlucky rolls are a fifth of the picture three lucky ones
# would change. Unseeded, this number was a lottery — 60 samples of one case ran
# 149 to 486 — and it failed a ship at 114 (#222). The randomness is replaced
# for the length of the probe and put back afterwards, which makes `seen` a
# fixed number per (chapter, source) that a change to the artwork can move and
# nothing else can.
SEEN_PROBE = """
({ chapter, frames, seed, warm }) => {
    const g = window.game;
    document.getElementById('overlay').classList.add('hidden');
    const realRandom = Math.random;
    let s = seed >>> 0;
    Math.random = () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; };
    let out;
    try {
        g.start(chapter);
        const make = () => { /*MAKE*/ };
        const why = make();
        if (why) return { skipped: why };
        const had = g.particles.length;
        g.puff = () => {}; g.scuff = () => {}; g.sparkle = () => {};
        const c = g.ctx.canvas, w = c.width, h = c.height;
        let seen = 0, top = 0, alive = 0;
        for (let n = 0; n < frames && g.particles.length; n++) {
            alive++;
            g.render();
            // A warm-up pass draws every frame the measured pass will draw and
            // reads no pixels back. What it is for is the *asking*: a sprite is
            // requested at the moment it is first drawn, and nothing can arrive
            // inside the synchronous block below.
            if (warm) { g.step(1 / 60); continue; }
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
        out = warm ? { warmed: true } : { had, seen, top, alive };
    } finally {
        // Put the page's own randomness back before anything else: a seeded
        // Math.random left installed makes every later test on this page a
        // replay of this one.
        Math.random = realRandom;
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


SEED = 20260814  # any fixed value; see the sweep below the test

# Nothing still on its way. `pending` empties whether the image arrived or gave
# up, which is what this needs: the question is not "did it load" but "has the
# picture stopped changing", and a sprite that failed is as settled as one that
# decoded — the fallback dog it falls back to is drawn from then on.
ART_SETTLED = ("() => window.__art().pending.length === 0 "
               "&& window.__art().posePending.length === 0")


def art_settled(page, timeout=20000):
    """Block until this page has stopped fetching artwork.

    Every probe below that reads pixels back and compares them needs this, and
    the rule has one author on purpose (#224): a probe that measures a page
    mid-load is measuring how much of the page had arrived, and the failure is
    invisible against a local server — everything is decoded off the disk before
    the first test runs — so it surfaces as a ship failing on the deployed site.

    Where the wait goes is the part worth stating. It belongs immediately before
    the measurement, not on the fixture: a page that has settled once starts
    fetching again the moment something is drawn that was never drawn before, and
    a test that reloads (the slow-link ones) walks straight back out of any
    guarantee the fixture gave it. So:

    * a probe that only *draws* what boot already asked for waits here first;
    * a probe that asks for artwork *itself* — RIG_DIFF and SWING_PROBE both call
      `preload` for a character the gallery may never have shown — cannot use
      this, because the fetch it must wait for has not started when the test
      calls it. Those two wait inside the probe, for the file they asked for.
    """
    page.wait_for_function(ART_SETTLED, timeout=timeout)


def drawn_from(page, cid, *states):
    """Fail unless the drawings these states are measured through are on the page.

    `art_settled` only says nothing is still on its way, and a page whose sprites
    all 404'd is as settled as one that loaded. A crossfade probe would then be
    fading the procedural fallback dog into itself and passing — green, over
    nothing. This is the assert the `poseFrames.some(...)` waits it replaced were
    really making; a wait says it by timing out, which reports as the wrong thing.
    """
    art = page.evaluate("window.__art()")
    assert cid not in art["failed"], (
        f"{cid}'s own render never loaded ({art['failed']}), so what follows is "
        "measuring the fallback dog rather than the artwork")
    want = {f for f in (resolved_pose(cid, s) for s in states) if f}
    missing = sorted(want - set(art["poseFrames"]))
    assert not missing, (
        f"{cid} is drawn in {list(states)} from {sorted(want)}, and {missing} never "
        "arrived — the states this fades between are not the ones on the screen")


def particles_seen(page, chapter, source, frames=40, seed=SEED):
    """How many pixels one source's burst is worth on one chapter, at its best
    frame: `seen` pixels changed by more than a nudge, `top` the strongest
    channel difference anywhere in the picture.

    The same answer every time it is asked, for a given chapter, source and
    seed. Two things have to hold for that, and the second one is why the
    measurement is run twice:

    * the burst's randomness is seeded inside the probe, and
    * the page has stopped fetching its artwork. A sprite is requested at the
      moment it is first drawn and can only arrive between two evaluates, so
      the first pass throws its pixels away and exists to ask for everything
      the second pass will draw. Against a local server this changes nothing —
      everything is already decoded. Against the deployed site it is the whole
      difference: measured mid-load, `top` came back 69 rather than 71 and the
      reproducibility test below failed a ship (#222).
    """
    js = (SEEN_PROBE.replace("/*MAKE*/", PARTICLE_SOURCES[source]["make"])
                    .replace("NO_BALLOON", json.dumps(NO_BALLOON)))
    page.evaluate(js, {"chapter": chapter, "frames": frames, "seed": seed, "warm": True})
    art_settled(page)
    return page.evaluate(js, {"chapter": chapter, "frames": frames, "seed": seed})


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
        # Measured over all five chapters and the full life of each burst, at
        # the seed above, a particle is worth 70 pixels (the smallest: footfall
        # dust on Chapter 5's sand) to 232 (a landing puff, same chapter). 40
        # sits well under every one of them, and is the same bound the
        # single-chapter dust test this grew out of used.
        #
        # The seed is not a lucky one. Sweeping twelve seeds over all 30
        # chapter/source pairs, the worst any of them produced was 58 pixels a
        # particle, and for the footfall dust — the tightest case, and the one
        # that failed a ship — this seed is the *worst* of the twelve on three
        # chapters of five.
        assert r["seen"] >= 40 * r["had"], (
            f"{source}: {r['had']} particles changed only {r['seen']} pixels on chapter "
            f"{chapter} over {r['alive']} frames — drawn, and not visible against that "
            f"background. Skipped elsewhere: {skipped}")
        # and its strongest pixel by 65/255 at worst (dust on Chapter 5's sand)
        assert r["top"] >= 35, (
            f"{source}: the strongest pixel it changed on chapter {chapter} moved by "
            f"{r['top']}/255 — too faint to read as anything")


def test_the_visibility_probe_measures_the_same_burst_every_time(own_page):
    """The bound above is only a gate if the number under it is reproducible.

    It was not. Every particle's velocity and radius is a `Math.random`, and
    `r: 3 + Math.random() * 4` is a disc of 28 to 154 pixels — so three unlucky
    rolls in a row measure a fifth of what three lucky ones do. Sixty samples of
    one case (the footfall dust on Chapter 5) ran 149 to 486 pixels against a
    bound of 120, and on 2026-08-14 one of them came in at 114 and failed a
    ship for a burst nothing was wrong with (#222).

    So: same chapter, same source, same seed, same answer — three times, because
    two unseeded runs can coincide and three barely ever do. A different seed is
    asked for as well, since a probe that ignored the seed entirely would pass
    the first half of this and mean nothing.

    And the page's own randomness has to come back afterwards. A seeded
    generator left installed would make every later test on this page a replay
    of this one — and, worse, would keep this test passing while doing it. It is
    detectable because the seeded stream restarts at the same place on every
    probe: ask the page for a number after two identical runs and get the same
    one twice.
    """
    runs, after = [], []
    for _ in range(3):
        runs.append(particles_seen(own_page, 4, "the footfall dust"))
        after.append(own_page.evaluate("() => Math.random()"))
    got = [(r["seen"], r["top"], r["had"], r["alive"]) for r in runs]
    assert len(set(got)) == 1, (
        f"the same measurement, asked for three times, answered {got} — the burst "
        "is still random, so the bound it feeds is a lottery, not a gate")
    assert len(set(after)) == 3, (
        f"the page answered Math.random() with {after} after three identical probe "
        "runs — the probe's seeded generator is still installed, and every test "
        "after this one on this page is replaying its dice")

    other = particles_seen(own_page, 4, "the footfall dust", seed=1234)
    assert (other["seen"], other["top"]) != got[0][:2], (
        f"seed 1234 measured exactly what seed {SEED} did ({got[0][:2]}) — the "
        "probe is not reading the seed at all, and its determinism is luck")


# What the artwork is worth to the measurement, measured: the strongest pixel
# the burst changes moved by 2/255 between a page with its sprites and a page
# without them, and the pixel *count* did not move at all. 4 is that difference
# with room, and still 30 clear of the `top >= 35` bound the probe feeds.
ART_IS_WORTH = 4


def test_what_the_artwork_is_worth_to_the_visibility_measurement(own_page):
    """The other half of #222: how much of the number is the page rather than
    the burst.

    The failing measurement (114 pixels, against a bound of 120) was below the
    whole observed range of that case — 12 samples of it ran 190 to 493 — and
    the explanation offered was the page: the probe measures the burst as a
    *contrast* against the frame behind it, and nothing in it required the
    sprites to have decoded. Pale dust over the pale sand of a half-drawn
    Chapter 4 would clear the `d > 12` threshold far less often.

    Measured, that is only a quarter right, and this is the experiment kept:
    the same seeded measurement on a page holding all its artwork and on a page
    whose sprite requests are refused outright. `seen` — the number the
    40-per-particle bound reads — is *identical*, so the 114 was the dice and
    the seeding above is what answers it. `top` moves, by 2/255, and 2/255 is
    enough to fail an exact-equality check: measured against the deployed site,
    where the sprites arrive over a network rather than off the disk, that is
    precisely what happened. Hence the warm-up pass in `particles_seen`.

    The bound this bakes in is a ceiling, not a target: it says the page state
    can be worth 4/255 to the probe and no more. If the artwork ever starts
    carrying the measurement — dust drawn over a character rather than the
    ground — this is what says so, and the burst's visibility would then be a
    claim about which dog is standing there.
    """
    own_page.wait_for_function(
        "() => window.__art().loaded.length >= 5 && window.__art().pending.length === 0",
        timeout=30000)
    loaded = particles_seen(own_page, 4, "the footfall dust")

    # ...and the same page again with the pictures refused. Reloaded, because the
    # sprites are asked for during boot: a route installed after that blocks
    # nothing, and this test would then compare a loaded page with itself.
    own_page.route("**/assets/characters/*", lambda route: route.abort())
    own_page.reload(wait_until="domcontentloaded")
    own_page.wait_for_function("window.__ready === true", timeout=20000)
    own_page.wait_for_function("() => window.__art().failed.length >= 5", timeout=30000)
    art = own_page.evaluate("window.__art()")
    assert not art["loaded"], (
        f"the second measurement was supposed to be on a page with no artwork, and "
        f"{len(art['loaded'])} sprites decoded on it anyway ({art['loaded'][:5]}) — "
        "this test compared a loaded page with a loaded page")
    bare = particles_seen(own_page, 4, "the footfall dust")

    assert bare["seen"] == loaded["seen"], (
        f"the same seeded burst was worth {loaded['seen']} pixels over the artwork and "
        f"{bare['seen']} over the fallback dogs — the measurement the bound reads is a "
        "measurement of how much of the page had loaded, and a slow machine can fail a "
        "ship for a burst nothing is wrong with (#222)")
    assert abs(bare["top"] - loaded["top"]) <= ART_IS_WORTH, (
        f"the strongest pixel moved by {loaded['top']}/255 over the artwork and "
        f"{bare['top']}/255 without it — more than the {ART_IS_WORTH} this probe is "
        "allowed to owe to page state, so what it reports about a burst is partly a "
        "report about which dog had loaded")


# A connection with a lot of distance in it and not much room: enough that the
# sprites are still on their way when the engine says it is ready, which is the
# state the deployed site was measured in and a local server never reaches.
SLOW_LINK = {"offline": False, "latency": 800,
             "downloadThroughput": 200_000, "uploadThroughput": 200_000}


def test_the_visibility_probe_answers_the_same_on_a_page_still_loading(own_page):
    """The failure this reproduces only ever happened over a network.

    `test_..._same_burst_every_time` above failed against the deployed site
    while passing locally forever: the first of its three measurements answered
    `top` 69 and the other two 71. Locally the sprites are decoded off the disk
    before the first test runs, so there is no such thing as a page mid-load and
    nothing to catch. Here the link is slowed until there is one.

    What is asserted is `particles_seen`'s promise, not the flake: asked for a
    measurement on a page that is still fetching its artwork, it answers what
    the settled page answers. Take its warm-up pass away and the two differ,
    which is the mutation recorded against this test.
    """
    cdp = own_page.context.new_cdp_session(own_page)
    cdp.send("Network.enable")
    cdp.send("Network.emulateNetworkConditions", SLOW_LINK)
    # ...and fetched, not remembered: this page has already loaded once, and a
    # sprite served out of the browser's own cache never touches the slow link.
    cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})
    own_page.reload(wait_until="domcontentloaded")
    own_page.wait_for_function("window.__ready === true", timeout=60000)

    art = own_page.evaluate("window.__art()")
    assert art["pending"] or art["posePending"], (
        "the page had already finished fetching its artwork by the time the engine "
        f"was ready ({len(art['loaded'])} sprites, {len(art['poseFrames'])} pose "
        "frames), so this test measured a settled page twice and proves nothing — "
        "the link above is not slow enough to reach the state it is here for")
    mid = particles_seen(own_page, 4, "the footfall dust")

    own_page.wait_for_function(ART_SETTLED, timeout=60000)
    js = (SEEN_PROBE.replace("/*MAKE*/", PARTICLE_SOURCES["the footfall dust"]["make"])
                    .replace("NO_BALLOON", json.dumps(NO_BALLOON)))
    settled = own_page.evaluate(js, {"chapter": 4, "frames": 40, "seed": SEED})
    assert (mid["seen"], mid["top"]) == (settled["seen"], settled["top"]), (
        f"asked on a page still fetching its sprites the probe said {mid}, and the "
        f"same page once everything had arrived said {settled} — what it reports is "
        "partly how much of the page was there when it was asked, which is a ship "
        "failing on a burst nothing is wrong with (#222)")


# The particle call sites in the engine. Code only: a call named in a comment,
# or one commented out, is not a burst the player can see, and counting it would
# demand a case for something that never happens — game.js says in as many words
# that the cloud bounce makes no puff (#233). Module level so test_prose can
# check this parse against the naive one that reads the prose.
PARTICLE_SITES = collections.Counter(re.findall(
    r"this\.(puff|scuff|sparkle)\(",
    code_only((APP / "public" / "js" / "game.js").read_text())))


def test_every_particle_call_site_is_covered_by_a_visibility_case():
    """The point of the table above is that a new burst cannot be added without
    someone asking whether it can be seen. That only holds if the table is
    checked against the code: this counts the calls to each factory in the
    engine and fails when one of them has no case here.
    """
    sites = PARTICLE_SITES
    assert sites, (
        "no `this.puff/scuff/sparkle(` call anywhere in game.js — either the engine "
        "stopped making particles, or this parse stopped finding them, and an empty "
        "count would agree with an empty table instead of saying so")
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


@pytest.mark.parametrize("cid,to,how,share",
                         [("bluey", "jump", "pose", 0.35), ("bandit", "idle", "rig", 0.5)],
                         ids=["one drawing to another", "a drawing to the rig"])
def test_a_state_change_is_spread_over_several_frames(desktop, cid, to, how, share):
    """Landing used to be one frame: the drawing of a jump on one, the drawing of
    a run on the next, and — before #215 gave every state a render to fall back
    on — a swap from a pose to the cut-out rig as well. That swap is now only
    where nobody has artwork at all, which is standing still, so that is the
    second case here: a hand-drawn Bandit running to a rigged Bandit stopped.

    This is a bound on the picture rather than on `BLEND`: the same run is
    measured twice, once blended and once with the blend switched off, and the
    worst single frame of the blended one has to be a small share of the snap's.
    Frames differ anyway (a run bobs), so the yardstick is the change *over* the
    largest ordinary frame, and both are measured here rather than written down.
    """
    art_settled(desktop)
    drawn_from(desktop, cid, "run", to)
    dt, steps, at = 1 / 60, 40, 20 / 60
    runs = {}
    for hard in (False, True):
        runs[hard] = desktop.evaluate(BLEND_PROBE, {
            "id": cid, "dt": dt, "steps": steps, "changeAt": at,
            "from_": "run", "to": to, "hard": hard})
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
        f"snapping from run to {to} changed {max(snapped)} against {steady} for an "
        f"ordinary frame — too little for this test to be measuring anything")
    spike = max(moved) - steady
    worst = max(snapped) - steady
    # `share` is per case because the two fades are not the same shape. Between
    # two stills the change per frame is flat (measured 0.13 of a snap); fading
    # into the rig, which is animating underneath, it ramps and peaks on the last
    # frame of the fade (0.36). A snap is 1.0 by construction and halving BLEND
    # would put the ramping case around 0.7, so both bounds still separate.
    assert spike < share * worst, (
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
    art_settled(desktop)
    drawn_from(desktop, cid, "run", "jump")
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


def respawn(page, **kw):
    """Play a chapter into the water twice, eased and cut, and report both.

    Wrapped rather than called directly for the same reason `particles_seen` is:
    the probe plays the run through twice and asks whether the two drew the same
    picture — `apart[cut - 1] == 0`, an exact equality — and a sprite that
    decodes between the two plays makes the second one a different picture from
    frame zero. That window is nine seconds wide on a slow link and none at all
    off a local disk, which is the #222 shape exactly: green here, a failed ship
    there. Everything the probe draws was asked for at boot, so the settle wait
    is the whole fix (#224).
    """
    art_settled(page)
    return page.evaluate(RESPAWN_PROBE, {"dt": 1 / 60, "tail": 150, "hunt": 60 * 30, **kw})


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
    r = respawn(own_page, chapter=0, before=24, after=40, hurl=0)
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
    r = respawn(own_page, chapter=0, before=4, after=8, hurl=hurl)
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


def test_the_respawn_probe_answers_the_same_on_a_page_still_loading(own_page):
    """The second of the two probes that can be asked before its artwork is there.

    #222 found the first one by shipping it. This is the same question asked of
    the respawn probe instead, and the survey behind #224 says it is the one that
    needed it most: measured on a page nine seconds into a slow load, none of its
    pose frames had arrived and every frame-to-frame number came back a few
    percent off. The exact-equality assert in the test above — the two plays drew
    the same picture before the splash — is what a sprite landing between them
    breaks, and it can only land between them on a link with distance in it.

    What is asserted is `respawn`'s promise rather than the flake: asked on a
    page still fetching, it answers what the settled page answers. Take the
    settle wait out of `respawn` and the two differ, which is the mutation
    recorded against this test.
    """
    cdp = own_page.context.new_cdp_session(own_page)
    cdp.send("Network.enable")
    cdp.send("Network.emulateNetworkConditions", SLOW_LINK)
    cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})
    own_page.reload(wait_until="domcontentloaded")
    own_page.wait_for_function("window.__ready === true", timeout=60000)

    art = own_page.evaluate("window.__art()")
    assert art["pending"] or art["posePending"], (
        "the page had already finished fetching its artwork by the time the engine "
        f"was ready ({len(art['loaded'])} sprites, {len(art['poseFrames'])} pose "
        "frames), so this test measured a settled page twice and proves nothing")

    mid = respawn(own_page, chapter=0, before=24, after=40, hurl=0)

    art_settled(own_page, timeout=60000)
    settled = own_page.evaluate(RESPAWN_PROBE, {
        "dt": 1 / 60, "chapter": 0, "before": 24, "after": 40,
        "tail": 150, "hunt": 60 * 30, "hurl": 0})
    for key in ("moved", "snapped", "apart", "gaps", "cams", "onScreen"):
        assert mid[key] == settled[key], (
            f"asked on a page still fetching its sprites the probe reported {key} as "
            f"{mid[key][:6]}, and the same page once everything had arrived reported "
            f"{settled[key][:6]} — what it measures is partly how much of the page "
            "was there when it was asked (#224)")


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
#   painted  the lowest row the prop really paints anywhere in its footprint, so
#            a prop that is not in the picture cannot pass by having nothing to
#            measure — see `LOWEST_PAINT_JS` for why that is a band and not a column
#
# The props are taken out by the chapter's own rule — `horizon: null` ships none
# — so the bare picture is one the game could really draw, even though since
# #228 no chapter ships that way. Each prop is measured
# under the camera that brings it on screen, at a fixed t so the sea's sparkle
# and the clouds sit still, and `col` is where the 0.6 layer puts it on screen.
#
# The one probe here that needs no settle wait at all (#224): `renderBackground`
# paints sky, hills and props out of the canvas API and draws no image, so
# nothing it measures can arrive part way through.

# The reduction the scenery probes share: the lowest row, anywhere in a band of
# columns, where the picture with the props in it differs from the same picture
# without them. That band is the prop's *footprint*, and it is a band because a
# single column is a claim that the prop's lowest paint is under its middle
# (#223). An A-frame has nothing drawn under its middle: adding the hammerbarn
# stepladder, the centre column reported it painting down to 22px above the
# floor, because the lowest thing there was the top step. I fixed the art —
# a real stepladder has a spreader bar down on the floor — but the wrong answer
# came from the probe, and the next prop hollow at its middle gets it again.
#
# Widening can only move `painted` *down* the picture (a row is painted if any
# column in the band paints it), so the band cannot invent a grounded prop out
# of a floating one — unless it reaches a neighbour, which is what the spacing
# guard in the test below is for.
LOWEST_PAINT_JS = r"""
  const same = (a, b, i) => Math.abs(a[i] - b[i]) <= 6 && Math.abs(a[i + 1] - b[i + 1]) <= 6
                         && Math.abs(a[i + 2] - b[i + 2]) <= 6;
  const footprint = (ctx, col, half, W, H) => {
    const x0 = Math.max(0, col - half), x1 = Math.min(W - 1, col + half);
    return { x0, w: x1 - x0 + 1, px: ctx.getImageData(x0, 0, x1 - x0 + 1, H).data };
  };
  const lowestPaint = (wit, bare, H) => {
    let painted = null;
    for (let y = 0; y < H; y++) {
      for (let x = 0; x < wit.w; x++) {
        if (!same(wit.px, bare.px, (y * wit.w + x) * 4)) { painted = y; break; }
      }
    }
    return painted;
  };
"""

SCENERY_PROBE = r"""
async ({ clearRows, halfBand }) => {
  const c = await import('/js/chapters.js');
  const g = window.game;
  const W = c.WORLD_W, H = c.WORLD_H;
  const mk = () => { const cv = document.createElement('canvas');
                     cv.width = W; cv.height = H; return cv.getContext('2d'); };
  const withCtx = mk(), bareCtx = mk(), skyCtx = mk();
  const strip = (ctx, col) => ctx.getImageData(col, 0, 1, H).data;
""" + LOWEST_PAINT_JS + r"""
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
        bareAt(ch, camX, col);            // repaints bareCtx: this picture, props out
        const wit = footprint(withCtx, col, halfBand, W, H);
        const painted = lowestPaint(wit, footprint(bareCtx, col, halfBand, W, H), H);
        rows.push(Object.assign({ x: p.x, kind: p.kind, base: p.y, col, painted,
                                  cols: wit.w },
                                look(ch, p.y, camX, col)));
      }
      // the closest two props of this chapter, which is what the band may not
      // reach: `col` is the same screen column for every prop (the camera is
      // chosen to centre it), so a neighbour sits exactly its world gap away
      const xs = props.map((p) => p.x).sort((a, b) => a - b);
      const gap = xs.length < 2 ? null
                : Math.min(...xs.slice(1).map((v, i) => v - xs[i]));
      chapters.push({ id: ch.id, horizon: ch.horizon, props: props.length,
                      checked: rows.length, gap, rows });
    }
    // The two defects of #210, asked of the same pictures: a foot on the ground
    // line of a beach whose shore is 120px higher, and one on the ground line of
    // a chapter whose own surface is 88px above it. Plus a foot up in the dream
    // sky, which is what the second one *was* before #228 painted the cloud sea
    // under it. Nothing is drawn for these — the question is only what the
    // picture holds at that y, which is the whole question.
    const of = (id) => c.CHAPTERS.find((x) => x.id === id);
    return { chapters,
      consts: { GROUND_Y: c.GROUND_Y, CLOUD_TOP: c.CLOUD_TOP, halfBand },
      defects: {
        submerged: look(of('beach'), c.GROUND_Y, 300, 640),
        dreamGround: look(of('sleepytime'), c.GROUND_Y, 300, 640),
        dreamSky: look(of('sleepytime'), c.CLOUD_TOP - 200, 300, 640),
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
# How far either side of a prop's centre counts as the prop, measured off the
# nine kinds the game draws rather than guessed: the widest foot is the house's,
# which paints the floor from -76 to +75 of its middle, and the narrowest are the
# trunks — a gum touches down over -6..+5. 80 reaches the ground under all of
# them. The ceiling is the other side: the closest two props in the game are the
# creek's trees, 340px apart, and a band that reached a neighbour would let one
# prop's foot vouch for another's. The test asserts that gap rather than trusting
# this comment, because the spacing lives in `sceneryFor` and can change.
HALF_BAND = 80
# How far the bottom of a prop's footprint may sit from the foot it stands on:
# 2 rows of antialiasing above it, and 10 below for a foot settled into its
# surface. Named rather than written into the assertion because the proof that
# the band is doing anything (`test_a_prop_hollow_at_its_middle_...`) has to
# reject and accept by the suite's own rule, not by a copy of the numbers.
FOOT_ABOVE, FOOT_BELOW = 2, 10


def foot_is_planted(delta):
    """Is a prop whose footprint bottoms out `delta` rows below its foot on it?"""
    return -FOOT_ABOVE <= delta <= FOOT_BELOW


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
    r = own_page.evaluate(SCENERY_PROBE, {"clearRows": CLEAR_ROWS, "halfBand": HALF_BAND})
    chapters = r["chapters"]
    assert len(chapters) == 5, f"only {len(chapters)} chapters were measured"

    # The band is a prop's footprint only while it stops short of the next prop.
    # Nothing above would notice if it did not: a neighbour's foot is on the
    # ground too, so an overlapping band would report every floating prop as
    # standing. Asked of `sceneryFor`'s own spacing, so packing a chapter tighter
    # fails here rather than quietly widening what counts as one prop.
    for ch in chapters:
        if ch["gap"] is None:
            continue
        assert 2 * HALF_BAND < ch["gap"], (
            f"{ch['id']}: its closest two props are {ch['gap']}px apart and the "
            f"footprint band is {2 * HALF_BAND + 1} columns wide — the band reaches "
            "the next prop, so one prop's foot can stand in for its neighbour's")

    for ch in chapters:
        assert ch["checked"] == ch["props"], (
            f"{ch['id']}: {ch['checked']} of {ch['props']} props were measured")
        for p in ch["rows"]:
            why = scenery_complaint(p)
            assert why is None, f"{ch['id']}: the {p['kind']} at x={p['x']} — {why}"
            assert p["painted"] is not None, (
                f"{ch['id']}: the {p['kind']} at x={p['x']} paints nothing anywhere in "
                f"the {p['cols']} columns around x={p['col']} — this prop was not "
                "measured, it was missed")
            assert foot_is_planted(p["painted"] - p["base"]), (
                f"{ch['id']}: the {p['kind']} at x={p['x']} stands on y={p['base']} but "
                f"its footprint paints down to y={p['painted']} — the art is not on "
                "its own foot")

    # The band is wide enough to be the prop and narrow enough to be only it, but
    # nothing above can tell — every prop the game draws today touches down under
    # its own middle, so a one-column probe and this one agree on all of them.
    # That is why the band's proof is a separate test, on a shape that does not.
    #
    # what all that measured: a sceneryFor that returned nothing would pass every
    # line above without ever looking at a prop
    total = sum(c["checked"] for c in chapters)
    dressed = [c["id"] for c in chapters if c["checked"]]
    assert total >= 30 and len(dressed) >= 3, (
        f"only {total} props over {dressed} — there is not enough scenery here for "
        "this to have tested anything")
    bare = sorted(c["id"] for c in chapters if not c["checked"])
    assert bare == [], (
        f"{bare} ship no scenery at all — every chapter has a middle distance since "
        "#228, and one that has quietly lost its trees is a regression, not a "
        "decision")
    # hammerbarn was in that list until #213 and sleepytime until #228, both for
    # the same reason: a far layer, a player, and nothing in between. Hammerbarn
    # declared a horizon and stood nothing on it; sleepytime declared none at all,
    # because the dream sky painted no surface anywhere for anything to stand on.
    # Both were closed by painting the floor first — the shop floor, and the cloud
    # sea — and these two counts are what stops either sliding back.
    shop = next(c for c in chapters if c["id"] == "hammerbarn")
    assert shop["checked"] >= 6, (
        f"hammerbarn is down to {shop['checked']} props — the chapter it was "
        "before #213 is one with nothing between the shelving and the player")
    sleepy = next(c for c in chapters if c["id"] == "sleepytime")
    assert sleepy["checked"] >= 6, (
        f"sleepytime is down to {sleepy['checked']} props — the chapter it was "
        "before #228 is 5400px of dream with an empty middle distance")
    # Every chapter's middle distance is dressed in its own biome (#229). The
    # beach shipped `kind: "tree"` — the creek's round oak with a greener leaf —
    # for as long as this chapter existed, and nothing above can tell: an oak and
    # a palm both stand perfectly well on the shoreline. This is the claim that
    # was being made and never checked.
    shared = {}
    for ch in chapters:
        for kind in {p["kind"] for p in ch["rows"]}:
            shared.setdefault(kind, []).append(ch["id"])
    borrowed = {k: v for k, v in shared.items() if len(v) > 1}
    assert borrowed == {}, (
        f"{borrowed} — a prop standing in two chapters' middle distances means one "
        "of them is dressed in the other's biome, which is what put a round shade "
        "tree on a tropical shoreline until #229")

    assert sleepy["horizon"] == r["consts"]["CLOUD_TOP"] < r["consts"]["GROUND_Y"], (
        f"the dream stands its planets on y={sleepy['horizon']}, which is not the top "
        f"of the cloud sea ({r['consts']['CLOUD_TOP']}) — a horizon on the ground line "
        "is what ran #210's trunks down through the floating platforms")

    # the same rule, run against the placements it was written for: all three were
    # in a shipped picture at some point, and all three have to come back as
    # complaints. `dreamGround` is #210's own placement and reads differently since
    # #228 — a foot on the ground line of the dream is now 88px *inside* the cloud
    # sea rather than hanging over nothing, which is a complaint either way.
    for name, want in [("submerged", "in the surface"),
                       ("dreamGround", "in the surface"),
                       ("dreamSky", "off the surface")]:
        why = scenery_complaint(r["defects"][name])
        assert why and want in why, (
            f"a foot placed the way the {name} prop was placed came back as "
            f"{why!r} — this check no longer catches the thing it is here for")

    # And the branch none of those three can reach any more: nothing whatever in
    # the column. No shipped chapter can produce it since #228, because all five
    # now paint a surface in every column — that is what the issue was. It is
    # still the branch that caught sleepytime's trunks in #210, so it is asked
    # here directly rather than left to a picture that cannot ask it.
    why = scenery_complaint({"clear": True, "edge": None, "delta": None,
                             "base": r["consts"]["GROUND_Y"]})
    assert why and "in mid air" in why, (
        f"a foot with no surface anywhere in its column came back as {why!r}")


# The footprint band's proof, and the only place in this file it can be had.
#
# Every prop the game ships touches down under its own middle, so narrowing the
# band back to one column changes not one number in the test above — the widening
# would be free to rot from the day it was written. The shape it is for is the
# A-frame, and the A-frame is not in the art any more: #213 fixed the wrong
# answer in the drawing, by giving the stepladder the spreader bar a real one has.
#
# So this takes the real `drawStepLadder` and paints its middle back out at floor
# level, which is what that ladder was, and asks the suite's own reduction for the
# prop's lowest paint twice — over one column, and over the band. The hole is the
# only difference between the two ladders, and the whole one is measured as well
# so that a reduction which simply disagreed with itself could not pass this.
HOLLOW_FOOT_PROBE = r"""
async ({ halfBand, holeHalf, holeTop, holeBottom }) => {
  const art = await import('/js/art.js');
  const W = 400, H = 400, base = 300, cx = 200, scale = 1.05, SKY = '#BFE3F2';
  const mk = () => { const cv = document.createElement('canvas');
                     cv.width = W; cv.height = H;
                     const ctx = cv.getContext('2d');
                     ctx.fillStyle = SKY; ctx.fillRect(0, 0, W, H); return ctx; };
""" + LOWEST_PAINT_JS + r"""
  const nowt = mk();                       // the picture with no ladder in it
  const whole = mk(); art.drawStepLadder(whole, cx, base, scale);
  const hollow = mk(); art.drawStepLadder(hollow, cx, base, scale);
  hollow.fillStyle = SKY;                  // the spreader bar taken back out
  hollow.fillRect(cx - holeHalf, base - holeTop, holeHalf * 2, holeTop + holeBottom);
  const low = (ctx, half) => {
    const y = lowestPaint(footprint(ctx, cx, half, W, H),
                          footprint(nowt, cx, half, W, H), H);
    return y === null ? null : y - base;
  };
  return { whole: { column: low(whole, 0), band: low(whole, halfBand) },
           hollow: { column: low(hollow, 0), band: low(hollow, halfBand) } };
}
"""


def test_a_prop_hollow_at_its_middle_is_measured_across_its_footprint(own_page):
    """A stepladder standing on the floor is standing on it, whichever column you
    ask — unless you ask the one column an A has nothing in (#223).

    Its own page: it borrows `art.js` to draw off screen.
    """
    r = own_page.evaluate(HOLLOW_FOOT_PROBE,
                          {"halfBand": HALF_BAND, "holeHalf": 13,
                           "holeTop": 22, "holeBottom": 18})
    whole, hollow = r["whole"], r["hollow"]

    assert whole["column"] is not None and whole["column"] == whole["band"], (
        f"the ladder as it ships measures {whole['column']} through its middle and "
        f"{whole['band']} across its footprint — those have to agree before a "
        "disagreement below can be blamed on the hole rather than on the two reads")
    assert foot_is_planted(whole["band"]), (
        f"the ladder as it ships bottoms out {whole['band']} rows from its foot, "
        "which this suite would already be failing — nothing below means anything")

    assert foot_is_planted(hollow["band"]), (
        f"with its spreader bar gone the ladder's footprint bottoms out "
        f"{hollow['band']} rows from its foot — the band did not find the legs, so "
        "widening it has not fixed anything")
    assert not foot_is_planted(hollow["column"]), (
        f"the same ladder measured through its middle bottoms out "
        f"{hollow['column']} rows from its foot and this suite calls that planted — "
        "the hole is not deep enough to reproduce #213, so nothing here is proving "
        "the band is what saved it")


# The kind assertion above is a fact about `sceneryFor`'s data, and the data is
# only half of it: `renderBackground`'s switch decides what a kind is *drawn*
# with, and pointing "palm" at `drawTree` would put the oak straight back on the
# beach with every kind still unique. So this asks the picture.
#
# What separates a palm from every other prop in this game is the sky it has in
# the middle of it. A canopy, a cloud tower, a stack of pallets are solid things
# with at most a few px of shadow between their parts; a palm is blades radiating
# out of a crown, so a row through it crosses blade, a lot of sky, blade. The
# measurement is therefore the widest hole *inside* a prop's own silhouette, and
# it is the drawing's "no canopy blob" written down as a number.
#
# One prop per kind, not per chapter: the kinds repeat, and every other kind in
# the game is the oracle here. Measured today the palm's hole is 37px and the
# next widest of the other eight is the dream planet's 23 (the gap between its
# ring and its body); the round tree it replaced is 10-17 depending on scale. A
# probe that had quietly started counting antialiasing as sky would not leave
# that spread, which is what makes the palm's number mean anything.
FROND_PROBE = r"""
async ({ span, minPaint }) => {
  const c = await import('/js/chapters.js');
  const g = window.game;
  const W = c.WORLD_W, H = c.WORLD_H;
  const mk = () => { const cv = document.createElement('canvas');
                     cv.width = W; cv.height = H; return cv.getContext('2d'); };
  const withCtx = mk(), bareCtx = mk();
  const same = (a, b, i) => Math.abs(a[i] - b[i]) <= 6 && Math.abs(a[i + 1] - b[i + 1]) <= 6
                         && Math.abs(a[i + 2] - b[i + 2]) <= 6;
  const held = g.ch;
  try {
    const out = [], seen = new Set();
    for (const ch of c.CHAPTERS) {
      for (const p of c.sceneryFor(ch)) {
        if (seen.has(p.kind)) continue;
        seen.add(p.kind);
        const camX = Math.max(0, (p.x - W / 2) / 0.6);
        const col = Math.round(p.x - camX * 0.6);
        const x0 = Math.max(0, col - span), x1 = Math.min(W, col + span), w = x1 - x0;
        withCtx.clearRect(0, 0, W, H); g.ch = ch; g.t = 0; g.renderBackground(withCtx, camX);
        const heldH = ch.horizon; ch.horizon = null;  // the same view with no props in it
        bareCtx.clearRect(0, 0, W, H); g.ch = ch; g.t = 0; g.renderBackground(bareCtx, camX);
        ch.horizon = heldH;
        const wit = withCtx.getImageData(x0, 0, w, H).data;
        const bare = bareCtx.getImageData(x0, 0, w, H).data;
        let hole = { gap: 0, y: null, painted: 0, runs: 0 }, widest = 0;
        for (let y = 0; y < H; y++) {
          let painted = 0, runs = 0, prev = false, started = false, gap = 0, run = 0;
          for (let x = 0; x < w; x++) {
            const on = !same(wit, bare, (y * w + x) * 4);
            if (on) {                       // a hole only counts between two blades,
              painted++;                    // so it is closed by the paint after it
              if (!prev) runs++;
              if (started && run > gap) gap = run;
              started = true; run = 0;
            } else if (started) run++;
            prev = on;
          }
          if (painted > widest) widest = painted;
          // only rows with some substance to them: two stray tips either side of the
          // whole prop is not a hole in it
          if (painted >= minPaint && gap > hole.gap) hole = { gap, y, painted, runs };
        }
        out.push({ id: ch.id, kind: p.kind, x: p.x, col, widestRow: widest,
                   gap: hole.gap, atY: hole.y, paintedThere: hole.painted,
                   runsThere: hole.runs });
      }
    }
    return out;
  } finally { g.ch = held; }
}
"""

# What the beach's palm has to clear, and what every other prop has to stay under.
# The measured numbers are 37 for the palm and 23 for the widest of the rest, so
# neither bound is closer than 6px to the thing it is separating — and the shape
# this is here to keep off the shoreline, the round tree, comes in at 10-17.
PALM_HOLE = 30
SOLID_PROP_HOLE = 25


def test_the_beach_is_dressed_in_palms_and_nothing_else_in_the_game_is(own_page):
    """The shoreline's props are blades with sky between them, not a canopy (#229).

    Its own page: it borrows the engine to draw backgrounds off screen.
    """
    props = own_page.evaluate(FROND_PROBE, {"span": 90, "minPaint": 20})
    kinds = {p["kind"]: p for p in props}
    assert len(kinds) == len(props) and len(kinds) >= 8, (
        f"{sorted(kinds)} — one prop per kind was measured and there are fewer here "
        "than the game ships, so some kind went unasked")

    assert "palm" in kinds, (
        f"no chapter ships a palm; the middle distances are dressed in {sorted(kinds)}")
    palm = kinds["palm"]
    assert palm["id"] == "beach", f"the palm is standing in {palm['id']}, not on the beach"
    assert palm["gap"] >= PALM_HOLE, (
        f"the widest hole inside the beach's palm is {palm['gap']}px at y={palm['atY']} "
        f"({palm['runsThere']} pieces there) — under {PALM_HOLE}px this is a solid "
        "shape with a shadow in it, i.e. the round shade tree the beach shipped "
        "until #229, not a crown of fronds")

    solid = {k: v["gap"] for k, v in kinds.items() if k != "palm"}
    assert max(solid.values()) <= SOLID_PROP_HOLE, (
        f"{ {k: g for k, g in solid.items() if g > SOLID_PROP_HOLE} } — every other prop "
        "in the game is a solid thing, so a hole this wide in one of them means this "
        "probe is measuring something other than sky between blades, and the palm's "
        f"{palm['gap']}px says nothing")

    # and that they are all really in the picture: a prop that painted nothing comes
    # back with no holes at all, which reads exactly like a solid one
    thin = {k: v["widestRow"] for k, v in kinds.items() if v["widestRow"] < 30}
    assert thin == {}, (
        f"{thin} — these are barely in the picture at their widest row, so nothing "
        "above measured them")


# The palm's first draft got each frond's width by bowing its control points
# vertically, which is fine for the four fronds that lie over sideways and wrong
# for the one that points up: the offset falls along that blade's own axis and it
# comes out as a 1px spike out of the crown. It looked like a hair on the lens.
#
# That defect is invisible to everything above. At the 0.8 the shoreline is drawn
# at, the difference between a blade and a spike is 5px of paint against 1 — under
# the 6-per-channel tolerance the picture probes compare colours with, so it is
# noise there however it is asked. A shape is scale-free, though, so this asks the
# same question of the same function at a size where the answer is not: 18px
# against 2. Nothing else in the game is drawn at this scale, and nothing needs to
# be — what is under test is the drawing, not the shoreline.
PALM_CROWN_PROBE = r"""
async ({ scale, below }) => {
  const a = await import('/js/art.js');
  const W = 1100, H = 900, foot = 860;
  const cv = document.createElement('canvas'); cv.width = W; cv.height = H;
  const ctx = cv.getContext('2d');
  ctx.fillStyle = "#000000"; ctx.fillRect(0, 0, W, H);
  a.drawPalm(ctx, W / 2, foot, scale);
  const d = ctx.getImageData(0, 0, W, H).data;
  const widest = (y) => {                    // the longest unbroken run in this row
    let best = 0, run = 0;
    for (let x = 0; x < W; x++) {
      const i = (y * W + x) * 4;
      if (d[i] > 10 || d[i + 1] > 10 || d[i + 2] > 10) { run++; if (run > best) best = run; }
      else run = 0;
    }
    return best;
  };
  let top = null, bottom = null;
  for (let y = 0; y < H; y++) if (widest(y) > 0) { if (top === null) top = y; bottom = y; }
  return { top, bottom, foot, blade: top === null ? 0 : widest(top + below) };
}
"""


def test_the_palms_upright_frond_is_a_blade_and_not_a_spike(own_page):
    """Every frond has width, including the one pointing straight up (#229)."""
    r = own_page.evaluate(PALM_CROWN_PROBE, {"scale": 5, "below": 20})

    assert r["top"] is not None and r["top"] >= 1, (
        f"the palm's topmost paint is at y={r['top']} — it is drawn off the top of "
        "this canvas, so the crown measured below is a cropped one")
    assert r["bottom"] >= r["foot"], (
        f"the palm's lowest paint is at y={r['bottom']}, above its own foot at "
        f"y={r['foot']} — it is not standing on the line it was given")
    assert r["blade"] >= 12, (
        f"20 rows below its tip the palm's topmost frond is {r['blade']}px across. "
        "A frond that gets its width from the vertical instead of from the "
        "perpendicular to its own axis collapses to a spike exactly here — this is "
        "18px when every frond has a width and 2px when the upright one does not")


# --- the beach: sand with the sea on one side -------------------------------

# Chapter 4 was built like the creek — 430-650px slabs of sand with a 440px gap
# between them — and `renderLevel` fills every gap with the chapter's water down
# to the bottom of the world. At 440px, nearly half the 960-wide view, the water
# was the picture and the sand was stepping stones in it (#214).
#
# So this reads the foreground strip itself. The level is drawn into an offscreen
# canvas one view at a time, camera stepping by exactly WORLD_W so the columns
# stitch into one continuous row of the world, and each column below the ground
# line is sand, water, or nothing. Two numbers come out: how much of the beach is
# sand, and the widest unbroken water — a pool you can see across, or a channel.
#
# One row is enough because the strip is uniform: everything below GROUND_Y+6 is
# either the water fill or a platform that runs to the bottom of the world. The
# probe returns the same numbers at +20, +40 and +70; +40 is clear of the
# platforms' lighter top lip and of the water's surface sparkle.
BEACH_PROBE = r"""
async ({ row }) => {
  const c = await import('/js/chapters.js');
  const g = window.game;
  const W = c.WORLD_W;
  const cv = document.createElement('canvas'); cv.width = W; cv.height = c.WORLD_H;
  const ctx = cv.getContext('2d');
  const hex = (s) => [parseInt(s.slice(1, 3), 16), parseInt(s.slice(3, 5), 16),
                      parseInt(s.slice(5, 7), 16)];
  const near = (d, i, c3) => Math.abs(d[i] - c3[0]) <= 26 && Math.abs(d[i + 1] - c3[1]) <= 26
                          && Math.abs(d[i + 2] - c3[2]) <= 26;
  // the level as #214 found it, so the same verdict can be asked about it
  const asFound = (ch) => {
    const rng = c.makeRng(1000 + 3 * 7919);
    const plats = [{ x: -200, w: 1200, y: c.GROUND_Y }];
    let x = 980;
    while (x < ch.length - 400) {
      const w = 430 + Math.floor(rng() * 220);
      plats.push({ x, w, y: c.GROUND_Y });
      plats.push({ x: x + w + 120, w: 210, y: c.GROUND_Y - 120 });
      x += w + 120 + 210 + 110;
    }
    plats.push({ x, w: 700, y: c.GROUND_Y });
    return { plats, tokens: [], obstacles: [], secret: { x: -9999, y: 0, taken: true } };
  };
  const scan = (ch, level) => {
    const water = hex(ch.water);
    const held = [g.ch, g.level, g.t];
    g.ch = ch; g.level = level; g.t = 0;
    let sand = 0, cols = 0, run = 0, worst = 0;
    for (let camX = 0; camX < ch.length; camX += W) {
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, W, c.WORLD_H);
      ctx.save(); ctx.translate(-camX, 0); g.renderLevel(ctx, camX); ctx.restore();
      const d = ctx.getImageData(0, row, W, 1).data;
      for (let x = 0; x < W; x++) {
        const i = x * 4;
        cols++;
        if (d[i + 3] >= 20 && !near(d, i, water)) { sand++; run = 0; }
        else { run++; if (run > worst) worst = run; }
      }
    }
    [g.ch, g.level, g.t] = held;
    return { cols, sand, share: sand / cols, worst };
  };
  // the engine draws the player and its particles into this layer, so they are
  // taken out of the way rather than counted as scenery
  g.start(3); g.stop();
  g.player.x = -99999; g.particles.length = 0; g.toasts.length = 0; g.balloon = null;
  const of = (id) => c.CHAPTERS.find((x) => x.id === id);
  return {
    poolMin: c.POOL_MIN,
    view: W,
    beach: scan(of('beach'), c.buildLevel(3)),
    creek: scan(of('creek'), c.buildLevel(1)),
    asFound: scan(of('beach'), asFound(of('beach'))),
  };
}
"""
# Measured: the beach is 85.5% sand with a widest pool of 171px; as #214 found it
# it was 60.7% and 440px. The floor sits between the two and nearer the defect,
# because the numbers that matter are the ones a level change would drift toward.
BEACH_SAND = 0.75
# and a gap stays a pool: POOL_MIN plus the 40px of jitter build() adds, plus
# room for the roundRect corners the fill eats into at either side.
POOL_SLACK = 60


def beach_complaint(m, pool_max, view):
    """Why this foreground does not read as a beach, or None if it does.

    One function for the shipped level and for the level #214 was filed about,
    so a rule that has stopped meaning anything fails on the defect first.
    """
    if m["share"] < BEACH_SAND:
        return (f"only {m['share']:.0%} of the ground line is sand (want "
                f"{BEACH_SAND:.0%}) — that is water with sand in it")
    if m["worst"] > pool_max:
        return (f"its widest unbroken water is {m['worst']}px of a {view}px view "
                f"(want {pool_max}) — that is a channel, not a rock pool")
    return None


def test_the_beach_is_sand_with_the_sea_on_one_side(own_page):
    """Chapter 4's sand was a handful of slabs with water filling every gap, so
    the player crossed it like stepping stones in a lake (#214). The sea belongs
    behind the beach — that is the far shoreline at SEA_TOP, which #210 put the
    palms on — and the gaps in front of it are rock pools.

    Its own page: it borrows the engine to draw levels off screen.
    """
    r = own_page.evaluate(BEACH_PROBE, {"row": 492})
    pool_max, view = r["poolMin"] + POOL_SLACK, r["view"]
    beach = r["beach"]

    assert beach["cols"] >= 6000, (
        f"only {beach['cols']} columns were read — the whole 6200px beach should have "
        f"been walked, so this measured a corner of it, not the chapter")
    why = beach_complaint(beach, pool_max, view)
    assert why is None, f"ch4 does not read as a beach: {why}"

    # the same rule against the level #214 was filed about, which has to come
    # back as a complaint about both halves of it
    was = beach_complaint(r["asFound"], pool_max, view)
    assert was and "water with sand in it" in was, (
        f"the level as #214 found it came back as {was!r} — this check no longer "
        f"catches the thing it is here for")
    assert r["asFound"]["worst"] > pool_max, (
        f"#214's 440px gaps measured {r['asFound']['worst']}px here, which this "
        f"check would now allow")

    # and it is a rule about a beach, not about water: the creek is stepping
    # stones on purpose and must not be quietly held to it
    assert beach_complaint(r["creek"], pool_max, view), (
        f"the creek passes the beach's own rule ({r['creek']['share']:.0%} sand, "
        f"widest water {r['creek']['worst']}px) — then the rule says nothing about "
        f"the difference between a beach and a creek, which is all it is for")


def test_no_console_errors_on_desktop(desktop):
    """Last, so it covers everything the tests above did."""
    assert not desktop.errors, str(desktop.errors[:3])


# --- phones: the only way it is really played -------------------------------

def test_the_menu_fits_without_scrolling(phone):
    assert phone.evaluate("document.body.scrollHeight <= window.innerHeight + 2")


# --- the rotate pill: said once, then out of the way (#252) ------------------

# The three rects #252 was measured from. Read as the player sees them: a node
# with `hidden` on it, or with no box, is not on the screen whatever its
# geometry says — `getBoundingClientRect` on a `display:none` pill is all zeros,
# which would otherwise read as "at the top-left corner, overlapping nothing".
BOTTOM_OF_THE_SCREEN = """
() => {
  const r = (id) => {
    const n = document.getElementById(id);
    const b = n.getBoundingClientRect();
    return { top: b.top, bottom: b.bottom, left: b.left, right: b.right,
             shown: !n.closest('.hidden') && b.width > 0 && b.height > 0 };
  };
  return { hint: r('rotate-hint'), bar: r('hud-progress'), dog: r('hud-progress-dog') };
}
"""


def overlap(a, b):
    """Area two rects share, in CSS pixels.

    Pure geometry. A caller holding a node that might not be on the screen has
    to ask ``shown`` itself: a ``display:none`` node's rect is all zeros, and
    zeros overlap nothing — a pass, for a reason that has nothing to do with
    what was being checked.
    """
    wide = min(a["right"], b["right"]) - max(a["left"], b["left"])
    tall = min(a["bottom"], b["bottom"]) - max(a["top"], b["top"])
    return max(0, wide) * max(0, tall)


def test_the_rotate_pill_says_it_once_and_then_goes_away(make_page):
    """It is a nudge, not a status bar. #252 found it up for the whole session."""
    page = make_page(IPHONE, touch=True)
    try:
        ms = page.evaluate("() => import('/js/main.js').then((m) => m.HINT_MS)")
        assert page.locator("#rotate-hint").is_visible(), (
            "a phone held upright never gets told to turn it sideways")
        page.wait_for_timeout(ms + 1000)
        assert not page.locator("#rotate-hint").is_visible(), (
            f"the pill is still up {(ms + 1000) / 1000:.0f}s after the page loaded")
    finally:
        page.context.close()


def test_the_rotate_pill_never_covers_the_progress_bar(make_page):
    """#252's measurement: hint y 779..836 *containing* bar y 820..832."""
    page = make_page(IPHONE, touch=True)
    try:
        assert page.locator("#rotate-hint").is_visible(), (
            "the pill is already gone before the chapter starts, so this test "
            "would pass without the game doing anything about it")
        page.click("#btn-play")
        page.wait_for_timeout(400)
        assert page.evaluate("window.game.mode") == "playing"

        seen = page.evaluate(BOTTOM_OF_THE_SCREEN)
        assert seen["bar"]["shown"] and seen["dog"]["shown"], (
            f"the progress bar is not on screen during a chapter: {seen}")
        for part in ("bar", "dog"):
            covered = overlap(seen["hint"], seen[part]) if seen["hint"]["shown"] else 0
            assert covered == 0, (
                f"the pill covers {covered:.0f}px2 of "
                f"#hud-progress{'' if part == 'bar' else '-dog'}: pill "
                f"y {seen['hint']['top']:.0f}..{seen['hint']['bottom']:.0f}, "
                f"it y {seen[part]['top']:.0f}..{seen[part]['bottom']:.0f}")
    finally:
        page.evaluate("() => window.game && window.game.stop()")
        page.context.close()


# --- and the class it belongs to: nothing sits on anything else (#258) -------

# Screens the check is run on. The two phones are the shapes it is really for;
# the short-and-wide one is a laptop with the browser chrome taking half the
# window, which is where a bottom-anchored thing and a top-anchored thing first
# meet.
SCREENS = [
    (IPHONE, True, "phone upright"),
    ({"width": 844, "height": 390}, True, "phone sideways"),
    (DESKTOP, False, "laptop"),
    ({"width": 1024, "height": 420}, False, "laptop, short window"),
]

# A parametrised id is how one case is named on a command line, so it is worth
# keeping readable: `pytest 'test_game.py::test_x[phone sideways]'`. These were
# hyphenated for a while because `mutate --prove-with` split what it was given on
# whitespace and re-ran half an id (#270); it carries a list now, so the names
# the failures use and the names the command line uses are the same again.
SCREEN_IDS = [name for _, _, name in SCREENS]

# The longest thing the game ever says, taken from the engine rather than
# retyped: a toast is furniture too, and the widest one is the one that would
# reach the pills. The interpolated ones (`${c.name} says g'day!`) are not
# collected — a name is not known here.
TOASTS = re.findall(r'this\.toast\("([^"]+)"\)', (APP / "public" / "js" / "game.js").read_text())

# What a player sees as one *thing*: something that paints its own background,
# or a leaf with text in it. The walk stops at the first one, so a compound
# widget is a single box — #hud-progress owns the fill and the paw marker
# riding it, and a marker on its own bar is not two things on top of each
# other. #game is the world (everything is over it, on purpose) and #overlay is
# a full-screen screen (it is meant to cover the lot), so the walk skips both.
FURNITURE = """
(rootSelector) => {
  const alphaOf = (colour) => {
    const parts = colour.match(/[\\d.]+/g) || [];
    return parts.length > 3 ? parseFloat(parts[3]) : 1;
  };
  // Every rect here is where the layout put the thing, which is not always
  // where a player can see it: inside a scrolling box, what is past the edge of
  // the box is not on the screen at all. So each item carries `vis` as well —
  // its rect cut down to whatever its scrolling ancestors let through — and a
  // rule about two things being in the same place has to use that one (#269).
  const clipOf = (node, clip) => {
    const cs = getComputedStyle(node);
    if (!/hidden|auto|scroll|clip/.test(cs.overflowX + ' ' + cs.overflowY)) return clip;
    const r = node.getBoundingClientRect();
    return { left: Math.max(clip.left, r.left), right: Math.min(clip.right, r.right),
             top: Math.max(clip.top, r.top), bottom: Math.min(clip.bottom, r.bottom) };
  };
  const cut = (b, clip) => {
    const box = { left: Math.max(clip.left, b.left), right: Math.min(clip.right, b.right),
                  top: Math.max(clip.top, b.top), bottom: Math.min(clip.bottom, b.bottom) };
    box.width = Math.max(0, box.right - box.left);
    box.height = Math.max(0, box.bottom - box.top);
    return box;
  };
  const seen = [];
  const walk = (node, clip) => {
    for (const kid of node.children) {
      if (kid.id === 'game' || kid.id === 'overlay') continue;
      const cs = getComputedStyle(kid);
      if (cs.display === 'none' || cs.visibility === 'hidden') continue;
      if (parseFloat(cs.opacity) < 0.05) continue;
      const b = kid.getBoundingClientRect();
      if (b.width < 1 || b.height < 1) continue;
      const paints = alphaOf(cs.backgroundColor) > 0.01;
      const leafText = kid.children.length === 0 && kid.textContent.trim() !== '';
      if (paints || leafText) {
        seen.push({
          what: kid.id || kid.className || kid.tagName.toLowerCase(),
          text: kid.textContent.trim().slice(0, 20),
          tag: kid.tagName.toLowerCase(),
          classes: typeof kid.className === 'string' ? kid.className : '',
          tappable: kid.tagName === 'BUTTON' || cs.cursor === 'pointer',
          left: b.left, right: b.right, top: b.top, bottom: b.bottom,
          width: b.width, height: b.height,
          vis: cut(b, clip),
        });
      } else {
        walk(kid, clipOf(kid, clip));
      }
    }
  };
  const root = document.querySelector(rootSelector);
  if (!root) throw new Error('nothing matched ' + rootSelector + ', so there is nothing to measure');
  const far = 1e6;
  walk(root, clipOf(root, { left: -far, right: far, top: -far, bottom: far }));
  return { seen, view: { w: window.innerWidth, h: window.innerHeight } };
}
"""

def on_screen(seen):
    """The things out of a walk that a player can actually see right now.

    Something scrolled past the edge of a scrolling box still has a rect, and
    that rect can sit exactly where a pinned button is — but it is not on the
    screen, and two things are only on top of each other if both of them are.
    """
    return [item for item in seen if item["vis"]["width"] > 0.5 and item["vis"]["height"] > 0.5]


# 44 CSS px is the tap target in both Apple's HIG and WCAG 2.5.5 — a five year
# old aiming at a pause button is exactly who that number is for.
TAP_TARGET = 44

# A pair that is allowed to overlap says why here, by the two names the walk
# reports. Empty on purpose: today nothing on this screen is meant to cover
# anything else, and an exemption added later should have to be argued for.
ALLOWED_TO_OVERLAP: dict[frozenset, str] = {}


@pytest.mark.parametrize("viewport,touch,screen", SCREENS, ids=SCREEN_IDS)
def test_nothing_on_the_hud_sits_on_top_of_anything_else(make_page, viewport, touch, screen):
    """The class #252 belongs to: chrome covering the thing it sits next to.

    #252 was the rotate pill completely containing the progress bar on a phone
    — every rect was there at runtime and nobody was subtracting them. So this
    asks the page what is on it, rather than naming the parts: anything added
    later is measured without someone remembering to come back here.

    It runs mid-chapter, which is the crowded screen and the one #252 was
    about. The menus are their own thing: an overlay is a screen, and covering
    what is behind it is its whole job.
    """
    assert TOASTS, ("no toasts found in game.js — this measures a screen with "
                    "the game's own words on it, so a rename there must not "
                    "quietly turn that half off")
    page = make_page(viewport, touch=touch)
    try:
        page.click("#btn-play")
        page.wait_for_timeout(400)
        assert page.evaluate("window.game.mode") == "playing", (
            f"{screen}: never got into a chapter, so there is no HUD to measure")
        page.evaluate("(text) => window.game.onEvent({ type: 'toast', text })",
                      max(TOASTS, key=len))
        page.wait_for_timeout(300)

        found = page.evaluate(FURNITURE, "#stage")
        seen, view = found["seen"], found["view"]
        names = sorted(item["what"] for item in seen)
        assert len(seen) >= 5, (
            f"{screen}: only {len(seen)} things found on a screen that has a "
            f"score, a token count, mute, pause and a progress bar: {names}")

        for a, b in itertools.combinations(on_screen(seen), 2):
            why = ALLOWED_TO_OVERLAP.get(frozenset((a["what"], b["what"])))
            area = overlap(a["vis"], b["vis"])
            assert area == 0 or why, (
                f"{screen}: {a['what']} covers {area:.0f}px2 of {b['what']} — "
                f"{a['what']} y {a['top']:.0f}..{a['bottom']:.0f} x "
                f"{a['left']:.0f}..{a['right']:.0f}, {b['what']} y "
                f"{b['top']:.0f}..{b['bottom']:.0f} x {b['left']:.0f}..{b['right']:.0f}")

        for item in seen:
            assert (item["left"] >= -0.5 and item["top"] >= -0.5
                    and item["right"] <= view["w"] + 0.5
                    and item["bottom"] <= view["h"] + 0.5), (
                f"{screen}: {item['what']} runs off a {view['w']}x{view['h']} "
                f"screen — x {item['left']:.0f}..{item['right']:.0f}, "
                f"y {item['top']:.0f}..{item['bottom']:.0f}")

        for item in seen:
            if not item["tappable"]:
                continue
            assert min(item["width"], item["height"]) >= TAP_TARGET, (
                f"{screen}: {item['what']} ({item['text']!r}) is "
                f"{item['width']:.0f}x{item['height']:.0f} — under the "
                f"{TAP_TARGET}px a small finger needs to hit it")
    finally:
        page.evaluate("() => window.game && window.game.stop()")
        page.context.close()


# --- and the screens all the words are on (#266) ------------------------------

# Rooted at the panel rather than the stage. An overlay covering the game is its
# whole job, so a check run over the stage has to exempt it — root the walk
# inside the panel and "the screen covers the game" is true by construction,
# leaving the three failures that are really about the screen: things inside it
# on top of each other, the panel running off a short window, and the buttons a
# player presses first being too small to hit.
PANEL = "#overlay .panel"

# The floor under the walk. Every one of these screens is a heading, some words
# and a way back; finding fewer than four things means the walk went wrong, and
# then every rule below it is being asked about nothing (#258).
MIN_THINGS = 4

# Text links inside a sentence, exempt from the 44px floor and saying why.
# WCAG 2.5.5 exempts a target that is "in a sentence or block of text" for
# exactly this reason: the only way to make an inline link 44px tall is to stop
# it being inline, and the sentence is what makes it make sense.
INLINE_TEXT_LINKS = {
    "a": "a link inside a sentence — the credits list and the artwork sources",
    "link-btn": "'About & credits →' is the last line of the menu's fine print",
}


def inline_text_link(item) -> str:
    """Why this tappable thing is exempt from the 44px floor, or "".

    Matched on the tag and the class, not on the id: `btn-credits` and
    `btn-back` are ids given to particular buttons, and an exemption keyed on
    one of those exempts *that button*, not the kind of thing it is. The next
    inline link would be added silently.
    """
    tokens = {item["tag"], *item["classes"].split()}
    return next((why for token, why in INLINE_TEXT_LINKS.items() if token in tokens), "")

# A screen allowed to be taller than the window, and why. Anything not named
# here has to fit: a panel that overflows without meaning to is the one that
# hides its own Play button. The named ones still have to be *reachable* —
# scrolled to the bottom, the lowest thing on them is on screen.
MAY_SCROLL = {
    "about & credits": "five links, a takedown offer and the full non-affiliation "
                       "notice — it is a page of small print by design",
    "characters": "one card per character, and the cast only grows",
    "a character's bio": "a portrait, a personality, a fun fact and the artwork source",
    "chapter select": "one card per chapter",
    "the story card": "three paragraphs and a joke, read out loud — it is a page "
                      "of story by design, and since #255 nothing is behind it",
    "results": "a stats table, the outro and four buttons",
    "stats": "a row per chapter",
}


def overlay_screens(page):
    """Walk the overlay screens the way a player reaches them, naming each.

    A generator rather than a table of selectors because getting to a screen *is*
    the navigation — the story card is behind the menu's story link, the results
    are behind a chapter played to the end — and a table would either duplicate that or hide
    it behind a lambda. It yields between screens, so the test measures each one
    while it is up and a failure names it.
    """
    page.wait_for_selector("#btn-play")
    yield "menu"

    page.click("#btn-chapters")
    page.wait_for_selector(".chapter-card")
    yield "chapter select"

    page.click("#btn-back")
    page.wait_for_selector("#btn-gallery")
    page.click("#btn-gallery")
    page.wait_for_selector(".char-card")
    yield "characters"

    page.click(".char-card")
    page.wait_for_selector("#btn-menu")
    yield "a character's bio"

    page.click("#btn-menu")
    page.wait_for_selector("#btn-stats")
    page.click("#btn-stats")
    page.wait_for_selector("table.stats")
    yield "stats"

    page.click("#btn-back")
    page.wait_for_selector("#btn-credits")
    page.click("#btn-credits")
    page.wait_for_selector(".credits-body")
    yield "about & credits"

    page.click("#btn-back")
    page.wait_for_selector("#btn-story")
    page.click("#btn-story")
    page.wait_for_selector("#btn-go")
    yield "the story card"

    page.click("#btn-go")
    page.wait_for_timeout(400)
    page.click("#btn-pause")
    page.wait_for_selector("#btn-resume")
    yield "paused"

    page.click("#btn-resume")
    play_chapter(page, 0)
    page.wait_for_selector(".stars-big")
    yield "results"


# What actually scrolls. A panel is a scrolling body with the screen's buttons
# pinned under it, and the panel itself is capped at the window and hides its
# overflow — so "is this screen taller than the window" is a question about the
# body, and asking the panel would answer 0 forever (#269).
SCROLLER = "#overlay .panel > .panel-body"

PANEL_FITS = """
(sel) => {
  const p = document.querySelector(sel);
  if (!p) throw new Error('nothing matched ' + sel + ', so nothing here scrolls');
  const r = p.getBoundingClientRect();
  return { over: p.scrollHeight - p.clientHeight, bottom: r.bottom,
           canScroll: /auto|scroll/.test(getComputedStyle(p).overflowY) };
}
"""


@pytest.mark.parametrize("viewport,touch,screen", SCREENS, ids=SCREEN_IDS)
def test_nothing_on_a_menu_sits_on_top_of_anything_else(make_page, viewport, touch, screen):
    """#258 for the screens with the words on them, which is nearly all of them.

    The HUD is five things; the menus are the story, the chapter cards, the
    character bios, the stats table, the credits and every button a player
    presses before they ever see the game. Same walk, same three rules, rooted
    at the panel — plus the one rule a panel has that the HUD does not: it has
    to either fit the window or be scrollable to the bottom of itself.
    """
    page = make_page(viewport, touch=touch)
    measured, scrolled = [], []
    try:
        for name in overlay_screens(page):
            found = page.evaluate(FURNITURE, PANEL)
            seen, view = found["seen"], found["view"]
            where = f"{screen}, {name}"
            names = sorted(item["what"] for item in seen)
            assert len(seen) >= MIN_THINGS, (
                f"{where}: only {len(seen)} things found on a screen that has a "
                f"heading, some words and a way back: {names}")
            assert any(item["tappable"] for item in seen), (
                f"{where}: nothing tappable found, so the tap-target rule below "
                f"is being asked about nothing: {names}")

            for a, b in itertools.combinations(on_screen(seen), 2):
                why = ALLOWED_TO_OVERLAP.get(frozenset((a["what"], b["what"])))
                area = overlap(a["vis"], b["vis"])
                assert area == 0 or why, (
                    f"{where}: {a['what']} ({a['text']!r}) covers {area:.0f}px2 of "
                    f"{b['what']} ({b['text']!r}) — the parts of them on the screen "
                    f"are {a['what']} y {a['vis']['top']:.0f}..{a['vis']['bottom']:.0f} x "
                    f"{a['vis']['left']:.0f}..{a['vis']['right']:.0f} and "
                    f"{b['what']} y {b['vis']['top']:.0f}..{b['vis']['bottom']:.0f} x "
                    f"{b['vis']['left']:.0f}..{b['vis']['right']:.0f}")

            for item in seen:
                assert item["left"] >= -0.5 and item["right"] <= view["w"] + 0.5, (
                    f"{where}: {item['what']} ({item['text']!r}) runs off the side of a "
                    f"{view['w']}x{view['h']} screen — x "
                    f"{item['left']:.0f}..{item['right']:.0f}. Nothing here scrolls "
                    f"sideways, so this is off the edge for good")

            for item in seen:
                if item["tappable"] and not inline_text_link(item):
                    assert min(item["width"], item["height"]) >= TAP_TARGET, (
                        f"{where}: {item['what']} ({item['text']!r}) is "
                        f"{item['width']:.0f}x{item['height']:.0f} — under the "
                        f"{TAP_TARGET}px a small finger needs to hit it")

            fit = page.evaluate(PANEL_FITS, SCROLLER)
            if fit["over"] > 1:
                assert name in MAY_SCROLL, (
                    f"{where}: the panel is {fit['over']:.0f}px taller than the "
                    f"{view['h']}px window and this screen is not one of the ones "
                    f"that may scroll — whatever is at the bottom of it (the Play "
                    f"button, the way back) is off the screen")
                assert fit["canScroll"], (
                    f"{where}: the panel overflows by {fit['over']:.0f}px and cannot "
                    f"be scrolled, so the bottom of it cannot be reached at all")
                scrolled.append(name)
                lowest = max(seen, key=lambda i: i["bottom"])
                page.evaluate("(sel) => { const p = document.querySelector(sel); "
                              "p.scrollTop = p.scrollHeight; }", SCROLLER)
                page.wait_for_timeout(120)
                bottom = page.evaluate(FURNITURE, SCROLLER)
                last = max(bottom["seen"], key=lambda i: i["bottom"])
                # against the bottom of the scrolling part, not the bottom of the
                # window: the buttons are pinned below it, so measuring the window
                # would pass for anything the body could possibly show (#269)
                assert last["bottom"] <= fit["bottom"] + 0.5, (
                    f"{where}: scrolled all the way down, {last['what']} "
                    f"({last['text']!r}) still ends at y {last['bottom']:.0f} and the "
                    f"scrolling part of the panel ends at y {fit['bottom']:.0f} on a "
                    f"{view['h']}px screen — the bottom of this panel cannot be "
                    f"reached (it was {lowest['what']} before scrolling)")
            else:
                for item in seen:
                    assert item["top"] >= -0.5 and item["bottom"] <= view["h"] + 0.5, (
                        f"{where}: the panel fits, but {item['what']} "
                        f"({item['text']!r}) is at y {item['top']:.0f}.."
                        f"{item['bottom']:.0f} on a {view['h']}px screen")
            measured.append(name)
    finally:
        page.evaluate("() => window.game && window.game.stop()")
        page.context.close()
    # the itinerary is a generator: a click that silently stopped working ends
    # it early, and a check that measured three screens would otherwise pass
    assert len(measured) == 9, f"only measured {measured}"
    # and the scrolling half is a branch, so it can quietly stop running: the
    # gallery is taller than every window here, and if one day nothing overflows
    # then MAY_SCROLL and the reachability rule are decoration
    assert scrolled, ("no screen overflowed its window, so nothing checked that a "
                      f"panel taller than the screen can be scrolled to the bottom "
                      f"({screen}, {len(measured)} screens)")


# --- and the way out of every one of them (#269) ------------------------------

# The block of buttons that ends a screen, wherever `showOverlay` has put it.
ACTIONS = "#overlay .panel .actions"

ACTION_BLOCK = """
(sels) => {
  const a = document.querySelector(sels.actions);
  if (!a) return null;
  const body = document.querySelector(sels.body);
  const box = (n) => {
    const r = n.getBoundingClientRect();
    return { what: n.id || n.className || n.tagName.toLowerCase(),
             text: n.textContent.trim().slice(0, 24),
             top: r.top, bottom: r.bottom, left: r.left, right: r.right,
             width: r.width, height: r.height };
  };
  const panel = document.querySelector(sels.panel);
  const high = (n) => (n ? n.getBoundingClientRect().height : 0);
  return {
    block: box(a),
    buttons: [...a.querySelectorAll('button')].map(box),
    // `closest` walks up: true means the block is still inside the part that
    // scrolls, which is exactly the arrangement #269 was about
    inTheScrollingPart: a.closest(sels.body) !== null,
    over: body ? body.scrollHeight - body.clientHeight : 0,
    scrollTop: body ? body.scrollTop : 0,
    // the three heights the split is made of: what a player reads through, what
    // the pinning costs, and the whole thing it is divided out of (#274)
    bodyHigh: high(body), footHigh: high(panel && panel.querySelector(':scope > .panel-foot')),
    panelHigh: high(panel),
    view: { w: window.innerWidth, h: window.innerHeight },
  };
}
"""

# The share of the panel that has to be left for reading through. Pinning the
# buttons is paid for out of the body (#269): put four buttons and a paragraph of
# fine print under a list and the list is read through a slot. Half is not a
# guess about taste — it is the point where the screen is more chrome than screen,
# and the worst real case today is the results on a 844x390 phone at 224/367 =
# 61%, so there is room before an honest layout trips it.
PEEPHOLE = 0.5


def peephole(m) -> str:
    """Why this screen is being read through a slot, or "".

    Only asked of a screen with something hidden below the fold. A menu is
    mostly buttons and that is what a menu is: measured across 9 screens x 4
    viewports, every screen whose foot outweighs its body has nothing hidden at
    all (`over` 0), so a bare `body >= foot` would fail eight screens a player
    can see all of. What cannot be right is a screen that *does* continue past
    the fold and gives the continuation less than half the panel (#274).
    """
    if m["over"] <= 1 or m["panelHigh"] <= 0:
        return ""
    share = m["bodyHigh"] / m["panelHigh"]
    if share >= PEEPHOLE:
        return ""
    return (f"{m['bodyHigh']:.0f}px of a {m['panelHigh']:.0f}px panel is left to read "
            f"through ({share:.0%}, floor {PEEPHOLE:.0%}) with {m['over']:.0f}px still "
            f"below the fold — the buttons pinned under it take {m['footHigh']:.0f}px")


@pytest.mark.parametrize("viewport,touch,screen", SCREENS, ids=SCREEN_IDS)
def test_the_buttons_that_end_a_screen_are_on_the_screen(make_page, viewport, touch, screen):
    """#269: on a phone held sideways, the way out was below the fold.

    Sideways is the way this game asks to be held, and on a 844x390 window the
    characters screen ran 535px past the bottom of it, credits 372, results 180.
    Nothing said so: the panel scrolled its own insides, so the document was
    never taller than the window, and the only route to "← Back" was a scroll
    gesture on a panel that gives no sign it scrolls.

    So this asks what a player asks on arriving at a screen, before touching
    anything: is the block of buttons that ends it — the way forward and the way
    back — actually on the screen? It is the same nine screens as the check
    above, and the only one it lets off is none of them.
    """
    page = make_page(viewport, touch=touch)
    measured, needed_it = [], []
    try:
        for name in overlay_screens(page):
            where = f"{screen}, {name}"
            found = page.evaluate(ACTION_BLOCK,
                                  {"actions": ACTIONS, "body": SCROLLER, "panel": PANEL})
            assert found, (
                f"{where}: no block of buttons on this screen at all — every screen "
                f"ends in one, and a screen that has lost its way out is what this "
                f"is here to notice")
            view, buttons = found["view"], found["buttons"]
            assert buttons, (
                f"{where}: the block is there but has no buttons in it ({found['block']}), "
                f"so the rule below is being asked about nothing")
            assert not found["inTheScrollingPart"], (
                f"{where}: the block of buttons is inside the part of the panel that "
                f"scrolls, so a screen taller than the window takes it with it — that "
                f"is #269 back again")
            assert found["scrollTop"] == 0, (
                f"{where}: the panel was already scrolled by {found['scrollTop']:.0f}px "
                f"before anything was measured, so this is not what a player arrives at")

            for b in buttons:
                assert b["top"] >= -0.5 and b["bottom"] <= view["h"] + 0.5, (
                    f"{where}: {b['what']} ({b['text']!r}) is at y {b['top']:.0f}.."
                    f"{b['bottom']:.0f} on a {view['h']}px screen — below the fold on "
                    f"arrival, and nothing on the screen says to scroll for it")
            if found["over"] > 1:
                needed_it.append(f"{name} (+{found['over']:.0f}px)")
                assert not (slot := peephole(found)), (
                    f"{where}: {slot}. Pinning the way out is worth doing, but not at "
                    f"this price — what is left of the screen is a slot")
            measured.append(name)
    finally:
        page.evaluate("() => window.game && window.game.stop()")
        page.context.close()
    assert len(measured) == 9, f"only measured {measured}"
    # a screen shorter than the window would pass this without the pinning doing
    # anything, so at least one has to have been the hard case — and the same
    # screens are the only ones the peephole rule is asked about, so an empty
    # list means that rule ran nowhere either
    assert needed_it, (
        f"no screen on {screen} was taller than its window ({len(measured)} measured), "
        f"so nothing here checked what happens when one is")


def test_the_peephole_rule_notices_a_screen_squeezed_to_a_slot(make_page):
    """Prove the rule above can go red, by building the failure it is about.

    No screen ships in that state — the tightest is 61% of the panel — so left
    alone the peephole rule is a line nothing in the app can currently falsify,
    which reads exactly like a rule that works. So the state is built here on the
    real page: the characters screen on a phone held sideways has 543px below the
    fold, and a fat block dropped into the pinned foot is what a fourth button and
    a paragraph of fine print would do to it (#274).
    """
    page = make_page({"width": 844, "height": 390}, touch=True)
    try:
        page.wait_for_selector("#btn-gallery")
        page.click("#btn-gallery")
        page.wait_for_selector(".char-card")
        sels = {"actions": ACTIONS, "body": SCROLLER, "panel": PANEL}

        before = page.evaluate(ACTION_BLOCK, sels)
        assert before["over"] > 1, (
            f"the characters screen fits this window ({before['over']:.0f}px over), so "
            f"the rule would not be asked about it either way and this drill proves "
            f"nothing")
        assert not peephole(before), (
            f"the drill starts from a screen that already fails: {peephole(before)}")

        page.evaluate(
            """(sel) => {
              const fat = document.createElement('div');
              fat.style.height = '250px';
              document.querySelector(sel).appendChild(fat);
            }""", f"{PANEL} > .panel-foot")
        page.wait_for_timeout(120)

        after = page.evaluate(ACTION_BLOCK, sels)
        assert after["over"] > 1, (
            f"the squeezed screen stopped having anything below the fold "
            f"({after['over']:.0f}px), so it is not the failure this is about")
        assert (said := peephole(after)), (
            f"250px of pinned block took the body from {before['bodyHigh']:.0f}px to "
            f"{after['bodyHigh']:.0f}px of a {after['panelHigh']:.0f}px panel and the "
            f"rule said nothing")
        assert f"{PEEPHOLE:.0%}" in said and "below the fold" in said, (
            f"the complaint does not say what the floor was or what is hidden: {said}")
    finally:
        page.context.close()


# --- and the pill that talks over the way out (#277) --------------------------

HINT = "#rotate-hint"

# Rooted at the panel, so this is "a button pinned under this screen" rather than
# "a button": the pill is allowed to be over the game, and during a chapter there
# is no panel at all.
FOOT_BUTTONS = "#overlay .panel > .panel-foot button"

HINT_BOX = """
(sels) => {
  const hint = document.querySelector(sels.hint);
  if (!hint) return null;
  const cs = getComputedStyle(hint);
  const box = (n) => {
    const b = n.getBoundingClientRect();
    return { what: n.id || n.className || n.tagName.toLowerCase(),
             text: n.textContent.trim().slice(0, 24),
             top: b.top, bottom: b.bottom, left: b.left, right: b.right,
             width: b.width, height: b.height };
  };
  const panel = document.querySelector(sels.panel);
  return {
    // `hidden` is what takes it away, and a node with no box is not on the
    // screen whatever its rect says (#252)
    shown: cs.display !== 'none' && cs.visibility !== 'hidden'
           && hint.getBoundingClientRect().width > 1,
    // `closest` walks up: false means it is loose on the stage again, anchored
    // to a window whose bottom now belongs to the panel
    inTheFoot: hint.closest(sels.foot) !== null,
    hint: box(hint),
    buttons: [...document.querySelectorAll(sels.buttons)].map(box),
    panel: panel ? box(panel) : null,
    view: { w: window.innerWidth, h: window.innerHeight },
  };
}
"""


HINT_SELS = {"hint": HINT, "foot": f"{PANEL} > .panel-foot",
             "buttons": FOOT_BUTTONS, "panel": PANEL}

# Forced up rather than waited for: the pill takes itself away after six seconds,
# and a check that waited would measure whichever screens it happened to reach
# first. It says so if there is no pill to raise — `null.classList` is a stack
# trace about the test, and the thing it would be saying is a real failure.
SHOW_HINT = """
(sel) => {
  const n = document.querySelector(sel);
  if (!n) throw new Error('nothing matched ' + sel + ', so there is no pill to raise');
  n.classList.remove('hidden');
}
"""


def hint_shows_itself(page, viewport) -> bool:
    """Does the game put the pill up by itself, with nothing forced?

    The pill takes itself away after `HINT_MS`, so simply looking would be
    racing a six second timer that started when the page loaded. Instead this
    asks for the state the game shows it in — a phone *arriving* in portrait —
    by leaving the shape of the window and coming back to it, which is the
    event the orientation watcher listens for. A window that is already
    landscape leaves portrait on the way back and is told to put the pill away,
    which is the other half of the same rule.
    """
    w, h = viewport["width"], viewport["height"]
    for size in ({"width": h, "height": w}, {"width": w, "height": h}):
        page.set_viewport_size(size)
        # the window is the size it was asked for before the page has been told
        # so — a fixed wait here reads the answer to the *previous* shape on a
        # busy machine, and the pill is exactly what is mid-change
        page.wait_for_function(f"() => window.innerWidth === {size['width']}")
        # and `orientationchange` is answered 250ms late on purpose, so a window
        # that has finished resizing still has one verdict about the pill to
        # come — measure before it and the *next* thing this test does to the
        # pill gets undone underneath it
        page.wait_for_timeout(500)
    return page.evaluate(HINT_BOX, HINT_SELS)["shown"]


@pytest.mark.parametrize("viewport,touch,screen", SCREENS, ids=SCREEN_IDS)
def test_the_rotate_pill_never_lands_on_the_way_out(make_page, viewport, touch, screen):
    """#277: "Turn your phone sideways" drawn across "← Back".

    The pill was `bottom: 8px` on the stage, from before #269 pinned the way out
    to the bottom of the panel and the panel grew to the bottom of the window.
    On a 390x844 phone the characters screen then had 32px of a 56px "← Back"
    under a translucent black pill for the first six seconds — the six seconds a
    player is looking for the way out.

    So every screen is asked with the pill up, at every window: does it touch a
    button pinned under this screen, and is all of it somewhere a player can
    see? It is forced up rather than waited for, because the timer would decide
    which screens got measured; that the game raises it on its own, and only on
    a phone held upright, is the last two assertions.
    """
    page = make_page(viewport, touch=touch)
    upright = viewport["height"] > viewport["width"] * 1.1
    measured, by_itself = [], None
    try:
        for name in overlay_screens(page):
            where = f"{screen}, {name}"
            if by_itself is None:
                by_itself = hint_shows_itself(page, viewport)
            # is it there at all, before anything is done to it: every screen is
            # built from scratch, and a pill thrown away by one of them cannot be
            # taken off the screen it is covering either
            assert page.evaluate(HINT_BOX, HINT_SELS), (
                f"{where}: no {HINT} on the page at all — a pill left behind by a "
                f"screen rebuild has no rect, and no rect reads here as covering "
                f"nothing")
            page.evaluate(SHOW_HINT, HINT)
            page.wait_for_timeout(80)

            found = page.evaluate(HINT_BOX, HINT_SELS)
            assert found["shown"], (
                f"{where}: the pill will not come up even with `hidden` taken off "
                f"it, so nothing below is measuring a pill a player would see")
            assert found["inTheFoot"], (
                f"{where}: the pill is outside the block of pinned buttons, so it is "
                f"placed against something other than what it can cover — that is "
                f"#277's arrangement, whatever the rects happen to be on this screen")
            hint, buttons = found["hint"], found["buttons"]
            assert buttons, (
                f"{where}: no buttons pinned under this screen, so the rule below is "
                f"being asked about nothing")

            for b in buttons:
                area = overlap(hint, b)
                assert area == 0, (
                    f"{where}: the rotate pill covers {area:.0f}px2 of {b['what']} "
                    f"({b['text']!r}) — pill y {hint['top']:.0f}..{hint['bottom']:.0f} "
                    f"x {hint['left']:.0f}..{hint['right']:.0f}, it y "
                    f"{b['top']:.0f}..{b['bottom']:.0f} x "
                    f"{b['left']:.0f}..{b['right']:.0f}")

            view, panel = found["view"], found["panel"]
            assert (hint["left"] >= -0.5 and hint["top"] >= -0.5
                    and hint["right"] <= view["w"] + 0.5
                    and hint["bottom"] <= view["h"] + 0.5), (
                f"{where}: the pill runs off a {view['w']}x{view['h']} screen — x "
                f"{hint['left']:.0f}..{hint['right']:.0f}, y "
                f"{hint['top']:.0f}..{hint['bottom']:.0f}")
            # the panel hides its overflow, so a pill inside it that is outside
            # its box is cut in half rather than merely somewhere odd
            assert (hint["top"] >= panel["top"] - 0.5
                    and hint["bottom"] <= panel["bottom"] + 0.5
                    and hint["left"] >= panel["left"] - 0.5
                    and hint["right"] <= panel["right"] + 0.5), (
                f"{where}: the pill is at y {hint['top']:.0f}..{hint['bottom']:.0f} "
                f"x {hint['left']:.0f}..{hint['right']:.0f} and the panel it is in "
                f"runs y {panel['top']:.0f}..{panel['bottom']:.0f} x "
                f"{panel['left']:.0f}..{panel['right']:.0f}, which hides what is "
                f"outside it — the part of the pill over that edge is cut off")
            measured.append(name)
    finally:
        page.evaluate("() => window.game && window.game.stop()")
        page.context.close()
    assert len(measured) == 9, f"only measured {measured}"
    assert by_itself == upright, (
        f"on {screen} the game "
        f"{'never raised' if upright else 'raised'} the pill on its own after the "
        f"window arrived at {viewport['width']}x{viewport['height']} — it is the "
        f"nudge for a phone held upright and nothing else, and if it does not come "
        f"up by itself here then the pill measured above is one only this test ever "
        f"sees")


def test_the_pill_rule_notices_the_pill_back_over_the_way_out(make_page):
    """Prove the rule above catches #277's own geometry, not just a stray pill.

    Put back where it was, the pill fails on the menu for the wrong reason: the
    panel there is 476px of an 844px window, so a pill against the bottom of the
    window is outside the panel and the clipping rule gets it first. #277 was
    measured on the characters screen, where the panel does reach the bottom and
    the pill lands squarely on "← Back" — so that is where the overlap rule has
    to be shown going red, with the pill given back the two lines of CSS it had.
    """
    page = make_page(IPHONE, touch=True)
    try:
        page.click("#btn-gallery")
        page.wait_for_selector(".char-card")
        page.evaluate(SHOW_HINT, HINT)
        page.wait_for_timeout(80)

        before = page.evaluate(HINT_BOX, HINT_SELS)
        assert before["buttons"], "no pinned buttons on the characters screen to be covered"
        assert not any(overlap(before["hint"], b) for b in before["buttons"]), (
            f"the drill starts from a screen where the pill is already on a button: "
            f"{before['hint']}, {before['buttons']}")

        page.evaluate("""(sel) => {
          const n = document.querySelector(sel);
          n.style.position = 'fixed';
          n.style.bottom = '8px';
          n.style.left = '50%';
          n.style.transform = 'translateX(-50%)';
        }""", HINT)
        page.wait_for_timeout(80)

        after = page.evaluate(HINT_BOX, HINT_SELS)
        # by key, not by tupling the area with the button: two buttons equally
        # covered would put a pair of dicts up against each other
        worst = max(after["buttons"], key=lambda b: overlap(after["hint"], b))
        assert overlap(after["hint"], worst) > 0, (
            f"the pill was put back against the bottom of the window and covers none "
            f"of the buttons pinned there — pill y {after['hint']['top']:.0f}.."
            f"{after['hint']['bottom']:.0f}, buttons {after['buttons']}")
        assert worst["text"].endswith("Back"), (
            f"it landed on {worst['text']!r} rather than the way out, so this is "
            f"not the arrangement #277 was measured on")
    finally:
        page.context.close()


@pytest.mark.leaves_a_game_running(reason="test_a_tap_jumps_on_touch taps the player "
                                          "this starts, and stops it afterwards")
def test_it_plays_on_touch(phone):
    phone.click("#btn-play")
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


def blank_page_fps(page) -> int:
    """What a page with *nothing on it* manages in this browser, right now.

    The control for the floor below: it is the same browser, the same machine and
    the same 700ms window, minus the game. A number under the floor here is a
    statement about the host, since there is nothing else left for it to be about.
    """
    control = page.context.new_page()
    try:
        control.goto("about:blank")
        return control.evaluate(FRAME_RATE)
    finally:
        control.close()


def playability_verdict(measure, control, floor=PLAYABLE_FPS):
    """Why a page is short of frames — itself, or the machine it is running on.

    Both readings arrive as callables because the case that matters cannot be
    produced to order: a starved *host* is what a 2-core box under load does to
    every page at once (#275), and handing this decision fake readings is the only
    way to run it for that case. The control is only paid for when it is needed.

    `measure` is asked twice before anything is blamed. The window is 700ms, and
    under `ship.py` — 942 tool tests just finished, a deploy is being polled — one
    700ms window is a sample of one; two failures in a day were both single
    samples that measured 59 and 60 when asked again.

    Returns `(cause, said)`:

    * `""` — the page has its frames.
    * `"retaken"` — one sample under the floor and one over. Worth saying out
      loud, not worth failing.
    * `"host"` — a page with nothing on it cannot reach the floor either, so
      there is nothing here about the game.
    * `"leak"` — the frames are there and this page is not getting them. The
      #182 case, and the only one that fails.
    """
    fps = measure()
    if fps >= floor:
        return "", ""
    again = measure()
    if again >= floor:
        return "retaken", (f"{fps}fps, then {again}fps when asked again, against a "
                           f"{floor}fps floor. One 700ms window is a sample of one on a "
                           f"busy box — but a page that is marginal twice a week is worth "
                           f"knowing about (#275)")
    host = control()
    if host < floor:
        return "host", (f"{fps} and {again}fps, and a page with *nothing on it* in the same "
                        f"browser manages {host}fps against a {floor}fps floor. This machine "
                        f"is not giving frames to anything, so this reading says nothing "
                        f"about the game (#275)")
    return "leak", (f"{fps} and {again}fps against a {floor}fps floor, while a page with "
                    f"nothing on it in the same browser gets {host}fps: the frames are there "
                    f"and this page is not getting them. Something else in this run is "
                    f"probably still animating — an `own_page` that walked away from a "
                    f"running game loop is the one that has happened (#182).")


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

    What it must not do is fail a *ship* for the machine's sake: this runs twice
    under `ship.py`, after the tool suite and again while the deploy is polled,
    and the answer there arrives after the push — so a false failure asks for a
    revert of a commit that is fine (#275, #276). Hence the control page: a bad
    reading with a healthy blank page beside it is the game's fault, and a bad
    reading with a starved one is the box's.
    """
    cause, said = playability_verdict(lambda: phone.evaluate(FRAME_RATE),
                                      lambda: blank_page_fps(phone))
    where = f"on {phone.viewport_size['width']}px"
    if cause == "host":
        pytest.skip(f"{where}: {said}")
    if cause == "retaken":
        warnings.warn(f"{where}: {said}")
    assert cause != "leak", f"{where}: {said}"


class Readings:
    """The numbers a case wants `measure`/`control` to return, in order.

    It counts the asking, because *when the control is paid for* is part of the
    rule: measuring a blank page costs another 700ms, and a run where every page
    is fine must not spend it.
    """

    def __init__(self, *values):
        self.values = values
        self.asked = 0

    def __call__(self) -> int:
        assert self.asked < len(self.values), (
            f"asked for reading {self.asked + 1} of {len(self.values)}: this case did not "
            f"expect the verdict to measure again")
        self.asked += 1
        return self.values[self.asked - 1]


def test_the_control_page_measures_and_then_goes_away(browser):
    """The one test that measures `blank_page_fps` — the cases below hand it in.

    Two things, and the second is why this is not covered by the drill: a control
    stuck at zero reads as a starved machine, which turns the floor *off* rather
    than making anything red, and a control page left open would itself be the
    kind of forgotten page the floor exists to notice (#182, #275).
    """
    ctx = browser.new_context(viewport=DESKTOP)
    try:
        page = ctx.new_page()
        page.goto("about:blank")
        open_pages = len(ctx.pages)
        fps = blank_page_fps(page)
        assert fps > 0, (
            "the control page reported no frames at all: every low reading in this "
            "session would now be excused as a starved machine")
        assert len(ctx.pages) == open_pages, (
            "the control page was left open in the session it was measuring")
    finally:
        ctx.close()


def test_a_page_that_has_its_frames_is_asked_once_and_costs_no_control():
    measure, control = Readings(60), Readings()
    assert playability_verdict(measure, control) == ("", "")
    assert (measure.asked, control.asked) == (1, 0), (
        "the happy path paid for a re-take or a control page: that is 700ms per "
        "measured page on every run, to answer a question nobody asked")


def test_one_bad_sample_followed_by_a_good_one_is_reported_not_failed():
    measure, control = Readings(19, 55), Readings()
    cause, said = playability_verdict(measure, control)
    assert cause == "retaken", f"a re-take over the floor was called {cause!r}: {said}"
    assert control.asked == 0, "a page that turned out fine still paid for a control"
    assert "19" in said and "55" in said, f"the two samples are not in the message: {said}"
    assert "#182" not in said, (
        f"a marginal sample was reported as the leak (#182): {said}")


def test_a_machine_that_cannot_give_a_blank_page_frames_is_not_the_games_fault():
    cause, said = playability_verdict(Readings(12, 13), Readings(14))
    assert cause == "host", f"a starved box was called {cause!r}: {said}"
    assert "14" in said, f"the control reading is not in the message: {said}"
    assert "#182" not in said, (
        f"a busy box was blamed on a leaked game loop (#182), which is the finding "
        f"#275 was filed to stop: {said}")


def test_a_page_short_of_frames_beside_a_healthy_blank_one_is_the_leak():
    cause, said = playability_verdict(Readings(12, 13), Readings(58))
    assert cause == "leak", f"a real starvation was called {cause!r}: {said}"
    assert "#182" in said, f"the leak is not named as the thing to look for: {said}"


# A page eating its own frames: the shape #182 arrived in, minus the intent. The
# spin is what an abandoned results screen does — paint, ask for another frame,
# paint — and 60ms of it per frame is enough to put a page under the floor while
# the machine around it is fine.
BURN_FRAMES = """
(ms) => {
  const spin = () => {
    const t = performance.now();
    while (performance.now() - t < ms) { /* the frame this page is not giving back */ }
    requestAnimationFrame(spin);
  };
  requestAnimationFrame(spin);
}
"""


def test_the_frame_floor_catches_a_page_that_is_really_not_getting_frames(browser):
    """The #182 case for real, in its own browser context rather than by hand.

    The three fake-reading cases above run the decision; this one runs the
    measurement too — a page that really cannot get frames, a control page that
    really can, and the verdict read off both. Its own context so nothing here is
    on a page the rest of the session shares, which is the whole subject.
    """
    ctx = browser.new_context(viewport=DESKTOP)
    try:
        starved = ctx.new_page()
        starved.goto("about:blank")
        starved.evaluate(BURN_FRAMES, 60)
        control = blank_page_fps(starved)
        if control < PLAYABLE_FPS:
            pytest.skip(f"a blank page gets {control}fps on this box, under the "
                        f"{PLAYABLE_FPS}fps floor: with the machine itself starved there is "
                        f"no difference here for the verdict to find")
        cause, said = playability_verdict(lambda: starved.evaluate(FRAME_RATE), lambda: control)
        assert cause == "leak", (
            f"a page burning its own frames beside a {control}fps blank page was called "
            f"{cause!r}: {said}")
        assert "#182" in said, f"the leak is not named as the thing to look for: {said}"
    finally:
        ctx.close()


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


# The colour the engine clears the canvas to before it draws anything, and the
# only place in the app it appears: no chapter's sky, ground, water or sprite
# palette is this navy, so a row of it in the middle of the picture is the empty
# frame showing through. Read out of the source rather than typed, so a repaint
# of the page background cannot leave this test measuring a colour nothing draws.
LETTERBOX = re.search(r'ctx\.clearRect\(0, 0, w, h\);\s*ctx\.fillStyle = "(#[0-9A-Fa-f]{6})";',
                      (APP / "public" / "js" / "game.js").read_text())


# Down the middle of the canvas, in device pixels, per chapter: how much of the
# screen the scene actually covers. The centre column is the honest place to ask
# — it misses the left and right bars entirely and sees every horizontal band.
PAINTED_COLUMN = """
async ({ hex, frames }) => {
  const { CHAPTERS } = await import('/js/chapters.js');
  const g = window.game;
  const c = g.canvas, ctx = c.getContext('2d');
  const rgb = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  const x = Math.floor(c.width / 2);
  document.getElementById('overlay').classList.add('hidden');
  g.toast = () => {};

  const look = () => {
    const d = ctx.getImageData(x, 0, 1, c.height).data;
    let bare = 0, first = null, last = null;
    for (let y = 0; y < c.height; y++) {
      const i = y * 4;
      if (d[i] === rgb[0] && d[i + 1] === rgb[1] && d[i + 2] === rgb[2]) {
        bare++;
        if (first === null) first = y;
        last = y;
      }
    }
    return { bare, height: c.height, first, last };
  };

  const out = { idle: look() };
  for (let i = 0; i < CHAPTERS.length; i++) {
    g.start(i);
    for (let n = 0; n < frames; n++) g.step(1 / 60);   // a way into the chapter
    g.render();
    out[CHAPTERS[i].id] = look();
    g.stop();     // shared browser: a loop left painting costs every later test
  }
  return out;
}
"""


@pytest.mark.parametrize("viewport,touch", [(IPHONE, True), (DESKTOP, False)],
                         ids=["iphone", "desktop"])
def test_the_scene_fills_the_screen_top_to_bottom(make_page, viewport, touch):
    """#251: held upright, the game was a strip in the middle of a dark screen.

    The measurement is the one the report was written from — the canvas is
    780x1688 device px on an iPhone and the scene ran y=624..1063, *439 of 1688
    rows, 26% of the screen*. The rest was the navy the frame is cleared to.

    The cause is letterboxing: the world is 16:9 and the phone held upright is
    not, so fitting the world by `min(w/W, h/H)` leaves two thick bars. The fix
    does not crop or zoom — the camera still shows exactly the same world, so
    what a player can see coming is unchanged — it draws the sky and the ground
    out to the edges of whatever screen it was given, and drops the ground line
    to `GROUND_ON_SCREEN` so the extra room lands in the sky where the story is.

    Its own page, both because the numbers depend on the viewport and because it
    starts every chapter; the desktop arm is here so the fix cannot pay for
    portrait by breaking the landscape view that was already right.
    """
    assert LETTERBOX, ("game.js no longer clears the frame to a flat colour, so this "
                       "test cannot tell a drawn screen from an empty one — find what "
                       "render() now starts with and re-point the pattern at it.")
    page = make_page(viewport, touch=touch)
    try:
        seen = page.evaluate(PAINTED_COLUMN, {"hex": LETTERBOX.group(1), "frames": 120})
        assert len(seen) == 6, f"only {len(seen)} screens measured: {sorted(seen)}"
        for where, m in sorted(seen.items()):
            painted = (m["height"] - m["bare"]) / m["height"]
            assert painted > 0.99, (
                f"{where} on {viewport['width']}x{viewport['height']}: only "
                f"{painted:.0%} of the middle of the screen is drawn on "
                f"({m['bare']} of {m['height']} device rows are the empty frame, "
                f"y={m['first']}..{m['last']}). That is the letterbox of #251.")
    finally:
        page.context.close()


def test_no_console_errors_on_touch(phone):
    assert not phone.errors, str(phone.errors[:3])


# --- the tap that arrives before the game does (#284) -------------------------

# Every other test in this file starts from `make_page`, which waits for
# `window.__ready` — so the whole of boot is a place the suite cannot see. These
# open their own page on purpose, on a slow connection: the window a player taps
# in is the one where the markup has painted and the fetches have not landed, and
# on a phone on mobile data it is seconds wide.
#
# Slowed with CDP rather than a held `page.route`, because a route handler that
# sleeps blocks the same thread the test drives the page on — the whole delay
# then elapses inside `wait_for_selector`, and the tap lands after boot.
SLOW_NET = {"offline": False, "latency": 700,
            "downloadThroughput": 500 * 1024, "uploadThroughput": 500 * 1024}

# What each dead button should turn out to have done. The menu is the only screen
# with an `h1.title`; every screen behind these buttons has an `h2`, so the
# heading is how the test says "the tap was honoured" without knowing the shapes.
# ▶ Play is the exception since #255: it opens no screen at all, it starts the
# chapter, so what it left behind is a running game (`heading` of None).
EARLY_TAPS = [
    ("btn-play", None),
    ("btn-story", "Chapter 1"),
    ("btn-gallery", "Everyone you'll meet"),
    ("btn-stats", "Stats"),
]


@pytest.mark.parametrize("button,heading", EARLY_TAPS, ids=[b for b, _ in EARLY_TAPS])
def test_a_tap_before_boot_finishes_is_not_lost(browser, base_url, button, heading):
    """#284: ▶ Play visible at 0.35s on the live phone, and dead until 1.13s.

    `index.html` ships a real menu so the page has something to show while
    `main.js` fetches the cast and the artwork — but `menu()` re-renders those
    buttons, and until it runs nothing is listening. A tap in that window went
    nowhere: a full-size, perfectly ordinary ▶ Play that does not work.

    So this taps in exactly that window — the first fetch is held open to make
    it wide, and the tap is only counted if `window.__ready` is still false when
    it lands — and asks for the screen the button names. It also asks that the
    button *looked* pressed, because a second of silence after a tap is what
    makes a kid tap again.
    """
    ctx = browser.new_context(viewport=IPHONE, has_touch=True, is_mobile=True,
                              device_scale_factor=2)
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        cdp = ctx.new_cdp_session(page)
        cdp.send("Network.enable")
        cdp.send("Network.emulateNetworkConditions", SLOW_NET)

        page.goto(base_url + "/", wait_until="commit")
        page.wait_for_selector(f"#{button}", state="visible", timeout=20000)
        assert page.evaluate("() => window.__ready !== true"), (
            f"boot finished before the {button} was even visible, so this tap is "
            f"not the one #284 is about — slow SLOW_NET down further")

        page.click(f"#{button}")
        # the guard that keeps this honest: a tap that lands *after* boot is a
        # tap on a live button, and would pass whatever the fix does
        assert page.evaluate("() => window.__ready !== true"), (
            f"boot finished while the {button} tap was being delivered — the "
            f"window this measures was not open")
        assert "waiting" in page.get_attribute(f"#{button}", "class"), (
            f"{button} took the tap and looked exactly as it did before: nothing "
            f"tells the player it was heard")

        page.wait_for_function("window.__ready === true", timeout=20000)
        if heading is None:
            with contextlib.suppress(PlaywrightTimeout):
                page.wait_for_function(
                    "() => window.game && window.game.mode === 'playing'", timeout=5000)
            assert page.evaluate("() => window.game && window.game.mode") == "playing", (
                f"tapping {button} before boot finished left the menu up rather than a "
                f"chapter — the tap was swallowed (#284)")
        else:
            page.wait_for_selector("#overlay h2", timeout=5000)
            got = page.text_content("#overlay h2")
            assert heading in got, (
                f"tapping {button} before boot finished left {got!r} on the screen — "
                f"the tap was swallowed (#284)")
    finally:
        # `window.game` is the canvas until the module names it: a page caught
        # mid-boot has an element there, not a Game
        page.evaluate("() => window.game instanceof Object && "
                      "typeof window.game.stop === 'function' && window.game.stop()")
        ctx.close()
    assert not errors, str(errors[:3])


# --- and the words about the picture, over the picture (#254) -----------------

# Raised through the engine's own event, which is the path the game uses when
# Bluey lands in the creek, and measured off the *layer* rather than the pill:
# `@keyframes pop` scales and lifts each pill for its whole 1.5s, so a rect read
# off a pill is a reading of the clock. The layer is where the toast was put.
TOAST_PLACE = """
(text) => {
  window.game.onEvent({ type: 'toast', text });
  const layer = document.querySelector('#toast-layer');
  if (!layer) throw new Error('no #toast-layer on the page to measure');
  const b = layer.getBoundingClientRect();
  const pills = [...layer.querySelectorAll('.toast')];
  return {
    pills: pills.map((p) => p.textContent.trim()),
    layer: { top: b.top, bottom: b.bottom, left: b.left, right: b.right,
             width: b.width, height: b.height },
    scene: window.game.sceneRect(),
    view: { w: window.innerWidth, h: window.innerHeight },
  };
}
"""


@pytest.mark.parametrize("viewport,touch,screen", SCREENS, ids=SCREEN_IDS)
def test_a_toast_lands_on_the_picture_not_in_the_sky(make_page, viewport, touch, screen):
    """#254: "Whoops!" 456px down a 1680px phone, with the scene starting at 624.

    `#toast-layer` was `top: 26%` of the *window*. On a laptop the picture fills
    the window and 26% is over the game; on a phone held upright the world band
    is about a fifth of the screen and sits low, so the message about the thing
    that just happened floated alone in the navy sky, a long way above the thing
    that just happened.

    So every window is asked the question the stylesheet could not: is the toast
    inside the picture — not merely inside the page, which it always was.
    """
    assert TOASTS, "no toasts found in game.js, so there is nothing to raise"
    page = make_page(viewport, touch=touch)
    try:
        page.click("#btn-play")
        page.wait_for_timeout(400)
        assert page.evaluate("window.game.mode") == "playing", (
            f"{screen}: never got into a chapter, so there is no game to talk about")

        said = max(TOASTS, key=len)
        m = page.evaluate(TOAST_PLACE, said)
        scene, layer, view = m["scene"], m["layer"], m["view"]
        # a layer with nothing in it is a rect at the placed top and no height,
        # which sits inside any band — so the pill has to be there to be measured
        assert m["pills"] == [said], (
            f"{screen}: raised {said!r} and the layer holds {m['pills']} — nothing "
            f"was measured")
        assert layer["height"] > 1 and layer["width"] > 1, (
            f"{screen}: the toast layer is {layer['width']:.0f}x{layer['height']:.0f}, "
            f"which is not a pill a player can read")
        # the band really is the smaller half of the story on a phone: if it ever
        # fills every window here, this test stops being about anything
        assert scene["height"] <= view["h"] + 0.5 and scene["width"] <= view["w"] + 0.5, (
            f"{screen}: the picture ({scene['width']:.0f}x{scene['height']:.0f}) is "
            f"bigger than the {view['w']}x{view['h']} window it is drawn in")

        assert layer["top"] >= scene["top"] - 1 and layer["bottom"] <= scene["bottom"] + 1, (
            f"{screen}: {said!r} sits at y {layer['top']:.0f}..{layer['bottom']:.0f}, "
            f"outside the picture at y {scene['top']:.0f}..{scene['bottom']:.0f} of a "
            f"{view['w']}x{view['h']} window — that is #254's message in the sky")
        middle = (layer["left"] + layer["right"]) / 2
        assert scene["left"] - 1 <= middle <= scene["right"] + 1, (
            f"{screen}: {said!r} is centred at x {middle:.0f}, beside the picture at x "
            f"{scene['left']:.0f}..{scene['right']:.0f}")
        # and near the top of it, where Bluey is — the bottom of the band is the
        # grass, and a pill down there covers what it is talking about
        assert layer["bottom"] <= scene["top"] + scene["height"] * 0.6, (
            f"{screen}: {said!r} reaches y {layer['bottom']:.0f}, past the top half of "
            f"the picture (y {scene['top']:.0f}..{scene['bottom']:.0f})")
    finally:
        page.evaluate("() => window.game && window.game.stop()")
        page.context.close()


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
