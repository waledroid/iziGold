### Task 5: Documentation

**Files:**
- Modify: `.claude/agents/izi.md`

- [ ] **Step 1: Document the lane, the numbers, and the bug**

Add a section covering:
- `--strategy ht|qf|both` (default `both`); `ht` reproduces every pre-2026-08-20 study.
- QuickFlip's rules and defaults (13:30 server, 10% of daily ATR, 90-min window, 0.25% risk, one trade/day, entry = close back inside the box after a sweep — NOT hammer/engulfing, which are unmeasured).
- **The time-convention trap, prominently**: candle `t` is server wall-clock; `hhmm()` reads it with no offset; server hour 00 is empty because it is the daily break. A +3h shift invalidated the first analysis and nearly put a mislabelled result into live trading.
- That QuickFlip is a **paid experiment at reduced size**, not a validated edge: 46 half-hours were searched, the slots passing a split test pass by ~$0.03–0.13/oz in the older half, and the pattern is the same recent-half-only shape seen across every study this week. Review after ~2 months of live logs.
- The three golden pins and what each guards.

- [ ] **Step 2: Commit**

```bash
git add .claude/agents/izi.md
git commit -m "docs(izi): the quickflip lane, its evidence, and the server-time trap"
```

---

## Phase 2 (separate plan, after this lands)

The EA side: `Strategies/QuickFlipNy.mqh`, a second `CTradeManager` on
`MagicNumber + QuickFlipMagicOffset`, attribution across the deal-history call
sites, and the hard requirement that `RiskManager` counts BOTH magics in the
daily-loss brake, drawdown and exposure accounting — otherwise QuickFlip's
losses are invisible to the 3% brake. That plan is written once this phase's
rules are proven in code.
