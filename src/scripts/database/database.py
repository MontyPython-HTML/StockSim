"""Shared TigerData (TimescaleDB) access layer used by both Flask and the MCP server."""

import json
import threading
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor, execute_values
from psycopg2.pool import ThreadedConnectionPool

import config

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

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
    pool = _get_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


@contextmanager
def get_cursor(dict_rows: bool = True):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor if dict_rows else None) as cur:
            yield cur


def apply_schema() -> None:
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
    with get_cursor(dict_rows=False) as cur:
        execute_values(cur, sql, rows)
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


def available_tickers() -> list[dict]:
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT ticker, min(ts) AS first_day, max(ts) AS last_day, count(*) AS row_count
            FROM stock_prices GROUP BY ticker ORDER BY ticker
            """
        )
        return cur.fetchall()


# --- sessions -------------------------------------------------------------


def create_session(
    session_id: str,
    ticker: str,
    start_date: date,
    end_date: date,
    sim_date: date,
    starting_cash: Decimal,
    nessie_customer_id: str | None,
    nessie_account_id: str | None,
    user_label: str = "anonymous",
) -> dict:
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO game_sessions (
                id, user_label, ticker, start_date, end_date, sim_date,
                starting_cash, cash_balance, nessie_customer_id, nessie_account_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                session_id,
                user_label,
                ticker.upper(),
                start_date,
                end_date,
                sim_date,
                starting_cash,
                starting_cash,
                nessie_customer_id,
                nessie_account_id,
            ),
        )
        return cur.fetchone()


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
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
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


def load_session_bundle(session_id: str, event_limit: int = 30) -> dict:
    """Session, holdings, trades and AI events in one round trip.

    The database is remote, so collapsing these four reads into a single query is worth
    the SQL - it is the difference between a ~600ms and a ~150ms game tick.
    """
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT
                (SELECT row_to_json(s) FROM game_sessions s WHERE s.id = %(sid)s) AS session,
                (SELECT coalesce(json_agg(h), '[]'::json) FROM (
                    SELECT ticker, shares, avg_cost FROM portfolio_holdings
                    WHERE session_id = %(sid)s AND shares > 0 ORDER BY ticker
                ) h) AS holdings,
                (SELECT coalesce(json_agg(t), '[]'::json) FROM (
                    SELECT id, ticker, trade_date, side, shares, price, cash_after
                    FROM transactions WHERE session_id = %(sid)s ORDER BY trade_date, id
                ) t) AS trades,
                (SELECT coalesce(json_agg(e), '[]'::json) FROM (
                    SELECT id, event_type, ticker, sim_date, payload, created_at
                    FROM mcp_events WHERE session_id = %(sid)s
                    ORDER BY created_at DESC, id DESC LIMIT %(limit)s
                ) e) AS events
            """,
            {"sid": session_id, "limit": event_limit},
        )
        return cur.fetchone()


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


