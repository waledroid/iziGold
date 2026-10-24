"""Base classes and utilities for database repositories."""

from __future__ import annotations


def row_to_dict(cur, row):
    """Convert sqlite3 tuple row to dict using cursor description."""
    if row is None:
        return None
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))
