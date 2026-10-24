"""Database repository mixins and helpers."""

from __future__ import annotations

from app.repos.agent import AgentRepoMixin
from app.repos.backtest import BacktestRepoMixin
from app.repos.base import row_to_dict
from app.repos.candles import CandleRepoMixin
from app.repos.kv_profile import KvProfileRepoMixin, PROFILE_FIELDS, profile_completion
from app.repos.proposals import ProposalRepoMixin
from app.repos.signals import SignalRepoMixin
from app.repos.trades import TradeRepoMixin

__all__ = [
    "AgentRepoMixin",
    "BacktestRepoMixin",
    "CandleRepoMixin",
    "KvProfileRepoMixin",
    "PROFILE_FIELDS",
    "ProposalRepoMixin",
    "SignalRepoMixin",
    "TradeRepoMixin",
    "profile_completion",
    "row_to_dict",
]
