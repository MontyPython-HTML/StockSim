"""Game rules for a portfolio run.

A session is a watchlist of symbols plus a wallet. The player picks the symbols up front,
trades any of them at the running date's close, and the clock advances one session at a
time across the whole watchlist.

Two things make the clock work the way it does:

* The session's window is the span where *every* watched symbol has data, so the player
  can always trade what they picked. Start is the latest first-day in the watchlist, end
  is the earliest last-day.
* The clock advances on the union of the watchlist's trading days, so a symbol with a
  holiday gap does not stall the run. Prices come from the most recent close at or before
  the current date, which keeps a thinly traded name tradeable.
"""

import logging
import random
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from decimal import Decimal

import config
from scripts.api import nessie
from scripts.database import database
from scripts.game import events, indicators, price_cache, price_source

DEFAULT_CHART_WINDOW = 180

# A guard for fast-forwarding: asking for thousands of days would otherwise return a
# signal list long enough to stall the browser drawing it. The UI batches five days at a
# time, so this never bites in normal play.
MAX_SIGNALS_PER_TICK = 40

# How many trailing bars signal detection is handed. The rules compare the last two
# bars of already-computed columns, so a handful is plenty and keeps an advance cheap.
SIGNAL_TAIL_ROWS = 5

log = logging.getLogger(__name__)

_ai_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ai")
_ai_inflight: set[tuple[str, str, str]] = set()
_ai_lock = threading.Lock()


class GameError(Exception):
    """bad ticker, insufficient funds, session over"""


class AIUnavailable(Exception):
    """The AI coach could not answer. Carries a message meant for the player."""


def _as_date(value) -> date:
    return value if isinstance(value, date) else datetime.strptime(value, "%Y-%m-%d").date()


def normalise_tickers(raw) -> list[str]:
    """Upper-case, de-duplicate and cap a caller-supplied watchlist."""
    if isinstance(raw, str):
        raw = [part for part in raw.replace(",", " ").split() if part]
    seen: dict[str, None] = {}
    for item in raw or []:
        symbol = str(item).strip().upper()
        if symbol:
            seen[symbol] = None
    tickers = list(seen)
    if len(tickers) > config.MAX_WATCHLIST:
        raise GameError(
            f"Pick at most {config.MAX_WATCHLIST} symbols "
            f"(you asked for {len(tickers)})."
        )
    return tickers


# --- state ----------------------------------------------------------------


def _portfolio(session: dict, holdings: list[dict], closes: dict[str, float]) -> dict:
    """Value every position at its own last close, not the focused chart's price."""
    positions = []
    market_value = 0.0
    for holding in holdings:
        ticker = holding["ticker"]
        shares = float(holding["shares"])
        close = closes.get(ticker)
        value = shares * close if close is not None else 0.0
        market_value += value
        positions.append(
            {
                "ticker": ticker,
                "shares": shares,
                "avg_cost": float(holding["avg_cost"]),
                "close": close,
                "market_value": value,
                "unrealized_pl": value - shares * float(holding["avg_cost"]),
            }
        )
    cash = float(session["cash_balance"])
    starting_cash = float(session["starting_cash"])
    net_worth = cash + market_value
    return {
        "positions": positions,
        "market_value": market_value,
        "cash_balance": cash,
        "net_worth": net_worth,
        "starting_cash": starting_cash,
        "total_return_pct": (net_worth - starting_cash) / starting_cash * 100,
    }


def _quotes(session_id: str, tickers: list[str], sim_date: date) -> dict[str, dict]:
    """Last close at or before sim_date for each symbol, real or simulated.

    Deliberately a single narrow query rather than one indicator frame per symbol: quotes
    are wanted for the whole watchlist on every tick, and building an 11k-row frame per
    symbol for two numbers each was the slowest thing in the request.
    """
    return database.latest_quotes(session_id, tickers, sim_date)


def _state_from_bundle(
    bundle: dict,
    focus: str | None = None,
    window_days: int = DEFAULT_CHART_WINDOW,
) -> dict:
    session = bundle["session"]
    if not session:
        raise GameError("Session not found")

    session_id = str(session["id"])
    watchlist = list(bundle.get("watchlist") or [])
    if not watchlist:
        raise GameError("This session has no symbols. Start a new one.")
    if focus and focus.upper() in watchlist:
        focus = focus.upper()
    else:
        focus = watchlist[0]

    sim_date = _as_date(session["sim_date"])
    quote_rows = _quotes(session_id, watchlist, sim_date)
    closes = {ticker: row["close"] for ticker, row in quote_rows.items()}

    focus_source = price_source.for_session(session, focus)
    history = focus_source.history_through(sim_date)

    return {
        "session_id": session_id,
        "tickers": watchlist,
        "focus": focus,
        "sim_date": sim_date.isoformat(),
        "start_date": _as_date(session["start_date"]).isoformat(),
        "end_date": _as_date(session["end_date"]).isoformat(),
        "status": session["status"],
        "simulation": focus_source.describe(),
        "today": indicators.latest_row_summary(history),
        "quotes": [
            {
                "ticker": ticker,
                "close": quote_rows.get(ticker, {}).get("close"),
                "previous_close": quote_rows.get(ticker, {}).get("previous_close"),
                "change_pct": quote_rows.get(ticker, {}).get("change_pct"),
                "simulated": quote_rows.get(ticker, {}).get("simulated", False),
            }
            for ticker in watchlist
        ],
        "portfolio": _portfolio(session, bundle["holdings"], closes),
        "chart": indicators.to_series(history.tail(window_days)),
        "trades": [
            {
                "date": _as_date(t["trade_date"]).isoformat(),
                "ticker": t["ticker"],
                "side": t["side"],
                "shares": float(t["shares"]),
                "price": float(t["price"]),
            }
            for t in bundle["trades"]
        ],
        "ai_feed": [
            {
                "id": event["id"],
                "type": event["event_type"],
                "ticker": event["ticker"],
                "sim_date": _as_date(event["sim_date"]).isoformat(),
                "payload": event["payload"],
            }
            for event in bundle["events"]
        ],
    }


def get_state(session_id: str, focus: str | None = None, window_days: int = DEFAULT_CHART_WINDOW) -> dict:
    return _state_from_bundle(database.load_session_bundle(session_id), focus, window_days)


# --- starting a run -------------------------------------------------------


def _window_for(watchlist: list[str], start_date: date, end_date: date) -> tuple[date, date]:
    """The span every watched symbol covers, so nothing chosen is untradeable."""
    first_days = []
    last_days = []
    for ticker in watchlist:
        bounds = price_cache.bounds(ticker)
        if not bounds:
            raise GameError(f"No price data stored for {ticker}. Ingest it first.")
        first = price_cache.first_trading_day(ticker, start_date)
        if first is None or first > min(end_date, bounds["last_day"]):
            raise GameError(f"No {ticker} trading days between {start_date} and {end_date}")
        first_days.append(first)
        last_days.append(bounds["last_day"])
    # Latest first-day and earliest last-day: the span where every chosen symbol trades.
    return max(first_days), min(end_date, min(last_days))


def start_session(
    tickers,
    start_date: date,
    end_date: date,
    nessie_customer_id: str | None = None,
    *,
    simulate_future: bool = False,
    horizon_days: int | None = None,
    drift: float | None = None,
    volatility: float | None = None,
    seed: int | None = None,
) -> dict:
    """Start a run over a watchlist.

    With simulate_future=True every watched symbol forks at the end of the real data, so
    the whole watchlist keeps trading into generated futures that belong to this session
    alone (see scripts/game/simulation.py).
    """
    watchlist = normalise_tickers(tickers)
    if not watchlist:
        raise GameError("Pick at least one symbol to trade.")

    # One round trip per symbol is the bulk of starting a run, so warm them together.
    price_cache.prefetch(watchlist)
    first_day, session_end = _window_for(watchlist, start_date, end_date)

    customer_id = nessie_customer_id or config.NESSIE_DEFAULT_CUSTOMER_ID
    starting_cash, account = nessie.get_starting_funds(customer_id)

    session = database.create_session(
        session_id=str(uuid.uuid4()),
        start_date=first_day,
        end_date=session_end,
        sim_date=first_day,
        starting_cash=starting_cash,
        nessie_customer_id=customer_id,
        nessie_account_id=account["_id"],
    )
    session_id = str(session["id"])
    database.set_session_tickers(session_id, watchlist)

    if simulate_future:
        # The run only ends when the generated futures do, not when real data ran out.
        last_simulated = session_end
        for ticker in watchlist:
            source = price_source.fork_session(
                session_id,
                ticker,
                session_end,
                horizon_days=horizon_days,
                drift=drift,
                volatility=volatility,
                seed=seed,
            )
            last_simulated = max(last_simulated, source.bounds()["last_day"])
        database.update_session(session_id, end_date=last_simulated)

    state = get_state(session_id)
    state["funding"] = {
        "source": nessie.source_label(),
        "account_nickname": account.get("nickname"),
        "account_type": account.get("type"),
        "balance": float(starting_cash),
    }
    return state


# --- background AI --------------------------------------------------------


def session_sectors(tickers) -> dict[str, dict]:
    """Sector lookup for a session's symbols, tolerating a catalog that is not migrated.

    Sector-scoped stories need to know which holdings share a sector; without the catalog
    the feature degrades to single-symbol news rather than failing the advance.
    """
    try:
        return database.ticker_sectors(list(tickers))
    except Exception:  # noqa: BLE001 - a missing catalog is not worth a failed tick
        log.warning("sector lookup unavailable; sector-scoped events disabled")
        return {}


def _last_event_date(event_log: list[dict], event_type: str, ticker: str) -> date | None:
    dates = [
        _as_date(entry["sim_date"])
        for entry in event_log
        if entry["event_type"] == event_type and entry["ticker"] == ticker
    ]
    return max(dates) if dates else None


def _least_covered(watchlist: list[str], event_log: list[dict], event_type: str) -> str:
    """The watched symbol with the fewest events of this kind.

    Stateless round-robin: automatic predictions and headlines spread across the whole
    watchlist instead of piling onto whichever symbol happens to be first.
    """
    counts = {ticker: 0 for ticker in watchlist}
    for entry in event_log:
        if entry["event_type"] == event_type and entry["ticker"] in counts:
            counts[entry["ticker"]] += 1
    return min(watchlist, key=lambda ticker: (counts[ticker], watchlist.index(ticker)))


def _run_ai(kind: str, session_id: str, ticker: str, sim_date: date) -> None:
    from scripts.api import gemini_mcp_client

    key = (session_id, kind, ticker)
    try:
        if kind == "PREDICTION":
            gemini_mcp_client.predict(session_id, ticker, sim_date)
        elif kind == "MARKET_SHOCK":
            # Rebuilt here rather than captured: this runs on a worker thread well after
            # the request that queued it, and each source holds a live DB-backed frame.
            # The whole watchlist is rebuilt, not just the reported symbol, because the
            # story may be a sector or market one that lands on several of them.
            session = database.get_session(session_id)
            if session:
                sources = price_source.for_watchlist(session)
                if any(source.kind == "simulated" for source in sources.values()):
                    events.run(session_id, sources, sim_date, ticker, session_sectors(sources))
        else:
            gemini_mcp_client.market_event(session_id, ticker, sim_date)
    except Exception as exc:
        log.warning("background %s for %s failed: %s", kind, ticker, exc)
    finally:
        with _ai_lock:
            _ai_inflight.discard(key)


def _schedule_ai(kind: str, session_id: str, ticker: str, sim_date: date) -> bool:
    """AI runs off the request path; results land in mcp_events and show up on a later tick."""
    key = (session_id, kind, ticker)
    with _ai_lock:
        if key in _ai_inflight:
            return False
        _ai_inflight.add(key)
    _ai_pool.submit(_run_ai, kind, session_id, ticker, sim_date)
    return True


def _maybe_schedule_ai(
    session_id: str,
    tickers: list[str],
    sim_date: date,
    event_log: list[dict],
    sources: dict,
) -> list[dict]:
    """Queue whatever the cadence says is due. Returns [{kind, ticker}] entries."""
    scheduled: list[dict] = []

    def days_since(kind: str, ticker: str) -> int | None:
        last = _last_event_date(event_log, kind, ticker)
        return None if last is None else sources[ticker].trading_days_between(last, sim_date)

    news_ticker = _least_covered(tickers, event_log, "NEWS_EVENT")
    gap = days_since("NEWS_EVENT", news_ticker)
    if (gap is None or gap >= config.MIN_DAYS_BETWEEN_EVENTS) and (
        random.random() < config.RANDOM_EVENT_PROBABILITY
    ):
        if _schedule_ai("NEWS_EVENT", session_id, news_ticker, sim_date):
            scheduled.append({"kind": "NEWS_EVENT", "ticker": news_ticker})

    prediction_ticker = _least_covered(tickers, event_log, "PREDICTION")
    prediction_gap = days_since("PREDICTION", prediction_ticker)
    if prediction_gap is None or prediction_gap >= config.PREDICTION_INTERVAL_DAYS:
        if _schedule_ai("PREDICTION", session_id, prediction_ticker, sim_date):
            scheduled.append({"kind": "PREDICTION", "ticker": prediction_ticker})

    # Shocks only exist where there is a generated future to bend.
    simulated = [ticker for ticker in tickers if sources[ticker].kind == "simulated"]
    if simulated:
        shock_ticker = _least_covered(simulated, event_log, "MARKET_SHOCK")
        if events.should_fire(event_log, shock_ticker, sources[shock_ticker], sim_date):
            if _schedule_ai("MARKET_SHOCK", session_id, shock_ticker, sim_date):
                scheduled.append({"kind": "MARKET_SHOCK", "ticker": shock_ticker})

    return scheduled


# --- the clock ------------------------------------------------------------


def advance_day(session_id: str, days: int = 1, with_ai: bool = True, focus: str | None = None) -> dict:
    bundle = database.load_session_bundle(session_id)
    session = bundle["session"]
    if not session:
        raise GameError("Session not found")

    watchlist = list(bundle.get("watchlist") or [])
    if not watchlist:
        raise GameError("This session has no symbols. Start a new one.")

    end_date = _as_date(session["end_date"])
    sim_date = _as_date(session["sim_date"])
    # A cold cache would otherwise fetch each symbol's history one at a time on the first
    # tick, which is where a fast-forward used to stall before it started moving.
    price_cache.prefetch(watchlist)
    sources = price_source.for_watchlist(session)

    if session["status"] != "active":
        state = _state_from_bundle(bundle, focus)
        return {**state, "signals": [], "pending_ai": []}

    # Walk the days in memory, then write the result once.
    signals: list[dict] = []
    finished = False
    for _ in range(max(1, days)):
        upcoming = [sources[t].next_trading_day(sim_date) for t in watchlist]
        upcoming = [day for day in upcoming if day is not None and day <= end_date]
        if not upcoming:
            finished = True
            break
        sim_date = min(upcoming)
        for ticker in watchlist:
            history = sources[ticker].history_through(sim_date)
            if history.empty:
                continue
            # Every rule only ever looks at the last two bars, and the indicator columns
            # are already computed on the whole frame, so the tail is enough. Passing the
            # full history here made a fast-forward re-scan decades of rows per symbol per
            # day to read two numbers.
            for signal in indicators.detect_signals(history.tail(SIGNAL_TAIL_ROWS)):
                signals.append({**signal, "ticker": ticker, "date": sim_date.isoformat()})

    if len(signals) > MAX_SIGNALS_PER_TICK:
        signals = signals[-MAX_SIGNALS_PER_TICK:]

    database.update_session(
        session_id,
        sim_date=sim_date,
        **({"status": "finished"} if finished else {}),
    )

    pending = (
        _maybe_schedule_ai(session_id, watchlist, sim_date, bundle["events"], sources)
        if with_ai and not finished
        else []
    )

    session["sim_date"] = sim_date
    if finished:
        session["status"] = "finished"
    state = _state_from_bundle(bundle, focus)
    state["signals"] = signals
    state["pending_ai"] = pending
    return state


# --- trading --------------------------------------------------------------


def execute_trade(
    session_id: str, ticker: str, side: str, shares: float, focus: str | None = None
) -> dict:
    session = database.get_session(session_id)
    if not session:
        raise GameError("Session not found")

    watchlist = database.session_tickers(session_id)
    ticker = (ticker or "").strip().upper()
    if ticker not in watchlist:
        raise GameError(f"{ticker or 'That symbol'} is not in this session's watchlist.")

    side = side.upper()
    if side not in ("BUY", "SELL"):
        raise GameError("side must be BUY or SELL")

    try:
        quantity = Decimal(str(shares))
    except Exception:
        raise GameError("shares must be a number")
    if quantity <= 0:
        raise GameError("shares must be greater than zero")

    sim_date = _as_date(session["sim_date"])
    quote = database.latest_quotes(session_id, [ticker], sim_date).get(ticker)
    if not quote or quote["close"] is None:
        raise GameError(f"No price for {ticker} on or before {sim_date}")
    price = Decimal(str(quote["close"]))

    holding = database.get_holding(session_id, ticker)
    held = holding["shares"] if holding else Decimal("0")
    avg_cost = holding["avg_cost"] if holding else Decimal("0")
    cash = session["cash_balance"]

    if side == "BUY":
        cost = (quantity * price).quantize(Decimal("0.01"))
        if cost > cash:
            raise GameError(f"Not enough cash: that costs ${cost:,.2f} and you have ${cash:,.2f}")
        new_shares = held + quantity
        new_avg_cost = ((held * avg_cost) + (quantity * price)) / new_shares
        new_cash = cash - cost
    else:
        if quantity > held:
            raise GameError(f"You only hold {held.normalize()} shares of {ticker}")
        new_shares = held - quantity
        new_avg_cost = avg_cost if new_shares > 0 else Decimal("0")
        new_cash = cash + (quantity * price).quantize(Decimal("0.01"))

    database.record_trade(
        session_id=session_id,
        ticker=ticker,
        trade_date=sim_date,
        side=side,
        shares=quantity,
        price=price,
        new_cash=new_cash,
        new_shares=new_shares,
        new_avg_cost=new_avg_cost.quantize(Decimal("0.0001")),
    )
    return get_state(session_id, focus)


# --- simulated future -----------------------------------------------------


def fork_simulation(session_id: str, focus: str | None = None, **overrides) -> dict:
    """Fork every symbol in this session's watchlist onto its own generated future.

    The fork lands on the last real trading day the session can see, so the player gets
    the history they have been trading plus a future they have not seen yet.
    """
    session = database.get_session(session_id)
    if not session:
        raise GameError("Session not found")

    watchlist = database.session_tickers(session_id)
    if not watchlist:
        raise GameError("This session has no symbols. Start a new one.")

    price_cache.prefetch(watchlist)

    # The future starts the day after the last real bar the run can see, so the player
    # keeps every real day they have not replayed yet. A finished run sits exactly on its
    # end date, so max() covers that case without a second branch.
    fork_date = max(_as_date(session["end_date"]), _as_date(session["sim_date"]))

    horizon = overrides.get("horizon_days")
    last_simulated = fork_date
    for ticker in watchlist:
        try:
            source = price_source.fork_session(
                session_id,
                ticker,
                fork_date,
                horizon_days=horizon,
                drift=overrides.get("drift"),
                volatility=overrides.get("volatility"),
                seed=overrides.get("seed"),
            )
        except ValueError as exc:
            raise GameError(str(exc))
        last_simulated = max(last_simulated, source.bounds()["last_day"])

    # A session that already ran out of real data is 'finished'; forking gives it more
    # days to play, so it has to become active again or advancing would stay a no-op.
    updates: dict = {"end_date": last_simulated}
    if _as_date(session["sim_date"]) < last_simulated:
        updates["status"] = "active"
    database.update_session(session_id, **updates)
    return get_state(session_id, focus)


def request_prediction(session_id: str, ticker: str | None = None) -> dict:
    """Ask the coach about one symbol, right now, with the reason on failure.

    Raises AIUnavailable with a message the UI can show, rather than returning None and
    leaving the player looking at a button that appears to do nothing.
    """
    from scripts.api import gemini_mcp_client

    session = database.get_session(session_id)
    if not session:
        raise GameError("Session not found")

    watchlist = database.session_tickers(session_id)
    if not watchlist:
        raise GameError("This session has no symbols. Start a new one.")
    target = (ticker or watchlist[0]).upper()
    if target not in watchlist:
        raise GameError(f"{target} is not in this session's watchlist.")

    try:
        return gemini_mcp_client.predict_or_raise(
            session_id, target, _as_date(session["sim_date"])
        )
    except GameError:
        raise
    except Exception as exc:
        raise AIUnavailable(gemini_mcp_client.friendly_error(exc)) from exc
