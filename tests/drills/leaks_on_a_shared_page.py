"""A probe that walks away from a running game on a *shared* page, on purpose.

The other half of the leak guard (#182). ``own_page`` can only speak for the page
it handed out, and that one is closed at the end of the test regardless — the
leak that actually cost a morning was on ``desktop``, which every later test goes
on using while the abandoned loop paints over the top of it.

Four tests, because the guard has four things to get right: blame the test that
leaked, put the loop down so the next test is not punished for it, say nothing
about a test that *declares* the handoff, and refuse a declaration with no
reason. Run by ``test_a_leak_on_a_shared_page_is_blamed_once_and_put_down`` in
``test_game.py``.
"""

import pytest

LEAKED = {}


def test_a_probe_that_walks_away_from_a_running_game(make_page):
    page = make_page({"width": 1280, "height": 800})
    LEAKED["page"] = page                     # kept open: this is the leak
    page.evaluate("() => { window.game.start(0); window.game.finish(); }")
    assert page.evaluate("() => window.game.mode") == "finished"


def test_the_next_test_inherits_a_page_that_is_no_longer_painting():
    assert LEAKED["page"].evaluate("() => window.game.running") is False, (
        "the guard blamed the test above and left the loop running anyway")


@pytest.mark.leaves_a_game_running(to="test_a_declaration_with_no_reason_is_refused",
                                   reason="the test below picks this chapter up")
def test_a_declared_handoff_is_left_alone():
    LEAKED["page"].evaluate("() => window.game.start(0)")
    assert LEAKED["page"].evaluate("() => window.game.mode") == "playing"


@pytest.mark.leaves_a_game_running
def test_a_declaration_with_no_reason_is_refused():
    LEAKED["page"].evaluate("() => window.game.stop()")
