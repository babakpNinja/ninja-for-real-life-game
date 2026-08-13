"""The licensing prose, checked by the suite instead of by remembering.

`scripts/fetch_assets.py --check` proves every hand-written copy of the notice
(README, boot splash, the main.js fallback) still matches the one in
data/asset-credits.json, and that the README carries no claim that stopped being
true when the artwork shipped. It was a hand-run command, which is another way
of spelling "runs the day I write it": this file is what makes it a check.

It reads the working tree, not the deployed site, so it means the same thing
under --base-url — the tree is what produced the deploy.
"""

import subprocess
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent
SCRIPTS = [
    ("fetch_assets.py", "the artwork is credited and the notice has not drifted"),
    ("build_rigs.py", "the rigs still cut every render where they say they do"),
]


@pytest.mark.parametrize("script,what", SCRIPTS, ids=[s for s, _ in SCRIPTS])
def test_the_asset_scripts_self_check(script, what):
    proc = subprocess.run(
        [sys.executable, str(APP / "scripts" / script), "--check"],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, f"{script} --check ({what}) failed:\n{proc.stdout}{proc.stderr}"
