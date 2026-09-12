import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pandas as pd

from scripts.database import database
from scripts.game import indicators

# Bounded on purpose. A full-history frame is around eleven thousand rows and a megabyte,
# and the catalog holds 108 symbols, so an unbounded cache would happily hold a hundred
# megabytes of frames nobody is looking at.
MAX_CACHED_TICKERS = 24

# Tickers are warmed in parallel. The frames come from a database on the far side of the
# network, so filling a ten-symbol session one round trip at a time was most of the time
# it took to start a game.
PREFETCH_WORKERS = 8

_frames: "OrderedDict[str, pd.DataFrame]" = OrderedDict()
_lock = threading.Lock()
# One lock per ticker rather than one for the whole cache: the fetch happens inside it,
# and a single lock would serialise every prefetch back into the slow path.
_load_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()

# Wide enough for the oldest listing we can download, so "start as far back as possible"
# is not silently clipped by this cache's own bounds.
EPOCH_START = date(1800, 1, 1)
EPOCH_END = date(2200, 1, 1)


def _lock_for(ticker: str) -> threading.Lock:
    with _locks_guard:
        lock = _load_locks.get(ticker)
        if lock is None:
            lock = _load_locks[ticker] = threading.Lock()
        return lock


def load(ticker: str) -> pd.DataFrame:
    ticker = ticker.upper()
    cached = _frames.get(ticker)
    if cached is not None:
        with _lock:
            _frames.move_to_end(ticker)
        return cached
    with _lock_for(ticker):
        cached = _frames.get(ticker)
        if cached is not None:
            return cached
        frame = indicators.compute_all(
            database.fetch_price_history(ticker, EPOCH_START, EPOCH_END)
        )
        with _lock:
            _frames[ticker] = frame
            while len(_frames) > MAX_CACHED_TICKERS:
                _frames.popitem(last=False)
        return frame


def prefetch(tickers) -> None:
    """Warm several tickers at once, ignoring duplicates and anything that has no rows."""
    unique = list(dict.fromkeys(t.upper() for t in tickers if t))
    if len(unique) < 2:
        for ticker in unique:
            load(ticker)
        return
    with ThreadPoolExecutor(max_workers=min(PREFETCH_WORKERS, len(unique))) as pool:
        list(pool.map(load, unique))


def _upper_bound(frame: pd.DataFrame, sim_date: date) -> int:
    """Index of the first row after sim_date.

    A binary search rather than frame[frame.ts <= d]: the frame is already sorted by ts,
    and this runs once per symbol per simulated day, so the linear scan over a full
    history was the hottest thing in an advance. Full history is over eleven thousand
    rows, and a fast-forward walks thousands of days.
    """
    return int(frame["ts"].searchsorted(sim_date, side="right"))


def history_through(ticker: str, sim_date: date) -> pd.DataFrame:
    frame = load(ticker)
    if frame.empty:
        return frame
    return frame.iloc[: _upper_bound(frame, sim_date)]


def close_on(ticker: str, sim_date: date) -> float | None:
    frame = load(ticker)
    if frame.empty:
        return None
    position = int(frame["ts"].searchsorted(sim_date, side="left"))
    if position < len(frame) and frame["ts"].iloc[position] == sim_date:
        return float(frame["close"].iloc[position])
    return None


def next_trading_day(ticker: str, after: date) -> date | None:
    frame = load(ticker)
    if frame.empty:
        return None
    position = _upper_bound(frame, after)
    return None if position >= len(frame) else frame["ts"].iloc[position]


def first_trading_day(ticker: str, on_or_after: date) -> date | None:
    frame = load(ticker)
    if frame.empty:
        return None
    position = int(frame["ts"].searchsorted(on_or_after, side="left"))
    return None if position >= len(frame) else frame["ts"].iloc[position]


def trading_days_between(ticker: str, start: date, end: date) -> int:
    frame = load(ticker)
    if frame.empty:
        return 0
    return max(0, _upper_bound(frame, end) - _upper_bound(frame, start))


def bounds(ticker: str) -> dict | None:
    frame = load(ticker)
    if frame.empty:
        return None
    return {
        "first_day": frame["ts"].iloc[0],
        "last_day": frame["ts"].iloc[-1],
        "row_count": len(frame),
    }
