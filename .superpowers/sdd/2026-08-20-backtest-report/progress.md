# SDD ledger — plan: docs/superpowers/plans/2026-08-20-backtest-report.md

Spec: docs/superpowers/specs/2026-08-20-backtest-report-design.md (read)
Branch: feat/backtest-report (from main @ 5e0cce8)

## Pre-flight conflict scan

| Rows | Produces → Consumes | Finding |
|---|---|---|
| T1 → T2,T3,T4,T5,T6,T7 | `_load_bt()`, `bt.run()` 4-tuple | OK — T4 publishes sizing via the `run.sizing` function attribute (the file's existing `run.bias_flips` pattern) precisely so run()'s arity never changes and T1's golden keeps calling it |
| T1 ↔ T3 | golden pins LOOSE; T3 flips default to strict | Conflict, resolved IN PLAN: T3 Step 7 sets `bt.STRICT_WINDOW = False` inside `_replay()` so the pin stays like-for-like |
| T2 → T6 | `legs[].t`, `stop_history`, `tp`, `exit_t`, `bal_after`, `opened_t` | OK — every field T6's `build_run_json` reads is produced by T2 |
| T3 → T4,T5,T6,T7 | `build_parser()` | OK — T4 calls it in main(), T5 regroups inside it, T6/T7 add `--json`/`--web` to its Output group |
| T4 → T6 | `run.sizing` dict | OK — keys clamp_pct/risk_median/risk_p90 match T6's stats block |
| T5 → T6,T7 | `CAVEATS` list | OK — T6 emits `meta.caveats`, T7 template renders `m.caveats` |
| T6 → T7 | `build_run_json()` → `write_report(artifact, path)` | OK — T7's tests build the artifact through T6's function |
| T2,T3,T4,T5,T6,T7 | all modify scripts/backtest.py | OK — sequential, no parallel dispatch; each ends with the golden test as the gate |
| T3 self | Step 4 moves args verbatim; T5 later regroups them | OK — sequential, not contradictory |
| T7 self | template external refs vs "self-contained" test | OK — template references no http(s) assets; `LineType.WithSteps` and `attachPrimitive` both verified present in the vendored v4.2.3 build (overlay canvas used regardless) |
| T6 self | `TF_NAME` | FIXED pre-flight: the module global is `TF` (scripts/backtest.py:308); plan corrected before dispatch |
| T1..T7 self | tests import path | FIXED pre-flight: `service/tests/` is a package (`__init__.py`), so all test imports use `from tests.test_backtest_golden import ...` |

## Rulings

Ruling: data-dependent test thresholds (T2 "a basket with adds exists", T3 "strict < loose", T4 "clamp gap > 20pp") are asserted against a 6,000-bar slice, but were measured over 12 months. If one fails ON THE SLICE, the implementer widens or moves the fixture slice until the phenomenon is present — never weakens or deletes the assertion. Rationale: the assertions encode real, measured behaviour; a slice that cannot show it is the wrong slice. Cost if wrong: a larger fixture file (still < 1 MB) and one extra regeneration of the golden file.

Ruling: implementers run on sonnet (the plan carries complete code, but each task ends in multi-step live verification); task reviewers on sonnet; the final whole-branch review on opus. Cost if wrong: a cheaper tier would have sufficed for Tasks 5 and 8 — a modest token overspend, no correctness risk.

## Progress
Task 1: dispatched (implementer sonnet, BASE 5e0cce8)
Task 1: complete pending review (commit 24291f2; 114 trades pinned, 69 multi-leg; break-check passed)

Ruling: Task 1's report shows the strict window filtering 105 of 114 loose entries on the 21-day slice — 0.43 baskets/day vs the live system's observed ~3/day (2026-08-19: 3 baskets, 15 signals). I verified the replay's one-shot semantics against the EA source (HalfTrendEma.mqh:181 `m_barsSinceFlip == m_confirm` vs backtest.py:747 `i - last_flip == CONFIRM_CLOSES`) and they MATCH, so this is not a transcription bug. But a 7x gap between replayed and live entry rates is a fidelity question worth answering, so Task 3 gains one verification step: measure strict trades/day over 12 months and compare with the live rate, reporting the gap rather than explaining it away. Cost if wrong: one extra measurement in Task 3 (~5 min); the alternative is shipping a default whose entry rate nobody checked against reality.
Task 1: complete (commits 5e0cce8..24291f2, review clean — both verdicts PASS)
Task 1: minor (deferred): test_backtest_golden.py:9 docstring advertises a `--regen-golden` pytest option that does not exist (my plan's defect, copied verbatim). FOLDED INTO TASK 3, which already edits this file.
Task 1: minor (deferred): task-1-report.md:31 misdescribes the Step 6 failure mode (count moved to 115; report says per-trade values moved). Evidence stands, description is wrong. No code impact.
Task 1: minor (deferred): the golden digest omits leg timestamps and per-leg oz; a change that altered timing/sizing while leaving pl and exit identical could slip past. Low probability (pl derives from oz and px). Revisit if a later task changes sizing.
Note: implementer reported strict filtering "105 of 114"; reviewer's read was "114 -> 87". The two disagree — Task 3 measures it properly over 12 months and settles it.
Task 2: implementer DONE (commit bf3b7e4; records 6/6, golden still 1/1, full suite 444 passed). Under review.
Task 2 -> downstream: `tp` is Optional[float] for a reason beyond fixed mode — 3/114 fixture trades stop out on the first bar before any target is computed. Task 6's build_run_json already emits `r2(t.get("tp"))` (None-safe) and Task 7's template already guards `if (tr.tp != null)`, so both are covered; no plan change needed.
Task 2: complete (commits 24291f2..bf3b7e4, review clean — both verdicts PASS)
Task 2: minor (deferred): unused `import pathlib` in test_backtest_records.py:4 (my brief's snippet, not implementer error).
Task 2: minor (deferred): task-2-report.md key enumeration omits the pre-existing `when` key. Documentation only.
Note: reviewer confirmed my brief was internally inconsistent — `exit_t` appears in Step 6's code but not in the Produces list. Implementer followed the code and flagged it. Plan defect, harmless, no rework.
Task 3: dispatched (implementer sonnet, BASE bf3b7e4)
Task 3: implementer DONE (commit 639417d; 4 new tests + golden pass; full suite 448). Under review.
Task 3 FINDING (material, for the owner): over 365 days at $4,000 the STRICT entry window is WORSE than loose — -$2,874.96 (-71.9%, max DD $3,035.57) vs loose -$1,998.59 (-50.0%, max DD $2,576.96). Strict is the EA's actual law (a correctness fix the owner requested), so this is not a reason to revert the default; it is the first thing the tool was built to tell us. Surface to the owner.
Task 3: entry-rate question from my Task 1 ruling is SETTLED. Fixture slice: loose 114 -> strict 87 (Task 1's report figure of "105 of 114" was simply wrong; the reviewer's 114->87 was right). Strict runs 4.69 trades/day over 12 months vs live ~3/day = 1.56x, plausibly the unmodelled brake/news/window. No 7x gap, no fidelity bug. My ruling to spend 5 minutes checking was cheap and it retired a real doubt.
Task 3: review returned PASS/PASS with 2 Medium + 1 Low.
Task 3: fix round 1/5 dispatched (resumed original implementer) — Medium "untested wiring line" (scripts/backtest.py:1188 `STRICT_WINDOW = not args.loose_window` is untested; a dropped `not` would pass the whole suite). Fix must be verified by mutation.

Ruling: CORRECTION — the "105 of 114" strict-filter figure was MY error, not Task 1's. The reviewer verified Task 1's report states loose 114 / strict 87 correctly, and that 105 is a separate, also-correct statistic (loose entries with no (entry_px, dir) match in the strict set, which counts re-priced survivors as well as filtered flips). My ledger paraphrase invented the "105 of 114 filtered" framing, and I then passed it into Task 3's dispatch, which baked the misattribution into commit 639417d's message. Decision: do NOT rewrite history for a commit-message paraphrase (churn with no code impact); correct it here, tell the implementer not to fix it, and carry the accurate numbers into Task 8's izi.md update. Cost if wrong: git log retains one unfair sentence about Task 1; the numbers in it are correct.

Task 3: minor (deferred): `--strict-window` is argparse.SUPPRESS'd, so --help gives no signal the flag exists or is now inert (my plan's Step 5 specified this). Only discoverable via the module docstring. Final review should decide whether it earns a visible "(accepted, no-op)" line.
Task 3: fix round 1/5 (1 addressed pending re-review; commits 639417d..d815890; mutation check passed: inverted wiring -> new test FAILS, restored -> PASSES)
Task 3: complete (commits bf3b7e4..d815890, re-review ADDRESSED, no new breakage; suite 449 passed)

Ruling (AMENDS my pre-flight ruling): the pre-flight said "if a data-dependent assertion cannot be shown on the fixture slice, widen the slice, never weaken the test". That was written before Task 1's golden pin existed. Widening the slice NOW would force regenerating golden_trades.json, destroying the provenance of the very pin that guards Tasks 4-7. So from Task 4 onward the order reverses: the fixture is FROZEN. If Task 4's clamp-gap assertion cannot be shown on it, the implementer measures the real gap on the fixture and asserts a threshold below it, documenting the measured values in the test — never regenerates the golden. Risk is low (12-month clamp rates are 67.9% at $800 vs 0.1% at $25,000, so a wide gap is near-certain on any slice). Cost if wrong: one test asserts a smaller gap than the 20pp I guessed, with the real numbers written next to it.
Task 4: dispatched (implementer sonnet, BASE d815890)
Task 4: implementer DONE (commit 26627c5; balance tests 5/5, golden 1/1, full suite 454). Under review.
Task 4 measured on the FROZEN fixture (strict default now in force, so entry counts differ from the 12-month figures):
  $500 73.4% clamped (risk med 1.55%, p90 3.83%) | $800 54.8% (1.04/2.50) | $1,200 28.2% (0.84/1.67)
  $4,000 2.3% (0.91/0.98) | $10,000 0.0% (0.96/0.99) | $25,000 0.0% (0.98/1.00)
  My 20pp gap estimate held with margin (54.8pp measured), so the fixture-freeze ruling cost nothing.
  Fixture rates run BELOW the 12-month rates ($500: 73.4% vs 88.7%) — different window and now strict-filtered entries; flagged to the reviewer to confirm it is not a measurement bug.
Task 4: complete (commits d815890..26627c5, review PASS/STRONG, no fix round)
Task 4: minor (deferred): `risk_median = r[len(r)//2]` is the upper-middle element, not a true median on even-length samples (my brief's snippet). Cosmetic; final review to triage whether it is worth a statistics.median() swap.
Task 4: reviewer confirmed the fixture-vs-12-month clamp divergence is sample variance, not a wiring bug — corroborated by the apples-to-apples 12-month run at $1,200 landing at 47.0% against the 50.8% I measured pre-plan.
Task 5: dispatched (implementer sonnet, BASE 26627c5)
Task 5: implementer DONE (commit 6c62994; help tests 4/4, full suite 457 + the known pop-approved flake; parser inventory diff EMPTY across 37 args). Under review.
Task 5: complete (commits 26627c5..6c62994, review PASS/HIGH, no fix round). Reviewer independently reproduced the 37-argument inventory diff from 26627c5 vs HEAD (empty, 38 rows each side), confirmed SUPPRESS preserved on --strict-window, and fault-injected a stray flag to prove the orphan test actually catches one.
Task 5: minor (deferred): wrapped help= continuation lines not re-indented after the receiver rename (cosmetic, ast.parse clean).
Task 5: note: --window-start/--window-end sit in Experiments per my own brief, though they gate live EA trading hours rather than being a study toggle. Implementer followed the brief correctly. Final review may re-home them.
Task 6: dispatched (implementer sonnet, BASE 6c62994)
Task 6: implementer DONE (commit 1b7066b; json tests 6/6, golden 1/1, suite 463 + known flake). Under review.
Task 6 -> Task 7 design inputs: real 12-month artifact is 6.3 MB / 70,707 bars / 1,210 trades. Size is inside the 5-7 MB the plan predicted, but the TRADE count is ~4x my "few hundred" estimate. Task 7 draws every trade: 1,210 boxes redrawn on each visible-range change (fine) but ~3,600 chart markers (entry + adds + exit) is the real risk — Lightweight Charts degrades with thousands of markers. Task 7's dispatch must require a responsiveness check against the REAL artifact, not the 87-trade fixture, and permit marker thinning at wide zoom if it drags.
Task 6: complete (commits 6c62994..1b7066b, review PASS/PASS, no fix round). Reviewer independently confirmed indicator provenance (app.indicators, amplitude 4), end-to-end positional alignment (candles filtered once before run() and reused), tp:null in BOTH cases (3/88 stop-outs; 61/61 in fixed mode), and no divide-by-zero on an empty trade list.
Task 6: minor (deferred): no automated test pins tp:null for fixed entry mode (reviewer verified by hand). FOLDED INTO TASK 7, which depends on that invariant.
Task 6: note: ind.ema9/21/55/200 are display overlays fixed at those periods regardless of --ema-len, per spec. Task 7 must not assume ema55 is "the strategy's EMA".
Task 6: DOC DRIFT to fix in Task 8: the spec says the artifact is "~3-4 MB", the plan says 5-7 MB, and reality is 6.3 MB. My original 3-4 MB figure (also told to the owner) was wrong; the spec must carry the measured number.

Ruling: the plan's Task 7 Step 7 requires opening the report in a browser and checking it by eye — something a subagent cannot do. Node v22 is available, so I am splitting that verification: the implementer must prove everything provable WITHOUT eyes (node --check on the extracted page script; a headless stub-driven test asserting the series/marker/box geometry the page builds from a known artifact), and the genuinely visual check (does it LOOK like MT5) is performed by me and the owner on the real 12-month file. Cost if wrong: a visual defect survives to the owner's first look — which is exactly where the plan already placed that check, so no verification is lost, only relocated.
Task 7: dispatched (implementer sonnet, BASE 1b7066b)
Task 7: complete (commits 1b7066b..f3b2cdf, review PASS/STRONG, no fix round). Reviewer independently re-derived the box geometry, disabled the marker sort to confirm the negative test bites, and verified self-containment on the real 6.4 MB file.
Task 7: minor -> FOLDED INTO TASK 8: marker thinning above 300 trades is invisible on the page itself. The page already carries a caveats line and a warn box; a run that hides markers must say so there. Principle already in the plan: a model's limits travel with its output.
Task 7: minor -> TASK 8: spec still says the mitigation for chart weight is "a timeframe switcher, not a change of default" (spec:260) but we shipped marker thinning; spec:98 and plan:1075/1563 still carry the stale 3-4 MB / 5-7 MB sizes against a measured 6.4 MB.
Task 7: coordination -> TASK 8: Task 7 ALREADY wrote an accurate --web/--json/thinning section into izi.md:533-567 with the real numbers. Task 8 must reconcile/tighten it, never append a duplicate or overwrite it with stale figures.
Task 7: cosmetic (deferred, no action): the stepped-stop `seen` map is keyed by raw timestamp, so two baskets sharing a timestamp would silently drop one point. Impossible today (one position at a time); noted if overlapping baskets ever ship.
Task 8: dispatched (implementer sonnet, BASE f3b2cdf)
Task 8: implementer DONE (commits 52459d6, bdf29b7; smoke PASS, web 5/5, golden 1/1, suite 469). Under review.

Ruling: I dispatched Task 8's review and the final whole-branch review CONCURRENTLY rather than in sequence. Task 8 is documentation plus a one-line conditional on the page, so the chance its review forces a change that invalidates the final review's reading is small, and any finding from either can be folded into the single fix wave the process allows at the end. Cost if wrong: the final reviewer read a page whose disclosure line may move by a few characters — it would not change a merge verdict.
Task 8: complete (commits f3b2cdf..bdf29b7, review PARTIAL PASS — findings folded into the single final fix wave)
FINAL REVIEW (opus, whole branch 5e0cce8..bdf29b7): MERGE AFTER FIXES. 1 HIGH, 5 MEDIUM, 6 LOW, nits.

Ruling: my ledger triaged Task 7's `seen`-map collision as "cosmetic, impossible today (one position at a time)". That was WRONG and the final reviewer overturned it with measurement: the collision is not about overlapping baskets but back-to-back ones — run() opens a new basket in the same loop iteration that closed the previous one, so a reversal exit and the next entry share a timestamp. Live effect on the real 12-month run: 124 trades (7.2%) lose their initial stop point and 71 (4.1%) draw NO stop line at all. Accepting my own triage would have shipped a report that silently omits the stop path on 1 trade in 25 — the exact class of defect this tool exists to prevent. Fixing in the wave. Cost of my error if it had stood: invisible, which is what makes it the worst kind.

Ruling: fix `--chart` rather than delete it (M4). Task 2 recorded the entry times its comment says are "unknown"; using them is a smaller change than removing a flag someone may script against.
Ruling: keep --window-start/--window-end in Experiments (the spec agrees) but fix the group blurb, which falsely claims "live EA has none" (L10).
Ruling: re-measure the clamp table under the SHIPPED default (strict) and move the balance guidance to $10,000+, stating plainly that $4,000 still clamps ~1 entry in 6. The alternative — keeping $4,000 as "minimum for meaningful results" while the tool prints "results distorted" on that very run — is the tool disagreeing with itself in front of the owner.
Final fix wave: dispatched (opus, BASE bdf29b7)
Final fix wave: complete (commits bdf29b7..c14eadb, 6 commits). Scoped re-review: ALL findings ADDRESSED except H1 PARTIAL.

Ruling (PARKED, surfaced to the owner): H1's primary defect is fixed and independently reproduced — trades losing their INITIAL stop point went 124 -> 0. But the fixer's report claims "0 trades left with <=1 stop point"; the re-reviewer, using the original reviewer's counting rule (which it reproduced exactly at 71 pre-fix), measures 64 trades (3.7%) still ending with <=1 point post-fix. Cause is inherent to the agreed collision rule, not a new bug: a single-leg trade whose stop NEVER MOVED has exactly one history entry, and if its exit collides with the next basket's entry the "newer basket wins" rule correctly takes the carry point, leaving one point and therefore no visible line. Net effect: ~1 trade in 27 renders no stop path, down from 1 in 24. Decision: park rather than open a second fix wave (the process allows one), because it is bounded, strictly better than before, affects only trades whose stop never moved, and the exit reason remains visible in the trade table. The fixer's "0%" claim is wrong and is corrected here; it never reached izi.md or any committed doc. Cost if wrong: the owner sees no stop line on ~3.7% of trades and must consult the table for those; the fix (render a flat entry->exit line when a trade has a single stop point) is the first item of the follow-up phase.

Suite at branch head: 479 passed, 1 deselected. Smoke test: PASS (8 sections). Report regenerated from FIXED code at $10,000 (1.3% clamped) to /mnt/c/Users/aatanda/Desktop/backtest12m.html.
BRANCH READY: 17 commits, awaiting owner decision on merge. Workspace retained until then.
