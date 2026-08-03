# Bot polish — pinned command reference, /status connection state, blocked reasons

**Date:** 2026-08-03 (approved in-session)

1. **Pinned message = static command reference.** The 60s live-status editing
   loop stops. `pinned_tick` now maintains (create-pin-once, self-heal if
   deleted/unpinned) a static message:
   - 📌 header, then one line per command: /status (snapshot + EA
     connection), /mode (AUTO/MANUAL toggle), /strategy (switch strategy),
     /config (current settings), plus two lines explaining proposals
     (🟢 take / 🔴 skip, valid while the strategy holds the stance).
   - The pinned_editor loop interval relaxes to 300s (only self-healing;
     content is static — edit only when the stored text version differs,
     kv `pinned_help_version` guards rewrite).
2. **/status first line: EA connection.** From `app.state.latest_heartbeat`
   (ts, HeartbeatRequest): age ≤ 30s → `EA: 🟢 connected (Xs ago)`; else
   `EA: 🔴 disconnected (last seen Xm ago)`; never seen → `EA: 🔴 never
   connected`. Rest of /status unchanged.
3. **Blocked proposals carry the real reason.** EA execute branch: before
   OnSignal, call `g_risk.CanEnter(why)`; if false →
   `PostProposalResult(cmdId, false, why)` (reasons are fixed EA literals —
   safe for the hand-built JSON). If CanEnter passes but OnSignal still
   fails → keep the generic "blocked by risk checks". Live-account guard
   (AllowLiveTrading) stays first, unchanged.

Tests: pinned_tick static behavior (create once, no edit when version
matches, re-pin after deletion), /status connection lines (fresh/stale/none
heartbeat), existing suites stay green. EA: compile 0/0.
Out of scope: any change to proposal lifecycle, /ui endpoints, alerts.
