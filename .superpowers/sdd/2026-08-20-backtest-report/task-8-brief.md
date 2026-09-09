### Task 8: Documentation — izi.md and the README line

`CLAUDE.md` makes this law: behaviour changes update `.claude/agents/izi.md`.

**Files:**
- Modify: `.claude/agents/izi.md`
- Modify: `docs/superpowers/plans/2026-08-20-backtest-report.md` (tick the boxes)

- [ ] **Step 1: Find the backtest section**

```bash
grep -n -i 'backtest' .claude/agents/izi.md | head -20
```

- [ ] **Step 2: Document the new behaviour**

Update (or add) the backtest section to state, in izi.md's voice:

- `scripts/backtest.py` now defaults to the **strict 3-bar entry window** — the live EA's law since 2026-08-16. `--loose-window` reproduces studies run before 2026-08-20; every pre-2026-08-20 study in `.superpowers/` was a LOOSE run.
- `--balance` refuses below $500 and warns below $2,000. The binding constraint is the 0.01 minimum lot, not spread: measured over 12 months of M5, entries clamp 88.7% at $500, 50.8% at $1,200, 10.2% at $4,000, 0.4% at $10,000. Every run reports its clamp rate and the risk actually taken.
- `--json PATH` writes the run artifact; `--web PATH` writes a self-contained HTML report (chart + trade boxes + stepped stop). The template is `service/app/static/backtest_report.html`, shared with the Mini App tab planned in spec §4.
- The replay does NOT model the daily-loss brake, the kill switch, or the news blackout — and now says so in `--help` and on every report.
- `service/tests/test_backtest_golden.py` pins replay behaviour over a fixed slice; if it fails and you did not intend to change a rule, you broke one.

- [ ] **Step 3: Record the headline result**

Add the strict-vs-loose 12-month comparison measured in Task 3 Step 8 to izi.md's history section — this is the first evidence of what the strict-entry fix is worth over a year.

- [ ] **Step 4: Commit**

```bash
git add .claude/agents/izi.md docs/superpowers/plans/2026-08-20-backtest-report.md
git commit -m "docs(izi): backtest defaults, balance floors, --json/--web report"
```

---

## Later phases (not this plan)

- **Spec §4 — Mini App tab.** Serve the artifact at `/api/backtest` behind `viewer_ok()`, add a 📊 Backtest tab reusing `backtest_report.html`'s drawing code, plus the balance input with $1k/$4k/$10k/$25k presets and a `[Run backtest]` button (a 12-month replay takes ~3.6 s). Enable `GZipMiddleware` there — the artifact is 5–7 MB over the wire.
- **Spec §6 — day/month report views.** Refactor `_report_month()` / `_report_day()` in `service/app/miniapp.py` to take a list of baskets instead of a sqlite connection, capture a golden test of today's live output first, then feed them backtest trades so the live and backtest reports share one shaper and one renderer.
