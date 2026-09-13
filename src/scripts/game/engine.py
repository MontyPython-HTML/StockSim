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
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from decimal import Decimal

import config
from scripts.api import nessie
from scripts.database import database
from scripts.game import events, expenses, indicators, news, patterns, price_cache, price_source

DEFAULT_CHART_WINDOW = 180

# A guard for fast-forwarding: asking for thousands of days would otherwise return a
# signal list long enough to stall the browser drawing it. The UI batches five days at a
# time, so this never bites in normal play.
MAX_SIGNALS_PER_TICK = 40

# How many trailing bars signal detection is handed. The rules compare the last two
# bars of already-computed columns, so a handful is plenty and keeps an advance cheap.
SIGNAL_TAIL_ROWS = 5

# A share count is a sanity check, not an economic one: no player trades a billion shares,
# and anything past this either overflows NUMERIC on the way in or blows up the cost
# arithmetic. Also catches NaN and infinity, which slip through every comparison.
MAX_TRADE_SHARES = Decimal("1000000000")

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
        # Rejected rather than coerced: str(None) used to become the symbol "NONE", which
        # then failed as "no price data for NONE" - a confusing way to say "bad payload".
        if not isinstance(item, str):
            raise GameError(f"symbols must be strings like \"AAPL\" (got {item!r})")
        symbol = item.strip().upper()
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


def _portfolio(
    session: dict,
    holdings: list[dict],
    closes: dict[str, float],
    bills_paid: float = 0.0,
    salary_earned: float = 0.0,
) -> dict:
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
        # This headline is deliberately stock performance only: salaries and bills are
        # excluded, while dividends remain investment income from the holdings.
        "total_return_pct": (net_worth + bills_paid - salary_earned - starting_cash) / starting_cash * 100 if starting_cash else 0.0,
        # Rent is not a trading loss and a paycheck is not a trading gain. The account number
        # decides whether the player went broke; this one is the only fair read on their
        # decisions, and showing both is what makes the difference teachable.
        "bills_paid": bills_paid,
        "salary_earned": salary_earned,
        "trading_return_pct": (net_worth + bills_paid - salary_earned - starting_cash) / starting_cash * 100 if starting_cash else 0.0,
        "overdrawn": cash < 0,
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

    sources = price_source.for_watchlist(session, watchlist)
    focus_source = sources[focus]
    history = focus_source.history_through(sim_date)
    ledger = bundle.get("expenses") or []
    bills_paid = sum(float(row["amount"]) for row in ledger if (row.get("kind") or "bill") == "bill")
    salary_earned = sum(float(row["amount"]) for row in ledger if row.get("kind") == "salary")

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
        "portfolio": _portfolio(session, bundle["holdings"], closes, bills_paid, salary_earned),
        "dividends": [
            {**row, "ex_date": _as_date(row["ex_date"]).isoformat(), "amount": float(row["amount"])}
            for row in bundle.get("dividends") or []
        ],
        "dividends_paid": sum(float(row["amount"]) for row in bundle.get("dividends") or []),
        "dividend_summary": {
            "total": sum(float(row["amount"]) for row in bundle.get("dividends") or []),
            "payments": len(bundle.get("dividends") or []),
        },
        "chart": indicators.to_series(history.tail(window_days)),
        "trades": [
            {
                "date": _as_date(t["trade_date"]).isoformat(),
                "ticker": t["ticker"],
                "side": t["side"],
                "shares": float(t["shares"]),
                "price": float(t["price"]),
                "forced": bool(t.get("forced")),
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
        # The syllabus with what this session has covered, so the screen can show which
        # patterns the player has actually been taught and which are still ahead of them.
        "patterns": patterns.progress(bundle["events"]),
        # Real-life money: what the bank has taken and paid in, and what is coming next.
        "expenses": {
            **expenses.summary(session, sim_date, ledger),
            "charged": [expenses.ledger_row(row) for row in ledger],
        },
        "bank": {
            "source": nessie.source_label() if session.get("finances_enabled", True) else "Pure stock simulation",
            **(expenses.bank_for(session) if session.get("finances_enabled", True) else {"customer": None, "account": None, "employer": None}),
        },
        "news": news.headlines(session, sim_date, bundle["events"], sources),
    }


def get_state(session_id: str, focus: str | None = None, window_days: int = DEFAULT_CHART_WINDOW) -> dict:
    return _state_from_bundle(database.load_session_bundle(session_id), focus, window_days)


# --- starting a run -------------------------------------------------------


def _seed_for(base_seed: int | None, ticker: str) -> int | None:
    """A per-symbol seed derived from the session's.

    Handing the whole watchlist one seed made every symbol draw the *same* random shocks,
    so unrelated names turned on exactly the same days - manufacturing the correlation the
    sector-event feature exists to demonstrate. crc32 rather than hash(), because str
    hashing is salted per process and a future would not survive a restart.
    """
    if base_seed is None:
        return None
    return zlib.crc32(f"{base_seed}:{ticker.upper()}".encode())


def _window_for(watchlist: list[str], start_date: date, end_date: date) -> tuple[date, date]:
    """The span every watched symbol covers, so nothing chosen is untradeable."""
    catalog = database.ticker_sectors(watchlist)
    first_days = []
    last_days = []
    for ticker in watchlist:
        bounds = price_cache.bounds(ticker)
        if not bounds:
            # "Ingest it first" is only advice for a symbol we know about. Telling someone
            # to ingest an unknown ticker sends them to a command that cannot help.
            if ticker not in catalog:
                raise GameError(
                    f"Unknown symbol {ticker}. Pick one from the catalog on the start page."
                )
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
    salary_amount: Decimal | None = None,
    use_finances: bool = True,
    initial_cash: Decimal | None = None,
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
    account = {"_id": None, "nickname": "Pure simulation", "type": "Simulation", "balance": 0}
    if use_finances:
        try:
            starting_cash, account = nessie.get_starting_funds(customer_id)
        except ValueError as exc:
            raise GameError(str(exc))
        salary = expenses.default_paycheck(account["_id"]) if salary_amount is None else salary_amount
        account_id = account["_id"]
        session_customer_id = customer_id
    else:
        starting_cash = initial_cash if initial_cash is not None else Decimal("10000")
        if starting_cash < 0:
            raise GameError("Starting cash cannot be negative.")
        salary = Decimal("0")
        account_id = None
        session_customer_id = None

    session = database.create_session(
        session_id=str(uuid.uuid4()),
        start_date=first_day,
        end_date=session_end,
        sim_date=first_day,
        starting_cash=starting_cash,
        nessie_customer_id=session_customer_id,
        nessie_account_id=account_id,
        salary_amount=salary,
        finances_enabled=use_finances,
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
                seed=_seed_for(seed, ticker),
            )
            last_simulated = max(last_simulated, source.bounds()["last_day"])
        database.update_session(session_id, end_date=last_simulated)

    state = get_state(session_id)
    state["funding"] = {
        "source": nessie.source_label() if use_finances else "Pure stock simulation",
        "account_nickname": account.get("nickname"),
        "account_type": account.get("type"),
        "balance": float(starting_cash),
        "paycheck": float(salary),
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


def _run_ai(kind: str, session_id: str, ticker: str, sim_date: date, detail: str = "") -> None:
    key = (session_id, kind, ticker, detail) if detail else (session_id, kind, ticker)
    try:
        # Imported inside the try on purpose: _schedule_ai has already marked this key
        # in-flight, and an import that raised outside would leave it there forever,
        # silently disabling every future event of this kind for the whole process.
        from scripts.api import gemini_mcp_client

        if kind == "PREDICTION":
            gemini_mcp_client.predict(session_id, ticker, sim_date)
        elif kind == patterns.LESSON_EVENT_TYPE:
            # `detail` is the signal name the detector produced when this was queued. The
            # lesson is taught against the chart as it stands, so the numbers quoted in it
            # are the ones the player can see, not the ones from the request thread.
            session = database.get_session(session_id)
            if session:
                history = price_source.for_session(session, ticker).history_through(sim_date)
                if not history.empty:
                    fired = next(
                        (
                            signal
                            for signal in indicators.detect_signals(history.tail(SIGNAL_TAIL_ROWS))
                            if signal["name"] == detail
                        ),
                        None,
                    )
                    if fired is None:
                        # Should not happen now that the lesson is dated to the signal's own
                        # bar; if it does, the pattern is still worth teaching, just without
                        # the detector's read of this particular bar.
                        log.warning(
                            "%s is not on %s's chart at %s; teaching the pattern without a "
                            "chart-specific read",
                            detail,
                            ticker,
                            sim_date,
                        )
                        fired = {"name": detail, "message": "", "direction": "neutral"}
                    patterns.teach(session_id, ticker, sim_date, fired, history)
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


def _schedule_ai(
    kind: str, session_id: str, ticker: str, sim_date: date, detail: str = ""
) -> bool:
    """AI runs off the request path; results land in mcp_events and show up on a later tick.

    `detail` distinguishes two jobs that share a symbol and a kind - a lesson on a golden
    cross and one on a volume spike are separate work, and keying only on the ticker would
    drop the second while the first was still running.
    """
    key = (session_id, kind, ticker, detail) if detail else (session_id, kind, ticker)
    with _ai_lock:
        if key in _ai_inflight:
            return False
        _ai_inflight.add(key)
    _ai_pool.submit(_run_ai, kind, session_id, ticker, sim_date, detail)
    return True


def _schedule_lesson(
    session_id: str,
    sim_date: date,
    event_log: list[dict],
    signals: list[dict],
    days_since,
    scheduled: list[dict],
) -> None:
    """Teach the pattern that just appeared on the player's own chart.

    Two rules, and the split between them is the whole curriculum:

    * A pattern this session has never taught is taught on sight. New material is the
      expensive thing to get in front of someone, and a signal that just fired is the one
      moment they are looking at it.
    * A pattern they have already met is only revisited once `PATTERN_REPEAT_GAP_DAYS`
      sessions have passed for that symbol, and only sometimes. Without this the feed
      becomes the same card over and over, because MACD crosses far more often than the
      other patterns.

    The lesson is dated to the bar the signal actually printed on, not to the end of the
    tick: a fast-forward hands us a whole walk of days at once, so the cross may be fifteen
    bars behind the clock. Dating it `sim_date` made the coach describe a pattern on a bar
    that no longer showed one - the card quoted the wrong session's prices and lost the
    detector's own explanation, because re-reading the chart at the end of the walk finds
    no such signal at all.
    """
    chosen = patterns.choose_new(signals, event_log)
    if chosen is None:
        candidate = patterns.choose_repeat(signals, event_log)
        if candidate is None or random.random() >= config.PATTERN_REPEAT_PROBABILITY:
            return
        gap = days_since(patterns.LESSON_EVENT_TYPE, candidate["ticker"])
        if gap is not None and gap < patterns.repeat_gap_days():
            return
        chosen = candidate

    try:
        lesson_date = _as_date(chosen["date"]) if chosen.get("date") else sim_date
    except (TypeError, ValueError):
        # A signal we produced ourselves always carries a date; a bad one must not turn a
        # clock tick into a 500, so the walk's last day is the fallback.
        log.warning("lesson signal %r has an unusable date %r", chosen["name"], chosen.get("date"))
        lesson_date = sim_date
    if _schedule_ai(
        patterns.LESSON_EVENT_TYPE,
        session_id,
        chosen["ticker"],
        lesson_date,
        detail=chosen["name"],
    ):
        scheduled.append(
            {
                "kind": patterns.LESSON_EVENT_TYPE,
                "ticker": chosen["ticker"],
                "pattern": chosen["name"],
                "date": lesson_date.isoformat(),
            }
        )


def _maybe_schedule_ai(
    session_id: str,
    tickers: list[str],
    sim_date: date,
    event_log: list[dict],
    sources: dict,
    signals: list[dict],
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

    # Note this is not behind a dice roll like the others: if the player's chart produced
    # a pattern they have never been taught, teaching it is the point of the feature.
    if config.PATTERN_LESSONS and signals:
        _schedule_lesson(session_id, sim_date, event_log, signals, days_since, scheduled)

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
    sources = price_source.for_watchlist(session, watchlist)

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

    # Bills and paychecks for the days just crossed, settled before the clock is saved: if
    # this fails the date stays put, and the retry re-walks days the ledger already holds.
    previous_sim_date = _as_date(session["sim_date"])
    charged = (
        expenses.apply_due(session, previous_sim_date, sim_date, watchlist)
        if session.get("finances_enabled", True)
        else []
    )
    dividend_rows = database.settle_dividends(session_id, watchlist, previous_sim_date, sim_date)

    database.update_session(
        session_id,
        sim_date=sim_date,
        **({"status": "finished"} if finished else {}),
    )

    pending = (
        _maybe_schedule_ai(
            session_id, watchlist, sim_date, bundle["events"], sources, signals
        )
        if with_ai and not finished
        else []
    )

    session["sim_date"] = sim_date
    if finished:
        session["status"] = "finished"
    # The bundle is one tick stale: the ledger rows just written are the ones the player has
    # not seen, and a forced sale also moved holdings and the trade log.
    if any(row["sold"] for row in charged):
        fresh = database.load_session_bundle(session_id)
        bundle["holdings"], bundle["trades"] = fresh["holdings"], fresh["trades"]
    bundle["expenses"] = list(bundle.get("expenses") or []) + charged
    bundle["dividends"] = list(bundle.get("dividends") or []) + dividend_rows
    state = _state_from_bundle(bundle, focus)
    state["signals"] = signals
    state["pending_ai"] = pending
    state["charged"] = charged
    return state


# --- trading --------------------------------------------------------------


def execute_trade(
    session_id: str, ticker: str, side: str, shares: float, focus: str | None = None
) -> dict:
    session = database.get_session(session_id)
    if not session:
        raise GameError("Session not found")

    if session["status"] != "active":
        # The clock is the risk. Trading on after it stops would let a finished run be
        # improved at frozen prices, which is exactly what the exercise is measuring.
        raise GameError("This session is over. Start a new one to keep trading.")

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
    # Not just tidiness: an absurd or non-finite size survives the multiplication but blows
    # up in quantize() as a decimal.InvalidOperation, which reached the player as a 500.
    if not quantity.is_finite() or quantity > MAX_TRADE_SHARES:
        raise GameError(f"shares must be between 0 and {MAX_TRADE_SHARES:,}")
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
                seed=_seed_for(overrides.get("seed"), ticker),
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
