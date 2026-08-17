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


# What `-m smoke` means here, since a marker with a vague meaning grows until it
# is the suite again (#332). The whole suite runs twice on every ship — once
# before the push and once against the live site — and 13 minutes of the second
# run re-asks questions whose answer *cannot* differ between two runs of one
# commit: physics, layout arithmetic, what a function returns.
#
# A test earns `smoke` if a **deploy** can be the thing that makes it fail:
#
#   * a file that is in the repo and not in the deploy (an exclude, a
#     .gitignore that followed the rsync, a build artifact never generated) —
#     the pictures, the small copies of them, the recordings;
#   * the page not booting at all over there: a module that 404s, a console
#     error, a screen that renders empty;
#   * the server in front of it: the wrong content type, a route that does not
#     answer, a stale container still serving the old build.
#
# A test does *not* earn it for being fast, and a test that reads the repo off
# the local disk can never earn it — it would ask the deployed URL nothing.
SMOKE = ("smoke: this can fail because of how the build was deployed, not just "
         "because of what the code does — the subset `ship.py` re-runs live")


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

    def make(viewport, touch=False, user_agent=None, init_script=None):
        """``user_agent``/``init_script`` are how a test reaches a browser this
        one is not: the phone in #354 has no element Fullscreen API and says so
        in its UA, and both have to be in place before the modules load."""
        ctx = browser.new_context(viewport=viewport, has_touch=touch, is_mobile=touch,
                                  device_scale_factor=2 if touch else 1,
                                  **({"user_agent": user_agent} if user_agent else {}))
        if init_script:
            ctx.add_init_script(init_script)
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

#: every test this session collected, and the subset left after deselection.
#: A handoff is a claim about another test, and whether that test is going to run
#: depends on the selector — `-m smoke` (#332) collects the test that starts a
#: chapter without the one that stops it.
collected: set[str] = set()
selected: set[str] = set()


def pytest_configure(config):
    # Both markers here, in one hook: a second `pytest_configure` in this module
    # would not be a second hook, it would replace this one — and the marker it
    # registered would be silently unknown.
    config.addinivalue_line("markers", SMOKE)
    config.addinivalue_line(
        "markers",
        f"{HANDOFF}(to, reason): this test hands a running game to another on "
        "purpose; `to` names the test that picks it up and `reason` says why")


def pytest_itemcollected(item):
    collected.add(item.function.__name__)


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(items):
    # trylast: -m/-k deselect in this same hook, so this must run after them to
    # see what is actually going to run.
    selected.clear()
    selected.update(item.function.__name__ for item in items)


def declares_the_receiver(module, to: str) -> bool:
    """Does the *file* have a test called ``to``, whatever this run selected?

    ``collected`` cannot answer this. Run one node id — which is what ``ship.py``
    does with a failed test now (#409) — and the only thing collected is that one
    test, so every receiver looks renamed and every handoff in the suite reads
    ``broken``: five real tests errored at teardown, each accused of naming a test
    "renamed or deleted" that is forty lines below it (#412). The module is
    imported either way, so it is the module that knows.

    Still or-ed with ``collected``: a receiver in another file of the same run is
    also evidence the receiver exists, and this check's job is only to catch a
    ``to`` that names nothing at all.
    """
    return callable(getattr(module, to, None)) or to in collected


def handoff_state(mark, nodeid: str, module=None) -> tuple[str, str]:
    """Whether the test this one hands its running game to is going to run.

    Three answers, not two (#40), returned as (state, complaint):

    ``held``  the receiver is in this run — the exemption stands and the loop is
              left alone, which is the case the marker exists for.
    ``void``  the receiver exists but this run deselected it (`-m smoke` collects
              the test that starts a chapter without the one that stops it,
              #332), or never selected it (a single-id re-run, #412). Nothing is
              coming to pick the game up, so the guard puts it down — quietly,
              because the test did nothing wrong.
    ``broken`` the declaration cannot be checked: no ``to``/``reason``, or a
              ``to`` this file does not contain (renamed, deleted). An exemption
              nobody can check is how a leak gets waved through, so it fails.
    """
    to, reason = mark.kwargs.get("to"), mark.kwargs.get("reason")
    if not to or not reason:
        return "broken", (
            f"{nodeid} is marked {HANDOFF} without both `to` and `reason`. An "
            f"unexplained exemption is how a leak gets waved through: name the "
            f"test that picks the running game up, and why.")
    if not declares_the_receiver(module, to):
        return "broken", (
            f"{nodeid} hands its running game to {to!r}, which this file does not "
            f"contain — renamed or deleted. The exemption cannot be checked, so it "
            f"does not hold.")
    return ("held", "") if to in selected else ("void", "")


def senders_of(module, name: str) -> list[str]:
    """The tests that hand ``name`` a chapter already playing, in file order.

    A list, because two tests can hand to one: both phone tests name
    ``test_a_tap_jumps_on_touch`` as the one that stops the chapter. So "was the
    state I inherit going to be built?" is *any* of them being in the run, and
    returning the first one found would skip a test whose other sender did run.

    The inverse of the ``to`` declaration, and it too has to be read off the
    module: a run of one node id collects the receiver and nothing else, so the
    run itself has no memory of who was supposed to have started the chapter.

    Four of this file's tests are one chapter played across four, so three of them
    read `window.game.player` on the strength of the test above having started it.
    Alone they do not fail, they raise — `player` is undefined — and #409's
    re-run-alone would report that as "failed again alone: a regression" (#412).
    """
    found = []
    for attr in vars(module).values():
        for mark in getattr(attr, "pytestmark", ()):
            if mark.name == HANDOFF and mark.kwargs.get("to") == name:
                found.append(getattr(attr, "__name__", str(attr)))
    return found


@pytest.fixture(autouse=True)
def handed_the_game(request):
    """Skip a test whose sender — the test that starts the chapter it acts on —
    this run left out.

    The handoff is a two-ended claim and only one end was checked. ``to`` protects
    the *sender*: it may walk away from a running loop because somebody is coming.
    Nothing protected the receiver, which reads ``window.game.player.y`` on the
    strength of the sender having played the chapter first. Run one of them alone —
    which is what ``ship.py`` does to a failed test now (#409) — and it raises on
    an undefined player, and the ship calls that "failed again alone: a regression"
    when the real answer is *this test cannot be asked on its own*.

    Skipped, not started-for-it: fabricating the chapter here would be a second
    copy of the sender, and in a full run it would let a sender that stopped
    starting chapters go unnoticed. This only fires when the sender is out of the
    run, which is exactly when the test's own verdict would mean nothing (#412).
    """
    senders = senders_of(request.node.module, request.node.function.__name__)
    if senders and not any(s in selected for s in senders):
        pytest.skip(f"{' / '.join(senders)} hands this test a chapter already playing, "
                    f"and this run selected none of them — alone this test asks nothing "
                    f"(#412)")


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
    with ``to`` naming the test that picks it up and ``reason`` saying why — and
    the declaration is checked against what this run actually collected, because
    a subset selector can leave the receiver out (see ``handoff_state``).

    The loop is put down *before* the test is failed: the blame belongs on the
    one test that left it, and a guard that reports the same leak on every test
    that follows is the noise that gets guards deleted (#60).
    """
    yield
    mark = request.node.get_closest_marker(HANDOFF)
    state, complaint = (handoff_state(mark, request.node.nodeid, request.node.module)
                        if mark else ("none", ""))
    for page in pages:
        if page.is_closed():
            continue
        left = page.evaluate(RUNNING_GAME)
        if left and state != "held":
            page.evaluate("() => window.game.stop()")
            if state == "none":
                pytest.fail(leak_message(left, request.node.nodeid,
                                         "a page every test after it goes on using"),
                            pytrace=False)
    if state == "broken":               # after the loops are down, as above
        pytest.fail(complaint, pytrace=False)


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
