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
        return page

    yield make
    for ctx in contexts:
        ctx.close()
