"""Where a session's prices come from.

There are two kinds of session. A replay session reads real ingested history through
the shared price cache. A simulated session has forked off the end of that history
into its own generated future, and reads real bars up to the fork plus synthetic bars
after it.

Both kinds expose the same handful of methods, so engine.py asks a session for a price
source once and then stops caring which sort it got.
"""

import threading
from datetime import date, timedelta
from typing import Iterable

import pandas as pd

from scripts.database import database
from scripts.game import price_cache, simulation


class RealSource:
    """Real history, shared across sessions and cached per ticker by price_cache."""

    kind = "real"

    def __init__(self, ticker: str) -> None:
        self.ticker = ticker.upper()

    def history_through(self, sim_date: date) -> pd.DataFrame:
        return price_cache.history_through(self.ticker, sim_date)

    def close_on(self, sim_date: date) -> float | None:
        return price_cache.close_on(self.ticker, sim_date)

    def next_trading_day(self, after: date) -> date | None:
        return price_cache.next_trading_day(self.ticker, after)

    def first_trading_day(self, on_or_after: date) -> date | None:
        return price_cache.first_trading_day(self.ticker, on_or_after)

    def trading_days_between(self, start: date, end: date) -> int:
        return price_cache.trading_days_between(self.ticker, start, end)

    def bounds(self) -> dict | None:
        return price_cache.bounds(self.ticker)

    def describe(self) -> dict:
        return {"mode": "real", "ticker": self.ticker}


class SimulatedSource:
    """Real bars up to the fork date, then this session's own generated bars.

    Indicators are computed over the concatenation, so SMA/RSI/MACD cross the fork
    smoothly instead of restarting on the first synthetic day.
    """

    kind = "simulated"

    def __init__(self, session_id: str, row: dict) -> None:
        self.session_id = session_id
        self.config = simulation.SimulationConfig.from_row(row)
        self.ticker = self.config.ticker
        self._lock = threading.Lock()
        self._frame: pd.DataFrame | None = None
        self._dates: pd.Series | None = None

    # --- internals --------------------------------------------------------

    def _build(self) -> pd.DataFrame:
        # The *whole* real series, not a lookback window: the chart and the indicators
        # have to span the real days a session replays, not just the last year before the
        # fork. price_cache already holds the per-ticker frame, so this costs no query,
        # and simulation keeps the splice-and-recompute result for the next request.
        real = price_cache.history_through(self.ticker, self.config.fork_date)
        return simulation.merged_frame(self.session_id, self.config, real)

    def _merged(self) -> pd.DataFrame:
        if self._frame is None:
            with self._lock:
                if self._frame is None:
                    self._frame = self._build()
        return self._frame

    def _timeline(self) -> pd.Series:
        """Every session date this symbol has, real then generated, and nothing else.

        Deliberately separate from `_merged`: computing SMA/RSI/MACD over a decade of
        bars is what the chart costs, and asking for it just to answer "what is the next
        trading day" built ten full indicator frames to start a ten-symbol session.
        """
        if self._dates is None:
            with self._lock:
                if self._dates is None:
                    real = price_cache.history_through(self.ticker, self.config.fork_date)
                    future = simulation.future_frame(self.session_id, self.config)
                    stamps = [real["ts"]] if not real.empty else []
                    if not future.empty:
                        stamps.append(future["ts"])
                    if not stamps:
                        self._dates = pd.Series(dtype="object")
                    else:
                        combined = pd.concat(stamps, ignore_index=True)
                        self._dates = (
                            combined.sort_values().drop_duplicates().reset_index(drop=True)
                        )
        return self._dates

    def refresh(self) -> None:
        """Drop the caches after the future changed (a shock, or a longer horizon)."""
        with self._lock:
            self._frame = None
            self._dates = None

    def _through(self, sim_date: date) -> pd.DataFrame:
        frame = self._merged()
        if frame.empty:
            return frame
        if sim_date > frame["ts"].iloc[-1]:
            # The player walked off the end of the generated horizon; roll more bars.
            self.config = simulation.extend_horizon(self.session_id, self.config)
            self.refresh()
            frame = self._merged()
        # The frame is sorted by ts, so a binary search beats filtering the whole thing.
        return frame.iloc[: int(frame["ts"].searchsorted(sim_date, side="right"))]

    # --- the same surface RealSource offers -------------------------------

    def history_through(self, sim_date: date) -> pd.DataFrame:
        return self._through(sim_date)

    def close_on(self, sim_date: date) -> float | None:
        frame = self._merged()
        if frame.empty:
            return None
        position = int(frame["ts"].searchsorted(sim_date, side="left"))
        if position < len(frame) and frame["ts"].iloc[position] == sim_date:
            return float(frame["close"].iloc[position])
        return None

    def next_trading_day(self, after: date) -> date | None:
        dates = self._timeline()
        if dates.empty:
            return None
        position = int(dates.searchsorted(after, side="right"))
        return None if position >= len(dates) else dates.iloc[position]

    def first_trading_day(self, on_or_after: date) -> date | None:
        dates = self._timeline()
        if dates.empty:
            return None
        position = int(dates.searchsorted(on_or_after, side="left"))
        return None if position >= len(dates) else dates.iloc[position]

    def trading_days_between(self, start: date, end: date) -> int:
        dates = self._timeline()
        if dates.empty:
            return 0
        return max(
            0,
            int(dates.searchsorted(end, side="right"))
            - int(dates.searchsorted(start, side="right")),
        )

    def bounds(self) -> dict | None:
        dates = self._timeline()
        if dates.empty:
            return None
        return {
            "first_day": dates.iloc[0],
            "last_day": dates.iloc[-1],
            "row_count": len(dates),
        }

    def describe(self) -> dict:
        return {"mode": "simulated", **self.config.describe()}


def for_session(session: dict, ticker: str) -> RealSource | SimulatedSource:
    """Pick the right source for one symbol of a session.

    Falls back to real data if the simulation row has gone missing, so a half-deleted
    session cannot 500 the game.
    """
    session_id = str(session["id"])
    row = database.get_simulation(session_id, ticker)
    if row:
        return SimulatedSource(session_id, row)
    return RealSource(ticker)


def for_watchlist(
    session: dict, tickers: Iterable[str] | None = None
) -> dict[str, RealSource | SimulatedSource]:
    """Every source for a session, keyed by ticker, in one round trip to the DB.

    `tickers` lets a caller that already holds the watchlist - every caller that came
    through `load_session_bundle` does - skip re-reading it from the database.
    """
    session_id = str(session["id"])
    rows = {row["ticker"]: row for row in database.get_simulations(session_id)}
    watchlist = list(tickers) if tickers else database.session_tickers(session_id)
    return {
        ticker: SimulatedSource(session_id, rows[ticker])
        if ticker in rows
        else RealSource(ticker)
        for ticker in watchlist
    }


def fork_session(session_id: str, ticker: str, fork_date: date, **overrides) -> SimulatedSource:
    """Create a symbol's simulated future, or lengthen one it already has.

    A future is part of the session's identity once the player has traded in it. Forking
    again therefore reuses the stored seed, anchor and fork date and only honours
    `horizon_days` - re-rolling the seed would silently rewrite candles the player has
    already seen and acted on.
    """
    existing = database.get_simulation(session_id, ticker)
    if existing:
        current = simulation.SimulationConfig.from_row(existing)
        config_ = simulation.replace_horizon(
            current, int(overrides.get("horizon_days") or current.horizon_days)
        )
    else:
        # Slice the cached frame instead of re-fetching a year of bars. Same window the
        # direct query used, without sending eleven thousand rows over the network twice.
        window = price_cache.history_through(ticker, fork_date)
        real = window[window["ts"] >= fork_date - timedelta(days=400)]
        anchor = None if real.empty else float(real["close"].iloc[-1])
        if anchor is None:
            raise ValueError(f"no real history for {ticker} at or before {fork_date}")
        config_ = simulation.build_config(
            ticker=ticker, fork_date=fork_date, anchor_price=anchor, frame=real, **overrides
        )

    database.create_simulation(session_id, **config_.persist_fields())
    source = SimulatedSource(session_id, config_.persist_fields())
    simulation.ensure_future(session_id, config_)
    # Unconditional: the stored config just changed, and a cached frame was built from the
    # old one even if every bar it holds happens to be the same.
    simulation.invalidate(session_id, ticker)
    return source
