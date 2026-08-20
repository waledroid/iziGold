"""Run the headless Node smoke test for the report template under pytest.

`backtest_report_smoke.js` checks the template's data wiring (series shapes,
marker order, stop-line gaps, canvas geometry) with hand-rolled DOM/canvas/
LightweightCharts stubs -- no npm or browser dependency. It used to be run
only by hand, which is how a template regression stays green for a month.
Node is not a hard requirement of this repo, so a missing `node` skips.
"""
import shutil
import subprocess

import pytest

from tests.test_backtest_golden import ROOT

SMOKE = ROOT / "service" / "tests" / "backtest_report_smoke.js"


@pytest.mark.skipif(shutil.which("node") is None,
                    reason="node is not installed; the report smoke test needs it")
def test_report_template_passes_the_headless_smoke_test():
    proc = subprocess.run(["node", str(SMOKE)], capture_output=True,
                          text=True, timeout=300)
    assert proc.returncode == 0, (
        f"{SMOKE.name} failed\n--- stdout ---\n{proc.stdout}"
        f"\n--- stderr ---\n{proc.stderr}")
    assert "PASS" in proc.stdout
