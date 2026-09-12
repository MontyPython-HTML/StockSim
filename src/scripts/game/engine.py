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
from scripts.game import indicators, price_cache

DEFAULT_CHART_WINDOW = 180

log = logging.getLogger(__name__)

_ai_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ai")
_ai_inflight: set[tuple[str, str]] = set()
_ai_lock = threading.Lock()


class GameError(Exception):
    """bad ticker, insufficient funds, session over"""


def _as_date(value) -> date:
    return value if isinstance(value, date) else datetime.strptime(value, "%Y-%m-%d").date()


def _portfolio(session: dict, holdings: list[dict], close: float | None) -> dict:
    positions = []
    market_value = 0.0
    for holding in holdings:
        shares = float(holding["shares"])
        value = shares * (close or 0.0)
        market_value += value
        positions.append(
            {
                "ticker": holding["ticker"],
                "shares": shares,
                "avg_cost": float(holding["avg_cost"]),
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


def _state_from_bundle(bundle: dict, window_days: int = DEFAULT_CHART_WINDOW) -> dict:
    session = bundle["session"]
    if not session:
        raise GameError("Session not found")

    ticker = session["ticker"]
    sim_date = _as_date(session["sim_date"])
    history = price_cache.history_through(ticker, sim_date)

    return {
        "session_id": str(session["id"]),
        "ticker": ticker,
        "sim_date": sim_date.isoformat(),
        "start_date": _as_date(session["start_date"]).isoformat(),
        "end_date": _as_date(session["end_date"]).isoformat(),
        "status": session["status"],
        "today": indicators.latest_row_summary(history),
        "portfolio": _portfolio(session, bundle["holdings"], price_cache.close_on(ticker, sim_date)),
        "chart": indicators.to_series(history.tail(window_days)),
        "trades": [
            {
                "date": _as_date(t["trade_date"]).isoformat(),
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
                "sim_date": _as_date(event["sim_date"]).isoformat(),
                "payload": event["payload"],
            }
            for event in bundle["events"]
        ],
    }


def get_state(session_id: str, window_days: int = DEFAULT_CHART_WINDOW) -> dict:
    return _state_from_bundle(database.load_session_bundle(session_id), window_days)


def start_session(
    ticker: str,
    start_date: date,
    end_date: date,
    nessie_customer_id: str | None = None,
) -> dict:
    ticker = ticker.upper()
    bounds = price_cache.bounds(ticker)
    if not bounds:
        raise GameError(f"No price data stored for {ticker}. Ingest it first.")

    first_day = price_cache.first_trading_day(ticker, start_date)
    if first_day is None or first_day > end_date:
        raise GameError(f"No {ticker} trading days between {start_date} and {end_date}")

    customer_id = nessie_customer_id or config.NESSIE_DEFAULT_CUSTOMER_ID
    starting_cash, account = nessie.get_starting_funds(customer_id)

    session = database.create_session(
        session_id=str(uuid.uuid4()),
        ticker=ticker,
        start_date=first_day,
        end_date=min(end_date, bounds["last_day"]),
        sim_date=first_day,
        starting_cash=starting_cash,
        nessie_customer_id=customer_id,
        nessie_account_id=account["_id"],
    )
    state = get_state(str(session["id"]))
    state["funding"] = {
        "source": nessie.source_label(),
        "account_nickname": account.get("nickname"),
        "account_type": account.get("type"),
        "balance": float(starting_cash),
    }
    return state


def _last_event_date(events: list[dict], event_type: str) -> date | None:
    dates = [_as_date(e["sim_date"]) for e in events if e["event_type"] == event_type]
    return max(dates) if dates else None


def _run_ai(kind: str, session_id: str, ticker: str, sim_date: date) -> None:
    from scripts.api import gemini_mcp_client

    key = (session_id, kind)
    try:
        if kind == "PREDICTION":
            gemini_mcp_client.predict(session_id, ticker, sim_date)
        else:
            gemini_mcp_client.market_event(session_id, ticker, sim_date)
    except Exception as exc:
        log.warning("background %s failed: %s", kind, exc)
    finally:
        with _ai_lock:
            _ai_inflight.discard(key)


def _schedule_ai(kind: str, session_id: str, ticker: str, sim_date: date) -> bool:
    """AI runs off the request path; results land in mcp_events and show up on a later tick."""
    key = (session_id, kind)
    with _ai_lock:
        if key in _ai_inflight:
            return False
        _ai_inflight.add(key)
    _ai_pool.submit(_run_ai, kind, session_id, ticker, sim_date)
    return True


def _maybe_schedule_ai(session_id: str, ticker: str, sim_date: date, events: list[dict]) -> list[str]:
    scheduled = []

    last_event = _last_event_date(events, "NEWS_EVENT")
    gap = price_cache.trading_days_between(ticker, last_event, sim_date) if last_event else None
    if (gap is None or gap >= config.MIN_DAYS_BETWEEN_EVENTS) and (
        random.random() < config.RANDOM_EVENT_PROBABILITY
    ):
        if _schedule_ai("NEWS_EVENT", session_id, ticker, sim_date):
            scheduled.append("NEWS_EVENT")

    last_prediction = _last_event_date(events, "PREDICTION")
    prediction_gap = (
        price_cache.trading_days_between(ticker, last_prediction, sim_date)
        if last_prediction
        else None
    )
    if prediction_gap is None or prediction_gap >= config.PREDICTION_INTERVAL_DAYS:
        if _schedule_ai("PREDICTION", session_id, ticker, sim_date):
            scheduled.append("PREDICTION")

    return scheduled


def advance_day(session_id: str, days: int = 1, with_ai: bool = True) -> dict:
    bundle = database.load_session_bundle(session_id)
    session = bundle["session"]
    if not session:
        raise GameError("Session not found")

    ticker = session["ticker"]
    end_date = _as_date(session["end_date"])
    sim_date = _as_date(session["sim_date"])

    if session["status"] != "active":
        state = _state_from_bundle(bundle)
        return {**state, "signals": [], "pending_ai": []}

    # Walk the days in memory, then write the result once.
    signals: list[dict] = []
    finished = False
    for _ in range(max(1, days)):
        next_day = price_cache.next_trading_day(ticker, sim_date)
        if next_day is None or next_day > end_date:
            finished = True
            break
        sim_date = next_day
        history = price_cache.history_through(ticker, sim_date)
        for signal in indicators.detect_signals(history):
            signals.append({**signal, "date": sim_date.isoformat()})

    database.update_session(
        session_id,
        sim_date=sim_date,
        **({"status": "finished"} if finished else {}),
    )

    pending = (
        _maybe_schedule_ai(session_id, ticker, sim_date, bundle["events"])
        if with_ai and not finished
        else []
    )

    session["sim_date"] = sim_date
    if finished:
        session["status"] = "finished"
    state = _state_from_bundle(bundle)
    state["signals"] = signals
    state["pending_ai"] = pending
    return state


def execute_trade(session_id: str, side: str, shares: float) -> dict:
    session = database.get_session(session_id)
    if not session:
        raise GameError("Session not found")

    side = side.upper()
    if side not in ("BUY", "SELL"):
        raise GameError("side must be BUY or SELL")

    try:
        quantity = Decimal(str(shares))
    except Exception:
        raise GameError("shares must be a number")
    if quantity <= 0:
        raise GameError("shares must be greater than zero")

    ticker = session["ticker"]
    close = price_cache.close_on(ticker, session["sim_date"])
    if close is None:
        raise GameError(f"No price for {ticker} on {session['sim_date']}")
    price = Decimal(str(close))

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
        trade_date=session["sim_date"],
        side=side,
        shares=quantity,
        price=price,
        new_cash=new_cash,
        new_shares=new_shares,
        new_avg_cost=new_avg_cost.quantize(Decimal("0.0001")),
    )
    return get_state(session_id)


def request_prediction(session_id: str) -> dict | None:
    from scripts.api import gemini_mcp_client

    session = database.get_session(session_id)
    if not session:
        raise GameError("Session not found")
    return gemini_mcp_client.predict(session_id, session["ticker"], session["sim_date"])
