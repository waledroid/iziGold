"""The report must be one self-contained file: no network, no server."""
import importlib.util
import json
import pathlib
import re

from tests.test_backtest_golden import BARS, _load_bt

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _writer():
    spec = importlib.util.spec_from_file_location(
        "btr", ROOT / "scripts" / "backtest_report.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _artifact():
    bt = _load_bt()
    candles = json.loads(BARS.read_text())
    trades, bal, dd, valley = bt.run(candles, 10000.0, False)
    args = bt.build_parser().parse_args(["--balance", "10000"])
    return bt.build_run_json(candles, trades, args,
                             {"bal": bal, "max_dd": dd, "valley": valley})


def test_report_is_written_and_is_not_empty(tmp_path):
    out = tmp_path / "r.html"
    _writer().write_report(_artifact(), str(out))
    assert out.stat().st_size > 100_000


def test_report_references_no_external_assets(tmp_path):
    out = tmp_path / "r.html"
    _writer().write_report(_artifact(), str(out))
    html = out.read_text(encoding="utf-8")
    for m in re.findall(r'(?:src|href)\s*=\s*["\']([^"\']+)', html):
        assert not m.startswith(("http://", "https://", "//")), \
            f"external asset would break offline use: {m}"


def test_report_embeds_the_chart_library_and_the_run(tmp_path):
    out = tmp_path / "r.html"
    art = _artifact()
    _writer().write_report(art, str(out))
    html = out.read_text(encoding="utf-8")
    assert "createChart" in html, "chart library not inlined"
    assert '"trades"' in html, "run artifact not inlined"
    assert str(art["stats"]["trades"]) in html


def test_no_placeholder_survives_into_the_output(tmp_path):
    out = tmp_path / "r.html"
    _writer().write_report(_artifact(), str(out))
    html = out.read_text(encoding="utf-8")
    for token in ("__LIB__", "__DATA__", "__TITLE__"):
        assert token not in html, f"unsubstituted placeholder {token}"


def test_fixed_entry_mode_serializes_tp_as_null_for_every_trade():
    """Task 6's reviewer verified 61/61 by hand; pin it in CI. The report's
    box-drawing code (`if (tr.tp != null)`) depends on fixed-mode trades never
    carrying a target, else it would draw a bogus green reward zone.

    bt.run() reads the ENTRY_MODE/FIXED_LOTS globals directly (they are only
    wired from args inside main(), which this test bypasses), so set them the
    same way test_backtest_golden.py sets STRICT_WINDOW."""
    bt = _load_bt()
    candles = json.loads(BARS.read_text())
    bt.STRICT_WINDOW = False
    bt.ENTRY_MODE = "fixed"
    bt.FIXED_LOTS = 0.05
    args = bt.build_parser().parse_args(
        ["--balance", "10000", "--entry-mode", "fixed"])
    trades, bal, dd, valley = bt.run(candles, 10000.0, False)
    art = bt.build_run_json(candles, trades, args,
                            {"bal": bal, "max_dd": dd, "valley": valley})
    assert art["trades"], "expected at least one trade in the fixture slice"
    for t in art["trades"]:
        assert t["tp"] is None, f"fixed-mode trade carries a tp: {t}"
