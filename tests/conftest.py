"""Shared browser fixtures for the game's end-to-end suite.

The same tests run against a local server and against the deployed site — the
only difference is where they are pointed:

    python -m pytest tests -q
    python -m pytest tests -q --base-url=https://<live-url>

One browser and one page per viewport for the whole run: the game is a single
stateful session (chapters unlock as they are played, progress is written to
``localStorage``), so a fresh page per test would test a game nobody is playing.
"""

import pytest
from playwright.sync_api import sync_playwright

def pytest_addoption(parser):
    parser.addoption("--base-url", action="store", default="http://localhost:3000",
                     help="where the game is served (default: the local dev server)")


@pytest.fixture(scope="session")
def base_url(request) -> str:
    return request.config.getoption("--base-url").rstrip("/")


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
