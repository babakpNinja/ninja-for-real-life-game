"""Shared browser fixtures for the game's end-to-end suite.

The same tests run against a local server and against the deployed site — the
only difference is where they are pointed:

    python -m pytest tests -q                               # boots its own server
    python -m pytest tests -q --base-url=https://<live-url> # the deployed site

With no ``--base-url`` the session starts ``node server.js`` on a free port and
stops it afterwards, so the suite is a *pre-push* gate: something that can say no
while the change is still in the workspace, without anyone remembering to start a
server first. Pointed at a URL it is the post-deploy check instead.

One browser and one page per viewport for the whole run: the game is a single
stateful session (chapters unlock as they are played, progress is written to
``localStorage``), so a fresh page per test would test a game nobody is playing.

The pages being shared is also the suite's one shared resource, and ``own_page``
is where that is opted out of — with a teardown that fails the test if it walked
away from a running game loop (#182).
"""

import contextlib
import os
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

APP = Path(__file__).resolve().parent.parent
BOOT_TIMEOUT = 20
DESKTOP = {"width": 1280, "height": 800}


def pytest_addoption(parser):
    parser.addoption("--base-url", action="store", default=None,
                     help="where the game is served; omit to boot a local server")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_until_serving(url: str, proc: subprocess.Popen, log) -> None:
    """Block until /api/health answers, or say why it never will.

    A server that died at boot is the interesting case: without this the browser
    would report 31 connection refusals and none of them would mention the node
    error that caused them.
    """
    deadline = time.time() + BOOT_TIMEOUT
    while time.time() < deadline:
        if proc.poll() is not None:
            log.seek(0)
            raise RuntimeError(f"node server.js exited with {proc.returncode}:\n"
                               f"{log.read().decode()[-1000:]}")
        try:
            with urllib.request.urlopen(f"{url}/api/health", timeout=2) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            time.sleep(0.2)
    raise RuntimeError(f"node server.js did not answer {url}/api/health in {BOOT_TIMEOUT}s")


@contextlib.contextmanager
def local_server():
    """Run the app's own server on a free port, and always stop it again.

    The port is chosen by the OS rather than hardcoded, so a dev server already
    running on 3000 is neither disturbed nor accidentally tested instead of the
    working tree.
    """
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    log = tempfile.TemporaryFile()          # not a pipe: a full pipe buffer would hang the server
    proc = subprocess.Popen(["node", "server.js"], cwd=APP, stdout=log, stderr=log,
                            env={**os.environ, "PORT": str(port)})
    try:
        wait_until_serving(url, proc, log)
        yield url
    finally:
        # runs on a failed test and on Ctrl-C; a leaked node would hold the port
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        log.close()


@pytest.fixture(scope="session")
def base_url(request) -> str:
    given = request.config.getoption("--base-url")
    if given:
        yield given.rstrip("/")
        return
    with local_server() as url:
        yield url


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture(scope="session")
def make_page(browser, base_url):
    """Open the game and wait until the engine says it is ready.

    Console and page errors are collected on ``page.errors`` from the first
    moment, because the ones worth catching happen during boot — long before any
    test thinks to look.

    Every page it opens is registered in ``pages`` for the leak guard below,
    which needs to ask about pages the running test never asked for.
    """
    contexts = []

    def make(viewport, touch=False):
        ctx = browser.new_context(viewport=viewport, has_touch=touch, is_mobile=touch,
                                  device_scale_factor=2 if touch else 1)
        page = ctx.new_page()
        page.errors = []
        page.on("pageerror", lambda e: page.errors.append(str(e)))
        page.on("console", lambda m: page.errors.append(m.text) if m.type == "error" else None)
        page.goto(base_url + "/", wait_until="domcontentloaded")
        page.wait_for_function("window.__ready === true", timeout=20000)
        contexts.append(ctx)
        pages.append(page)
        return page

    yield make
    for ctx in contexts:
        ctx.close()
    del pages[:]


# Every page `make_page` has opened this session. Module level rather than a
# fixture because the guard below is autouse: asking for `make_page` there would
# boot a server and a browser for the tests that never open a page.
pages = []


# A game the engine is still animating, or nothing. `start()` calls `loop()`, and
# the loop only stops on `stop()` — reaching the finish line sets `mode` to
# "finished" and keeps painting the results screen, which is the expensive case.
RUNNING_GAME = """
() => {
  const g = window.game;
  if (!g || !g.running) return null;
  return { mode: g.mode, chapter: g.ch ? g.ch.id : null };
}
"""


def page_census(subject=None, open_pages=None) -> list[dict]:
    """Every *other* page this session still has open, and what it is animating.

    The frame floor (#275) decides that a page short of frames beside a healthy
    blank one is being starved by a loop somebody walked away from (#182) — and
    until #302 it decided that by elimination, without ever asking the browser
    what was open. It fired once that way during a ship, on a suite that ran green
    on either side of it, so the one thing the message asserted was the one thing
    nothing had established.

    `pages` is the same list the leak guard below reads, so this is that guard's
    question asked of the whole session rather than of one test's teardown. The
    subject is left out: the page being measured is *allowed* to be animating —
    the phone is mid-chapter by the time the floor is taken, on purpose.

    A page that cannot be asked is its own row (`running` is the string), not a
    quiet no: "nothing else is animating" is about to be the reason a reading is
    excused, and an unanswered question must not be able to say it (#40).
    """
    rows = []
    for page in (pages if open_pages is None else open_pages):
        if page is subject or page.is_closed():
            continue
        try:
            running = page.evaluate(RUNNING_GAME)
        except Exception as e:  # noqa: BLE001 — a closed/crashed page is 'could not ask'
            running = f"could not ask ({type(e).__name__})"
        rows.append({"where": describe_page(page), "running": running})
    return rows


def describe_page(page) -> str:
    """A page named the way the suite names them: by the viewport it was opened at."""
    size = page.viewport_size or {}
    where = f"{size.get('width')}x{size.get('height')}" if size else "no viewport"
    return f"{where} {page.url}"


def census_phrase(rows: list[dict]) -> str:
    """What the census found, in the sentence a failure will carry.

    Never silence: an empty census and a census nobody took read identically in a
    message, and they are opposite findings (#40).
    """
    busy = [r for r in rows if isinstance(r["running"], dict)]
    unknown = [r for r in rows if isinstance(r["running"], str)]
    if not rows:
        return "no other page in this run is open"
    said = []
    if busy:
        said.append("still animating: " + "; ".join(
            f"{r['where']} (mode={r['running'].get('mode')}"
            + (f", {r['running']['chapter']}" if r["running"].get("chapter") else "") + ")"
            for r in busy))
    if unknown:
        said.append("could not be asked: " + "; ".join(r["where"] for r in unknown))
    if not said:
        said.append("none of them animating")
    return f"{len(rows)} other page(s) open, " + ", ".join(said)


def leak_message(left: dict, nodeid: str, which_page: str) -> str:
    """What to say about a game loop a test walked away from.

    Its own function because the sentence is the whole point: the symptom lands
    on some *other* test, minutes later, as a player who did not move (#176) —
    and there is nothing in that failure to suggest the cause is a page this test
    left animating.
    """
    where = f" in {left['chapter']}" if left.get("chapter") else ""
    return (f"{nodeid} left the game loop running{where} (mode={left['mode']}) on "
            f"{which_page}. Every page in this suite shares one browser, so a "
            f"loop left painting competes with every test after this one — the phone "
            f"page dropped to ~3fps and a tap test failed for no visible reason. "
            f"End the probe with `g.stop()`.")


HANDOFF = "leaves_a_game_running"


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        f"{HANDOFF}(reason): this test hands a running game to the next one on "
        "purpose; the reason must say which test picks it up")


@pytest.fixture(autouse=True)
def no_leaked_game_loop(request):
    """Fail a test that walks away from a game still animating on a shared page.

    ``own_page`` guards the page it hands out, and that page is closed at the end
    of the test anyway. The leak #182 cost a morning to was on the shared pages:
    ``desktop`` and the two phones are opened once and used by everything after,
    so a loop left painting on one of them competes with every test for the rest
    of the session. Autouse, because a probe leaks without meaning to — there is
    no point offering this as something to opt into.

    Any running loop, not just a finished one. A first cut here allowed
    ``playing`` on the grounds that this suite is one long play session and the
    tap tests act on a chapter an earlier test started — but a chapter left
    playing goes on playing, reaches its own finish line some tests later, and
    the results screen is then blamed on whichever test happened to be running.
    So the handoff is declared instead: ``@pytest.mark.leaves_a_game_running``
    with a reason naming the test that picks it up.

    The loop is put down *before* the test is failed: the blame belongs on the
    one test that left it, and a guard that reports the same leak on every test
    that follows is the noise that gets guards deleted (#60).
    """
    yield
    mark = request.node.get_closest_marker(HANDOFF)
    if mark and not (mark.kwargs.get("reason") or mark.args):
        pytest.fail(f"{request.node.nodeid} is marked {HANDOFF} with no reason. "
                    f"An unexplained exemption is how a leak gets waved through: "
                    f"say which test picks the running game up.", pytrace=False)
    for page in pages:
        if page.is_closed():
            continue
        left = page.evaluate(RUNNING_GAME)
        if left and not mark:
            page.evaluate("() => window.game.stop()")
            pytest.fail(leak_message(left, request.node.nodeid,
                                     "a page every test after it goes on using"),
                        pytrace=False)


@pytest.fixture
def own_page(make_page, request):
    """A page of this test's own, for the tests that must not inherit a screen.

    Everything else here shares ``desktop`` on purpose. Two things cannot: a page
    whose artwork is blocked (the route would break every test after it) and
    anything asserting a sprite has *not* been drawn yet, since by the end of the
    file the gallery has drawn all twenty-five.

    Torn down as it was asked for — the page is closed, and a test that left the
    engine looping on it fails at teardown rather than being cleaned up quietly.
    Cleaning up quietly is how the next probe gets written the same way.
    """
    page = make_page(DESKTOP)
    yield page
    left = page.evaluate(RUNNING_GAME)
    page.context.close()          # after the read: closing is what stops the loop
    if left:
        pytest.fail(leak_message(left, request.node.nodeid, "the page it asked for"),
                    pytrace=False)
