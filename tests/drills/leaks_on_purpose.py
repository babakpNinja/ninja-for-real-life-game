"""A test that leaves the game loop running, on purpose.

Not named ``test_*.py``, so a normal ``pytest tests`` run walks past this
directory; pytest collects a file it is handed by name whatever it is called.
The suite's own guard is the thing under test here, and the only honest way to
ask "does it fail the test that leaked" is to let a test leak and read what
pytest says about it — see ``test_a_leaked_game_loop_fails_the_test_that_left_it``
in ``test_game.py``, which runs this file in a subprocess.

Deliberately the *easy* mistake: start a chapter, assert something true about
it, return. Nothing here looks wrong, which is the point — ``start()`` begins a
render loop that outlives the test, and every probe in the suite is written in
these three lines.
"""


def test_a_probe_that_walks_away_from_a_running_game(own_page):
    own_page.evaluate("() => window.game.start(0)")
    assert own_page.evaluate("() => window.game.mode") == "playing"
