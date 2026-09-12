"""Portfolio-level views: relative performance and the account's own equity curve.

The game screen used to answer "how is this one symbol doing" and nothing else. A session
is a basket, though, and the questions a basket raises are different: are my holdings
actually different, or the same bet repeated? And is the account up, regardless of which
line moved?

Both answers are derived from data the session already has - the price frames and the
trade ledger - so nothing here needs a snapshot table and a session that predates this
code still gets a full curve.
"""

from datetime import date, datetime, timedelta

from scripts.game import price_source

# Enough to see a trend on screen without turning the payload into a spreadsheet.
DEFAULT_BASKET_WINDOW = 180
MAX_BASKET_WINDOW = 1000


def _as_date(value) -> date:
    return value if isinstance(value, date) else datetime.strptime(str(value), "%Y-%m-%d").date()


def _iso(value) -> str:
    return _as_date(value).isoformat()


def _previous_day(day: str) -> str:
    return (_as_date(day) - timedelta(days=1)).isoformat()


def _rebased(frame) -> list[dict]:
    """Closes as an index, 100 at the first visible bar.

    Rebasing is the only way to put a $2 stock and a $500 stock on one axis and compare
    the thing that matters, which is the percentage move.
    """
    base = float(frame["close"].iloc[0])
    if not base:
        return []
    return [
        {"date": row.ts.isoformat(), "index": round(float(row.close) / base * 100, 3)}
        for row in frame.itertuples()
    ]


def _window_change(frame) -> float | None:
    first = float(frame["close"].iloc[0])
    last = float(frame["close"].iloc[-1])
    if not first:
        return None
    return round((last - first) / first * 100, 2)


def _closes_lookup(frames: dict) -> dict[str, tuple[list[str], list[float]]]:
    return {
        ticker: ([row.isoformat() for row in frame["ts"]], [float(v) for v in frame["close"]])
        for ticker, frame in frames.items()
    }


def _close_on_or_before(entry, day: str) -> float | None:
    """The last close at or before a date, by binary search over that symbol's own series."""
    if not entry:
        return None
    stamps, closes = entry
    position = _bisect_right(stamps, day) - 1
    return None if position < 0 else closes[position]


def _bisect_right(stamps: list[str], day: str) -> int:
    low, high = 0, len(stamps)
    while low < high:
        middle = (low + high) // 2
        if stamps[middle] <= day:
            low = middle + 1
        else:
            high = middle
    return low


def equity_curve(
    session: dict,
    trades: list[dict],
    frames: dict,
    dates: list[str],
    expenses: list[dict] | None = None,
) -> list[dict]:
    """Net worth per session date, rebuilt from the trade ledger and the bank's charges.

    Holdings only change when a trade happens, so the account's value on any day is the
    running cash plus what was held that day at that day's close. That makes the curve
    exact for any window, including ones the player has already scrolled past. Bills are
    replayed the same way - leave them out and the curve drifts away from the cash balance
    on screen by exactly the rent.
    """
    if not dates:
        return []

    lookups = _closes_lookup(frames)
    holds: dict[str, float] = {ticker: 0.0 for ticker in frames}
    cash = float(session["starting_cash"])

    ordered = sorted(trades, key=lambda trade: _iso(trade["trade_date"]))
    by_date: dict[str, list[dict]] = {}
    for trade in ordered:
        by_date.setdefault(_iso(trade["trade_date"]), []).append(trade)

    # Bills fall on calendar days, and the curve is drawn on trading days. Keying charges
    # by date and looking each day up would silently drop every bill that landed on a
    # weekend, so they are drained in date order onto the first plotted day at or after
    # them instead.
    charges = sorted(
        ((_iso(charge["due_date"]), float(charge["amount"])) for charge in expenses or []),
        key=lambda row: row[0],
    )
    charge_index = 0

    def drain_charges(through: str) -> None:
        nonlocal cash, charge_index
        while charge_index < len(charges) and charges[charge_index][0] <= through:
            cash -= charges[charge_index][1]
            charge_index += 1

    def settle(day: str) -> None:
        nonlocal cash
        for trade in by_date.get(day, []):
            shares = float(trade["shares"])
            price = float(trade["price"])
            ticker = trade["ticker"]
            if trade["side"] == "BUY":
                holds[ticker] = holds.get(ticker, 0.0) + shares
                cash -= shares * price
            else:
                holds[ticker] = holds.get(ticker, 0.0) - shares
                cash += shares * price

    # Everything that happened before the window still counts toward what is held today.
    for day in sorted(by_date):
        if day < dates[0]:
            settle(day)
    drain_charges(_previous_day(dates[0]))

    curve: list[dict] = []
    for day in dates:
        settle(day)
        drain_charges(day)
        value = 0.0
        for ticker, held in holds.items():
            if held:
                price = _close_on_or_before(lookups.get(ticker), day)
                if price is not None:
                    value += held * price
        curve.append(
            {
                "date": day,
                "net_worth": round(cash + value, 2),
                "cash": round(cash, 2),
                "market_value": round(value, 2),
            }
        )
    return curve


def basket(
    session: dict,
    trades: list[dict],
    window_days: int | None = None,
    tickers: list[str] | None = None,
    expenses: list[dict] | None = None,
) -> dict:
    """Every watched symbol rebased to 100, plus the account's equity over the same window."""
    window = int(window_days or DEFAULT_BASKET_WINDOW)
    window = max(20, min(window, MAX_BASKET_WINDOW))

    sim_date = _as_date(session["sim_date"])
    sources = price_source.for_watchlist(session, tickers)

    frames: dict = {}
    series: list[dict] = []
    dates: set[str] = set()
    for ticker, source in sources.items():
        frame = source.history_through(sim_date)
        if frame.empty:
            continue
        tail = frame.tail(window)
        frames[ticker] = tail
        dates.update(row.isoformat() for row in tail["ts"])
        # Whether the bars on screen include invented ones. A forked session is still
        # showing real history until its window reaches the fork, and labelling those bars
        # "simulated" would be wrong.
        fork = getattr(getattr(source, "config", None), "fork_date", None)
        series.append(
            {
                "ticker": ticker,
                "simulated": bool(fork and tail["ts"].iloc[-1] >= fork),
                "change_pct": _window_change(tail),
                "last_close": float(tail["close"].iloc[-1]),
                "points": _rebased(tail),
            }
        )

    axis = sorted(dates)
    series.sort(key=lambda row: (row["change_pct"] is None, -(row["change_pct"] or 0)))

    return {
        "session_id": str(session["id"]),
        "sim_date": sim_date.isoformat(),
        "window_days": window,
        "series": series,
        "equity": equity_curve(session, trades, frames, axis, expenses),
        "starting_cash": float(session["starting_cash"]),
    }
