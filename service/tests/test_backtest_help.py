"""--help must state what the replay does NOT model. A result whose limits are
invisible gets trusted further than it deserves."""
import argparse
import json
import subprocess
import sys

from tests.test_backtest_golden import BARS, ROOT, _load_bt


def test_caveats_name_the_unmodelled_rails():
    bt = _load_bt()
    blob = " ".join(bt.CAVEATS).lower()
    assert "brake" in blob
    assert "kill switch" in blob
    assert "news" in blob


def test_help_text_contains_the_caveats_and_the_balance_guidance():
    bt = _load_bt()
    text = bt.build_parser().format_help()
    assert "not modelled" in text.lower()
    assert "$4,000" in text or "4000" in text


def test_arguments_are_grouped():
    bt = _load_bt()
    titles = [g.title for g in bt.build_parser()._action_groups]
    for expected in ("Data", "Rules", "Experiments", "Output"):
        assert expected in titles, f"missing '{expected}' group in --help"


def test_every_argument_belongs_to_one_of_the_four_groups():
    bt = _load_bt()
    ap = bt.build_parser()
    named = {"Data", "Rules", "Experiments", "Output"}
    grouped = {a.dest for g in ap._action_groups if g.title in named
               for a in g._group_actions}
    ungrouped = {a.dest for a in ap._actions} - grouped - {"help"}
    assert not ungrouped, f"arguments outside the four groups: {sorted(ungrouped)}"


def test_every_flag_documents_itself():
    """A flag with no help text is a flag nobody can audit. --balance had an
    entire spec section and showed nothing, not even its default."""
    ap = _load_bt().build_parser()
    missing = [a.option_strings[0] for a in ap._actions
               if a.dest != "help" and not a.help]
    assert not missing, f"flags with no --help text: {missing}"


def test_the_no_op_strict_window_flag_is_visible_and_says_it_is_a_no_op():
    """Hiding a flag whose meaning just inverted is the wrong instinct: a
    scripted run passing --strict-window deserves to be told it is inert."""
    ap = _load_bt().build_parser()
    act = next(a for a in ap._actions if "--strict-window" in a.option_strings)
    assert act.help is not argparse.SUPPRESS and act.help
    assert "no-op" in act.help.lower()
    assert "--strict-window" in ap.format_help()


def test_the_experiments_blurb_does_not_claim_the_window_flags_are_off():
    """--window-start/--window-end are the EA's REAL trading window (4-23),
    not a study filter that defaults to OFF."""
    ap = _load_bt().build_parser()
    blurb = next(g.description for g in ap._action_groups
                 if g.title == "Experiments")
    assert "window" in blurb.lower(), \
        "the Experiments blurb must call out the window flags as live defaults"


def test_a_plain_run_prints_the_caveats_to_stdout(tmp_path):
    """The caveats reached --help, --json and the report page, but not stdout
    -- which is where most runs are actually read."""
    src = tmp_path / "src.json"
    src.write_text(json.dumps({"candles": json.loads(BARS.read_text())}))
    out = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "backtest.py"),
         "--source", str(src), "--balance", "10000"],
        capture_output=True, text=True, timeout=300, check=True).stdout
    assert "NOT MODELLED:" in out
    for phrase in ("daily-loss brake", "kill switch", "news blackout"):
        assert phrase in out, f"stdout never names the {phrase}"
