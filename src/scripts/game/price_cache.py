import threading
from datetime import date

import pandas as pd

from scripts.database import database
from scripts.game import indicators

_frames: dict[str, pd.DataFrame] = {}
_lock = threading.Lock()

EPOCH_START = date(1990, 1, 1)
EPOCH_END = date(2100, 1, 1)


def load(ticker: str) -> pd.DataFrame:
    ticker = ticker.upper()
    cached = _frames.get(ticker)
    if cached is not None:
        return cached
    with _lock:
        if ticker not in _frames:
            frame = database.fetch_price_history(ticker, EPOCH_START, EPOCH_END)
            _frames[ticker] = indicators.compute_all(frame)
        return _frames[ticker]


def invalidate(ticker: str | None = None) -> None:
    with _lock:
        if ticker:
            _frames.pop(ticker.upper(), None)
        else:
            _frames.clear()


def history_through(ticker: str, sim_date: date) -> pd.DataFrame:
    frame = load(ticker)
    return frame[frame["ts"] <= sim_date]


def close_on(ticker: str, sim_date: date) -> float | None:
    frame = load(ticker)
    match = frame.loc[frame["ts"] == sim_date, "close"]
    return None if match.empty else float(match.iloc[0])


def next_trading_day(ticker: str, after: date) -> date | None:
    frame = load(ticker)
    later = frame.loc[frame["ts"] > after, "ts"]
    return None if later.empty else later.iloc[0]


def first_trading_day(ticker: str, on_or_after: date) -> date | None:
    frame = load(ticker)
    later = frame.loc[frame["ts"] >= on_or_after, "ts"]
    return None if later.empty else later.iloc[0]


def trading_days_between(ticker: str, start: date, end: date) -> int:
    frame = load(ticker)
    return int(((frame["ts"] > start) & (frame["ts"] <= end)).sum())


def bounds(ticker: str) -> dict | None:
    frame = load(ticker)
    if frame.empty:
        return None
    return {
        "first_day": frame["ts"].iloc[0],
        "last_day": frame["ts"].iloc[-1],
        "row_count": len(frame),
    }
