"""Shared TigerData (TimescaleDB) access layer used by both Flask and the MCP server."""

import json
import threading
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
from psycopg2.extras import RealDictCursor, execute_values
from psycopg2.pool import ThreadedConnectionPool

import config

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

# Rows per INSERT statement. A full history is over eleven thousand rows, and sending
# that as one statement blows the server's per-message memory budget on a small instance.
INSERT_PAGE_SIZE = 1000

_pool: ThreadedConnectionPool | None = None
_pool_lock = threading.Lock()


def _get_pool() -> ThreadedConnectionPool:
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = ThreadedConnectionPool(1, 8, config.DATABASE_DSN)
    return _pool


@contextmanager
def get_conn():
    """An autocommit connection, i.e. one round trip per statement instead of two.

    psycopg2 opens a transaction for every statement otherwise, and the commit that closes
    it is a network round trip in its own right - against a remote database that was most
    of the time a session took to start. Anything that must land as one unit takes
    get_transaction() below instead.
    """
    pool = _get_pool()
    conn = pool.getconn()
    if not conn.autocommit:
        conn.autocommit = True
    try:
        yield conn
    finally:
        pool.putconn(conn)


@contextmanager
def get_cursor(dict_rows: bool = True):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor if dict_rows else None) as cur:
            yield cur


@contextmanager
def get_transaction(dict_rows: bool = True):
    """A cursor inside one real transaction, for writes that must be all-or-nothing.

    Batching several statements is exactly what autocommit connections cannot promise, so
    the writers that touch more than one row set - a trade and the balance it moves, a
    shock and the bars it reprices - come through here.
    """
    pool = _get_pool()
    conn = pool.getconn()
    previous = conn.autocommit
    conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=RealDictCursor if dict_rows else None) as cur:
            yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.autocommit = previous
        pool.putconn(conn)


def apply_schema() -> None:
    # One multi-statement string: PostgreSQL runs it as a single implicit transaction.
    with get_cursor(dict_rows=False) as cur:
        cur.execute(SCHEMA_PATH.read_text())


# --- prices ---------------------------------------------------------------


def upsert_prices(ticker: str, rows: Iterable[tuple]) -> int:
    sql = """
        INSERT INTO stock_prices (ticker, ts, open, high, low, close, adj_close, volume)
        VALUES %s
        ON CONFLICT (ticker, ts) DO UPDATE SET
            open = EXCLUDED.open,
            high = EXCLUDED.high,
            low = EXCLUDED.low,
            close = EXCLUDED.close,
            adj_close = EXCLUDED.adj_close,
            volume = EXCLUDED.volume
    """
    rows = list(rows)
    if not rows:
        return 0
    with get_transaction(dict_rows=False) as cur:
        execute_values(cur, sql, rows, page_size=INSERT_PAGE_SIZE)
    return len(rows)


def fetch_price_history(ticker: str, start_date: date, end_date: date) -> pd.DataFrame:
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT ts, open, high, low, close, adj_close, volume
            FROM stock_prices
            WHERE ticker = %s AND ts BETWEEN %s AND %s
            ORDER BY ts
            """,
            (ticker.upper(), start_date, end_date),
        )
        rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "adj_close", "volume"])
    if df.empty:
        return df
    for col in ("open", "high", "low", "close", "adj_close"):
        df[col] = df[col].astype(float)
    df["volume"] = df["volume"].astype("int64")
    return df


def fetch_date_bounds(ticker: str) -> dict | None:
    with get_cursor() as cur:
        cur.execute(
            "SELECT min(ts) AS first_day, max(ts) AS last_day, count(*) AS row_count "
            "FROM stock_prices WHERE ticker = %s",
            (ticker.upper(),),
        )
        row = cur.fetchone()
    return row if row and row["row_count"] else None


# --- sessions -------------------------------------------------------------


def create_session(
    session_id: str,
    start_date: date,
    end_date: date,
    sim_date: date,
    starting_cash: Decimal,
    nessie_customer_id: str | None,
    nessie_account_id: str | None,
    user_label: str = "anonymous",
    salary_amount: Decimal = Decimal("0"),
) -> dict:
    """Create the session row. The watchlist is written separately, by set_session_tickers."""
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO game_sessions (
                id, user_label, start_date, end_date, sim_date,
                starting_cash, cash_balance, nessie_customer_id, nessie_account_id, salary_amount
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                session_id,
                user_label,
                start_date,
                end_date,
                sim_date,
                starting_cash,
                starting_cash,
                nessie_customer_id,
                nessie_account_id,
                salary_amount,
            ),
        )
        return cur.fetchone()


def set_session_tickers(session_id: str, tickers: Iterable[str]) -> int:
    """Replace a session's watchlist, preserving the given order as the display order."""
    rows = [(session_id, ticker.upper(), index) for index, ticker in enumerate(tickers)]
    with get_transaction(dict_rows=False) as cur:
        cur.execute("DELETE FROM session_tickers WHERE session_id = %s", (session_id,))
        if rows:
            execute_values(
                cur,
                "INSERT INTO session_tickers (session_id, ticker, sort_order) VALUES %s",
                rows,
            )
    return len(rows)


def session_tickers(session_id: str) -> list[str]:
    with get_cursor() as cur:
        cur.execute(
            "SELECT ticker FROM session_tickers WHERE session_id = %s ORDER BY sort_order, ticker",
            (session_id,),
        )
        return [row["ticker"] for row in cur.fetchall()]


def get_session(session_id: str) -> dict | None:
    with get_cursor() as cur:
        cur.execute("SELECT * FROM game_sessions WHERE id = %s", (session_id,))
        return cur.fetchone()


def update_session(session_id: str, **fields: Any) -> dict | None:
    if not fields:
        return get_session(session_id)
    assignments = ", ".join(f"{name} = %s" for name in fields)
    with get_cursor() as cur:
        cur.execute(
            f"UPDATE game_sessions SET {assignments}, updated_at = now() WHERE id = %s RETURNING *",
            (*fields.values(), session_id),
        )
        return cur.fetchone()


# --- holdings and trades --------------------------------------------------


def get_holding(session_id: str, ticker: str) -> dict | None:
    with get_cursor() as cur:
        cur.execute(
            "SELECT * FROM portfolio_holdings WHERE session_id = %s AND ticker = %s",
            (session_id, ticker.upper()),
        )
        return cur.fetchone()


def record_trade(
    session_id: str,
    ticker: str,
    trade_date: date,
    side: str,
    shares: Decimal,
    price: Decimal,
    new_cash: Decimal,
    new_shares: Decimal,
    new_avg_cost: Decimal,
) -> dict:
    """Cash, holdings and the trade log all move together or not at all."""
    ticker = ticker.upper()
    with get_transaction() as cur:
        cur.execute(
            "UPDATE game_sessions SET cash_balance = %s, updated_at = now() WHERE id = %s",
            (new_cash, session_id),
        )
        if new_shares > 0:
            cur.execute(
                """
                INSERT INTO portfolio_holdings (session_id, ticker, shares, avg_cost)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (session_id, ticker) DO UPDATE SET
                    shares = EXCLUDED.shares,
                    avg_cost = EXCLUDED.avg_cost,
                    updated_at = now()
                """,
                (session_id, ticker, new_shares, new_avg_cost),
            )
        else:
            cur.execute(
                "DELETE FROM portfolio_holdings WHERE session_id = %s AND ticker = %s",
                (session_id, ticker),
            )
        cur.execute(
            """
            INSERT INTO transactions
                (session_id, ticker, trade_date, side, shares, price, cash_after)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (session_id, ticker, trade_date, side, shares, price, new_cash),
        )
        return cur.fetchone()


def _as_date(value) -> date:
    """ISO string (from JSON) or date/datetime (from the driver) -> date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value), "%Y-%m-%d").date()


def _revive_dates(bundle: dict) -> dict:
    """Put the DATE columns back to `date` after the JSON trip.

    The bundle's whole point is one query instead of five, and row_to_json/json_agg are
    what buy that - at the cost of column types: dates arrive as ISO strings. Restoring
    them here keeps "sim_date is a date" true for every reader, rather than requiring
    each one to remember to parse it (a date comparison against a string raises
    TypeError, so forgetting 500s the whole tick).
    """
    session = bundle.get("session") or {}
    for field in ("start_date", "end_date", "sim_date"):
        if session.get(field):
            session[field] = _as_date(session[field])
    for event in bundle.get("events") or []:
        event["sim_date"] = _as_date(event["sim_date"])
    for trade in bundle.get("trades") or []:
        trade["trade_date"] = _as_date(trade["trade_date"])
    return bundle


def load_session_bundle(session_id: str, event_limit: int = 30) -> dict:
    """Session, watchlist, holdings, trades and AI events in one round trip.

    The database is remote, so collapsing these five reads into a single query is worth
    the SQL - it is the difference between a ~600ms and a ~150ms game tick.
    """
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT
                (SELECT row_to_json(s) FROM game_sessions s WHERE s.id = %(sid)s) AS session,
                (SELECT coalesce(json_agg(st.ticker ORDER BY st.sort_order, st.ticker), '[]'::json)
                 FROM session_tickers st WHERE st.session_id = %(sid)s) AS watchlist,
                (SELECT coalesce(json_agg(h), '[]'::json) FROM (
                    SELECT ticker, shares, avg_cost FROM portfolio_holdings
                    WHERE session_id = %(sid)s AND shares > 0 ORDER BY ticker
                ) h) AS holdings,
                (SELECT coalesce(json_agg(t), '[]'::json) FROM (
                    SELECT id, ticker, trade_date, side, shares, price, cash_after, forced
                    FROM transactions WHERE session_id = %(sid)s ORDER BY trade_date, id
                ) t) AS trades,
                (SELECT coalesce(json_agg(e), '[]'::json) FROM (
                    SELECT id, event_type, ticker, sim_date, payload, created_at
                    FROM mcp_events WHERE session_id = %(sid)s
                    ORDER BY created_at DESC, id DESC LIMIT %(limit)s
                ) e) AS events,
                (SELECT coalesce(json_agg(x), '[]'::json) FROM (
                    SELECT id, bill_id, kind, label, payee, due_date, amount, shortfall, cash_after
                    FROM session_expenses WHERE session_id = %(sid)s ORDER BY due_date, id
                ) x) AS expenses
            """,
            {"sid": session_id, "limit": event_limit},
        )
        return _revive_dates(cur.fetchone())


# --- standing orders ------------------------------------------------------


def settle_cash_flows(session_id: str, flows: list[dict], prices_on, plan_sales) -> tuple[list[dict], Decimal]:
    """Pay bills and bank paychecks in date order, in one transaction, never below zero cash.

    The session and its holdings are row-locked, so a concurrent tick cannot settle the same
    day twice and (session_id, bill_id, due_date) rows already on the ledger are skipped. A
    bill the cash cannot cover sells shares first - `plan_sales(holdings, prices, needed)`
    picks them and `prices_on(day)` prices them - and whatever is still owed after that is
    stored as the bill's shortfall.
    """
    cent = Decimal("0.01")
    with get_transaction() as cur:
        cur.execute("SELECT cash_balance FROM game_sessions WHERE id = %s FOR UPDATE", (session_id,))
        row = cur.fetchone()
        if row is None:
            return [], Decimal("0")
        cash = row["cash_balance"]

        cur.execute(
            "SELECT ticker, shares FROM portfolio_holdings "
            "WHERE session_id = %s AND shares > 0 FOR UPDATE",
            (session_id,),
        )
        holdings = {held["ticker"]: held["shares"] for held in cur.fetchall()}

        cur.execute(
            "SELECT bill_id, due_date FROM session_expenses "
            "WHERE session_id = %s AND due_date = ANY(%s::date[])",
            (session_id, sorted({flow["due_date"] for flow in flows})),
        )
        settled = {(done["bill_id"], done["due_date"]) for done in cur.fetchall()}

        applied: list[dict] = []
        sold_tickers: set[str] = set()
        for flow in flows:
            if (flow["bill_id"], flow["due_date"]) in settled:
                continue
            amount = Decimal(str(flow["amount"])).quantize(cent)
            sold: list[dict] = []

            if flow["kind"] == "salary":
                cash += amount
                paid = amount
            else:
                if cash < amount and holdings:
                    quotes = prices_on(flow["due_date"])
                    prices = {ticker: quote["price"] for ticker, quote in quotes.items()}
                    for ticker, shares in plan_sales(holdings, prices, amount - cash):
                        price = prices[ticker]
                        cash += (shares * price).quantize(cent)
                        holdings[ticker] -= shares
                        if holdings[ticker] <= 0:
                            del holdings[ticker]
                        sold_tickers.add(ticker)
                        cur.execute(
                            """
                            INSERT INTO transactions
                                (session_id, ticker, trade_date, side, shares, price, cash_after, forced)
                            VALUES (%s, %s, %s, 'SELL', %s, %s, %s, TRUE)
                            """,
                            (session_id, ticker, quotes[ticker]["date"], shares, price, cash),
                        )
                        sold.append(
                            {
                                "ticker": ticker,
                                "shares": float(shares),
                                "price": float(price),
                                "date": quotes[ticker]["date"].isoformat(),
                            }
                        )
                paid = max(Decimal("0"), min(amount, cash))
                cash -= paid

            cur.execute(
                """
                INSERT INTO session_expenses
                    (session_id, bill_id, kind, label, payee, due_date, amount, shortfall, cash_after)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, bill_id, kind, label, payee, due_date, amount, shortfall, cash_after
                """,
                (
                    session_id,
                    flow["bill_id"],
                    flow["kind"],
                    flow["label"],
                    flow.get("payee"),
                    flow["due_date"],
                    paid,
                    amount - paid,
                    cash,
                ),
            )
            applied.append({**cur.fetchone(), "sold": sold})

        for ticker in sold_tickers:
            if ticker in holdings:
                cur.execute(
                    "UPDATE portfolio_holdings SET shares = %s, updated_at = now() "
                    "WHERE session_id = %s AND ticker = %s",
                    (holdings[ticker], session_id, ticker),
                )
            else:
                cur.execute(
                    "DELETE FROM portfolio_holdings WHERE session_id = %s AND ticker = %s",
                    (session_id, ticker),
                )
        if applied:
            cur.execute(
                "UPDATE game_sessions SET cash_balance = %s, updated_at = now() WHERE id = %s",
                (cash, session_id),
            )
        return applied, cash


def list_expenses(session_id: str) -> list[dict]:
    with get_cursor() as cur:
        cur.execute(
            "SELECT id, bill_id, label, payee, due_date, amount, cash_after "
            "FROM session_expenses WHERE session_id = %s ORDER BY due_date, id",
            (session_id,),
        )
        return cur.fetchall()


# --- MCP event log --------------------------------------------------------


def insert_mcp_event(
    session_id: str, ticker: str, sim_date: date, event_type: str, payload: dict
) -> int:
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO mcp_events (session_id, event_type, ticker, sim_date, payload)
            VALUES (%s, %s, %s, %s, %s) RETURNING id
            """,
            (session_id, event_type, ticker.upper(), sim_date, json.dumps(payload)),
        )
        return cur.fetchone()["id"]


def update_mcp_event_payload(event_id: int, payload: dict) -> None:
    """Rewrite an event's payload in place.

    Shocks are logged before they are applied (the bars need the event id), so the
    "how far did it actually reach" numbers are written back a moment later.
    """
    with get_cursor() as cur:
        cur.execute(
            "UPDATE mcp_events SET payload = %s::jsonb WHERE id = %s",
            (json.dumps(payload), event_id),
        )


# --- ticker universe ------------------------------------------------------


def upsert_tickers(rows: Iterable[tuple]) -> int:
    """Seed/refresh the ticker catalog. Rows are (ticker, name, sector, universe, is_tech)."""
    sql = """
        INSERT INTO tickers (ticker, company_name, sector, universe, is_tech)
        VALUES %s
        ON CONFLICT (ticker) DO UPDATE SET
            company_name = EXCLUDED.company_name,
            sector = EXCLUDED.sector,
            universe = EXCLUDED.universe,
            is_tech = EXCLUDED.is_tech
    """
    rows = list(rows)
    if not rows:
        return 0
    with get_transaction(dict_rows=False) as cur:
        execute_values(cur, sql, rows)
    return len(rows)


def list_universe(universe: str | None = None, tech_only: bool = False) -> list[dict]:
    """Catalog of known symbols, left-joined with the history we actually have.

    A NULL first_day means "known ticker, nothing ingested yet" - the UI uses that to
    show the difference between an empty universe and a broken one.
    """
    clauses: list[str] = []
    params: list[Any] = []
    if universe:
        clauses.append("t.universe = %s")
        params.append(universe)
    if tech_only:
        clauses.append("t.is_tech")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_cursor() as cur:
        cur.execute(
            f"""
            SELECT t.ticker, t.company_name, t.sector, t.universe, t.is_tech,
                   p.first_day, p.last_day, coalesce(p.row_count, 0) AS row_count,
                   (p.row_count IS NOT NULL) AS has_data
            FROM tickers t
            LEFT JOIN (
                SELECT ticker, min(ts) AS first_day, max(ts) AS last_day, count(*) AS row_count
                FROM stock_prices GROUP BY ticker
            ) p ON p.ticker = t.ticker
            {where}
            ORDER BY t.ticker
            """,
            tuple(params),
        )
        return cur.fetchall()


def ticker_sectors(tickers: Iterable[str] | None = None) -> dict[str, dict]:
    """Sector (and tech flag) per symbol, as a lookup.

    A sector-wide headline has to know which of a session's symbols share a sector, and
    one small read of the catalog is cheaper than a query per symbol per event.
    """
    wanted = [ticker.upper() for ticker in tickers] if tickers else None
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT ticker, sector, is_tech
            FROM tickers
            WHERE (%s::text[] IS NULL OR ticker = ANY(%s::text[]))
            """,
            (wanted, wanted),
        )
        return {
            row["ticker"]: {"sector": row["sector"] or "", "is_tech": bool(row["is_tech"])}
            for row in cur.fetchall()
        }


def universe_stats() -> dict:
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT count(*) AS known,
                   count(*) FILTER (WHERE coalesce(p.row_count, 0) > 0) AS ingested,
                   count(*) FILTER (WHERE t.is_tech) AS tech
            FROM tickers t
            LEFT JOIN (
                SELECT ticker, count(*) AS row_count FROM stock_prices GROUP BY ticker
            ) p ON p.ticker = t.ticker
            """
        )
        return cur.fetchone()


# --- per-user simulated future --------------------------------------------

_SIMULATION_COLUMNS = (
    "ticker",
    "fork_date",
    "anchor_price",
    "horizon_days",
    "drift",
    "volatility",
    "mean_reversion",
    "generator",
    "seed",
    "last_generated",
)


def create_simulation(session_id: str, **fields: Any) -> dict:
    unknown = set(fields) - set(_SIMULATION_COLUMNS)
    if unknown:
        raise ValueError(f"unknown simulation field(s): {sorted(unknown)}")
    fields["ticker"] = str(fields["ticker"]).upper()
    columns = ", ".join(fields)
    placeholders = ", ".join("%s" for _ in fields)
    with get_cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO user_simulations (session_id, {columns})
            VALUES (%s, {placeholders})
            ON CONFLICT (session_id, ticker) DO UPDATE SET
                {', '.join(f'{name} = EXCLUDED.{name}' for name in fields)},
                updated_at = now()
            RETURNING *
            """,
            (session_id, *fields.values()),
        )
        return cur.fetchone()


def get_simulation(session_id: str, ticker: str) -> dict | None:
    with get_cursor() as cur:
        cur.execute(
            "SELECT * FROM user_simulations WHERE session_id = %s AND ticker = %s",
            (session_id, ticker.upper()),
        )
        return cur.fetchone()


def get_simulations(session_id: str) -> list[dict]:
    with get_cursor() as cur:
        cur.execute(
            "SELECT * FROM user_simulations WHERE session_id = %s ORDER BY ticker",
            (session_id,),
        )
        return cur.fetchall()


def update_simulation(session_id: str, ticker: str, **fields: Any) -> dict | None:
    unknown = set(fields) - set(_SIMULATION_COLUMNS)
    if unknown:
        raise ValueError(f"unknown simulation field(s): {sorted(unknown)}")
    if not fields:
        return get_simulation(session_id, ticker)
    assignments = ", ".join(f"{name} = %s" for name in fields)
    with get_cursor() as cur:
        cur.execute(
            f"UPDATE user_simulations SET {assignments}, updated_at = now() "
            "WHERE session_id = %s AND ticker = %s RETURNING *",
            (*fields.values(), session_id, ticker.upper()),
        )
        return cur.fetchone()




def upsert_simulated_prices(session_id: str, ticker: str, rows: Iterable[tuple]) -> int:
    """Rows are (ts, open, high, low, close, volume, event_id)."""
    sql = """
        INSERT INTO simulated_prices
            (session_id, ticker, ts, open, high, low, close, volume, event_id)
        VALUES %s
        ON CONFLICT (session_id, ticker, ts) DO UPDATE SET
            open = EXCLUDED.open,
            high = EXCLUDED.high,
            low = EXCLUDED.low,
            close = EXCLUDED.close,
            volume = EXCLUDED.volume,
            event_id = coalesce(EXCLUDED.event_id, simulated_prices.event_id)
    """
    rows = list(rows)
    if not rows:
        return 0
    payload = [(session_id, ticker.upper(), *row) for row in rows]
    with get_transaction(dict_rows=False) as cur:
        execute_values(cur, sql, payload, page_size=INSERT_PAGE_SIZE)
    return len(payload)


def fetch_simulated_prices(
    session_id: str, ticker: str, start_date: date, end_date: date
) -> pd.DataFrame:
    """Same column shape as fetch_price_history so the two frames can be concatenated."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT ts, open, high, low, close, close AS adj_close, volume
            FROM simulated_prices
            WHERE session_id = %s AND ticker = %s AND ts BETWEEN %s AND %s
            ORDER BY ts
            """,
            (session_id, ticker.upper(), start_date, end_date),
        )
        rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "adj_close", "volume"])
    if df.empty:
        return df
    for col in ("open", "high", "low", "close", "adj_close"):
        df[col] = df[col].astype(float)
    df["volume"] = df["volume"].astype("int64")
    return df


def rescale_simulated_prices(session_id: str, ticker: str, updates: Iterable[tuple]) -> int:
    """Rewrite already-generated bars after a shock.

    updates are (open, high, low, close, volume, event_id, ts); a NULL event_id leaves
    whatever tag the bar already carried, so a second shock never un-attributes the first.
    """
    updates = list(updates)
    if not updates:
        return 0
    # One statement, not one per bar. `executemany` sends each row as its own round trip,
    # which against a remote database made a single 252-bar shock take about seventeen
    # seconds; a VALUES list is paged the same way the insert path already is.
    sql = """
        UPDATE simulated_prices AS target
        SET open = batch.open,
            high = batch.high,
            low = batch.low,
            close = batch.close,
            volume = batch.volume,
            event_id = coalesce(batch.event_id, target.event_id)
        FROM (VALUES %s) AS batch (session_id, ticker, ts, open, high, low, close, volume, event_id)
        WHERE target.session_id = batch.session_id
          AND target.ticker = batch.ticker
          AND target.ts = batch.ts
    """
    # The casts are not decoration: a batch whose event_id is all NULLs would otherwise be
    # inferred as text, and coalesce(text, bigint) is a type error rather than a shrug.
    template = "(%s::uuid, %s, %s::date, %s, %s, %s, %s, %s, %s::bigint)"
    payload = [
        (session_id, ticker.upper(), ts, o, h, low, c, v, event)
        for o, h, low, c, v, event, ts in updates
    ]
    # Every row is a *multiplier* on the bar it replaces, so this is the one write that
    # genuinely has to be all-or-nothing: a half-applied shock cannot be re-run.
    with get_transaction(dict_rows=False) as cur:
        execute_values(cur, sql, payload, template=template, page_size=INSERT_PAGE_SIZE)
    return len(payload)


def latest_quotes(session_id: str, tickers: list[str], as_of: date) -> dict[str, dict]:
    """Last close (and the one before it) for each symbol at or before a date.

    One index-driven LATERAL lookup per symbol instead of loading a full price frame for
    each: the game needs two numbers for every symbol on every tick, and building an
    eleven-thousand-row frame per symbol just to read its tail was the slowest part of
    the request. Simulated bars are unioned in so a forked session reads them seamlessly.
    """
    tickers = [ticker.upper() for ticker in tickers]
    if not tickers:
        return {}

    # Which source a symbol answers from depends on the session, not just the date. A
    # forked symbol reads its generated bars from the fork onward; the real series must be
    # cut off there, because the catalog now holds real history past any fork and taking
    # the newest row across both would quote a real 2025 close for a chart that is drawing
    # generated prices - the player would trade at a price they cannot see.
    #
    # Each branch is its own ORDER BY ... LIMIT 1 so Postgres walks the (ticker, ts DESC)
    # index backwards and stops after one row; written as a single UNION with an outer sort
    # it would read every bar up to as_of for every symbol instead.
    latest = """
        SELECT * FROM (
            (SELECT ts, close, FALSE AS simulated FROM stock_prices
             WHERE ticker = t.ticker
               AND ts <= LEAST(%(as_of)s::date, coalesce(us.fork_date, %(as_of)s::date))
             ORDER BY ts DESC LIMIT 1)
            UNION ALL
            (SELECT ts, close, TRUE AS simulated FROM simulated_prices
             WHERE session_id = %(session_id)s AND ticker = t.ticker
               AND ts <= %(as_of)s::date
             ORDER BY ts DESC LIMIT 1)
        ) s ORDER BY ts DESC LIMIT 1
    """
    with get_cursor() as cur:
        cur.execute(
            f"""
            SELECT t.ticker, last.ts, last.close, last.simulated, prev.close AS previous_close
            FROM unnest(%(tickers)s::text[]) AS t(ticker)
            LEFT JOIN user_simulations us
                   ON us.session_id = %(session_id)s AND us.ticker = t.ticker
            CROSS JOIN LATERAL ({latest}) last
            LEFT JOIN LATERAL (
                SELECT close FROM (
                    (SELECT ts, close FROM stock_prices
                     WHERE ticker = t.ticker AND ts < last.ts
                       AND ts <= LEAST(%(as_of)s::date,
                                       coalesce(us.fork_date, %(as_of)s::date))
                     ORDER BY ts DESC LIMIT 1)
                    UNION ALL
                    (SELECT ts, close FROM simulated_prices
                     WHERE session_id = %(session_id)s AND ticker = t.ticker
                       AND ts < last.ts
                     ORDER BY ts DESC LIMIT 1)
                ) p ORDER BY p.ts DESC LIMIT 1
            ) prev ON TRUE
            """,
            {"tickers": tickers, "session_id": session_id, "as_of": as_of},
        )
        rows = cur.fetchall()

    quotes: dict[str, dict] = {}
    for row in rows:
        close = float(row["close"])
        previous = row["previous_close"]
        previous = float(previous) if previous is not None else None
        quotes[row["ticker"]] = {
            "close": close,
            "previous_close": previous,
            "change_pct": None if not previous else (close - previous) / previous * 100,
            "simulated": bool(row["simulated"]),
            "date": row["ts"].isoformat(),
        }
    return quotes


def simulated_shock_log(session_id: str, ticker: str | None = None, limit: int = 20) -> list[dict]:
    """Every event that touched a generated future, newest first.

    One row per event (not per bar) with the window it repriced - a single headline
    bends dozens of bars, and listing those individually is noise.
    """
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT e.id AS event_id, e.ticker, e.payload,
                   min(s.ts) AS first_bar, max(s.ts) AS last_bar,
                   count(*) AS bars, min(s.close) AS low_close, max(s.close) AS high_close
            FROM simulated_prices s
            JOIN mcp_events e ON e.id = s.event_id
            WHERE s.session_id = %s AND (%s IS NULL OR s.ticker = %s)
            GROUP BY e.id, e.ticker, e.payload
            ORDER BY first_bar DESC
            LIMIT %s
            """,
            (session_id, ticker.upper() if ticker else None, ticker.upper() if ticker else None, limit),
        )
        return cur.fetchall()


