"""Handoffs whose receiver is not going to run (#332).

The declared handoff (``leaves_a_game_running``) is a claim about *another test*,
and until #332 nothing checked that the other test was in the run. Then `ship.py`
started re-running a subset live (`-m smoke`), which collects the test that starts
a chapter on the phone without the one that stops it — and the game was handed to
nobody.

Two ways that claim can fail to hold, and they are not the same finding:

* the receiver exists but this run left it out — nobody is coming, so the guard
  puts the loop down and says nothing, because the test did as it was told;
* the receiver is not in the file at all — a sentence nobody can check, which is
  refused.

Run by ``test_a_handoff_to_a_test_that_is_not_running_is_not_an_exemption`` in
``test_game.py``, which deselects the receiver with ``-k``.
"""

import pytest

LEFT = {}


@pytest.mark.leaves_a_game_running(to="test_the_receiver_this_run_left_out",
                                   reason="it would stop the chapter this starts")
def test_it_hands_the_game_to_a_test_that_will_not_run(make_page):
    page = make_page({"width": 1280, "height": 800})
    LEFT["page"] = page
    page.evaluate("() => window.game.start(0)")
    assert page.evaluate("() => window.game.mode") == "playing"


def test_the_receiver_this_run_left_out():
    """Deselected by the runner. In a full run it is what stops the loop."""
    LEFT["page"].evaluate("() => window.game.stop()")


def test_the_guard_put_the_loop_down_instead():
    assert LEFT["page"].evaluate("() => window.game.running") is False, (
        "the game was handed to a test this run never collected, and left painting")


@pytest.mark.leaves_a_game_running(to="test_renamed_away_two_refactors_ago",
                                   reason="a receiver that does not exist")
def test_it_hands_the_game_to_a_test_that_does_not_exist():
    LEFT["page"].evaluate("() => window.game.start(0)")
    assert LEFT["page"].evaluate("() => window.game.mode") == "playing"


def test_that_one_was_put_down_too():
    assert LEFT["page"].evaluate("() => window.game.running") is False, (
        "a broken declaration was refused and the loop left running anyway")
