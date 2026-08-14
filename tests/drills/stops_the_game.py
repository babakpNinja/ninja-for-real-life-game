"""The same probe, written correctly: start a chapter and put the loop down.

The other half of the guard, and the half a leak-detector usually loses. A check
that also fires on the tests doing the right thing is one nobody keeps — it gets
read as noise and then switched off — so this run has to come back clean, and
the drill in ``test_game.py`` asserts that it does.
"""


def test_a_probe_that_stops_what_it_started(own_page):
    own_page.evaluate("() => window.game.start(0)")
    assert own_page.evaluate("() => window.game.mode") == "playing"
    own_page.evaluate("() => window.game.stop()")
