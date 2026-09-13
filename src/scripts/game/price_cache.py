import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pandas as pd

from scripts.database import database
from scripts.game import indicators

MAX_CACHED_TICKERS = 24

PREFETCH_WORKERS = 8

_frames: "OrderedDict[str, pd.DataFrame]" = OrderedDict()
_lock = threading.Lock()
_load_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()

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
            if ticker in _frames:
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
    """Warm several tickers at once, ignoring duplicates and anything that has no rows.

    Only the misses are fetched: this runs at the top of every clock tick, and a tick on a
    warm session has nothing to load. Spinning up a pool to have four threads look in a
    dictionary and then joining them was pure per-tick overhead.
    """
    unique = list(dict.fromkeys(t.upper() for t in tickers if t))
    missing = [ticker for ticker in unique if ticker not in _frames]
    if len(missing) < 2:
        for ticker in missing:
            load(ticker)
        return
    with ThreadPoolExecutor(max_workers=min(PREFETCH_WORKERS, len(missing))) as pool:
        list(pool.map(load, missing))


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
