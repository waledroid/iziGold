#!/usr/bin/env python3
"""Turn a backtest run artifact into ONE self-contained HTML file.

Self-contained on purpose: the report is an artifact you keep, mail, and
compare against next month's. It must open from disk with no server, no
network and no build step, so the chart library, the page and the run data are
all inlined.

The template lives in service/app/static/ so the Mini App tab (spec section 4)
can reuse the same drawing code later instead of growing a second one.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "service" / "app" / "static" / "backtest_report.html"
LIB = ROOT / "service" / "app" / "static" / "vendor" / \
    "lightweight-charts.standalone.production.js"


def write_report(artifact, out_path):
    """artifact: the dict from backtest.build_run_json(). Writes out_path."""
    html = TEMPLATE.read_text(encoding="utf-8")
    meta = artifact.get("meta", {})
    title = f"XAUUSD backtest — {meta.get('bars', 0)} bars, " \
            f"{len(artifact.get('trades', []))} trades"
    # Substitute data LAST: the artifact is arbitrary JSON and must never be
    # re-scanned for placeholder tokens.
    html = html.replace("__TITLE__", title)
    html = html.replace("__LIB__", LIB.read_text(encoding="utf-8"))
    html = html.replace("__DATA__", json.dumps(artifact, separators=(",", ":")))
    Path(out_path).write_text(html, encoding="utf-8")
