"""--help must state what the replay does NOT model. A result whose limits are
invisible gets trusted further than it deserves."""
from tests.test_backtest_golden import _load_bt


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
