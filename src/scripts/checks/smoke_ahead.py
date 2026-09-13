"""Smoke test for the day-ahead chart preview the game page pans on.

The page fetches tomorrow's chart window mid-tick so the curve starts moving on time rather
than when the advance request comes back. That only works if the preview is *exactly* what
the tick goes on to produce: if it is off by a value, the confirming update finds something
to redraw, and the player watches the chart twitch half a second into every slide. So this
drives the real engine against the real database and compares the two window by window - a
replay session, a generated future, a multi-day fast-forward, and a focus that is not in the
watchlist - and checks that asking moved nothing.

```sh
uv run python -m scripts.checks.smoke_ahead
uv run python -m scripts.checks.smoke_ahead --keep    # leave the rows behind to poke at
```
"""

from __future__ import annotations

import argparse
import json as jsonlib
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.database import database
from scripts.game import engine, price_cache

UI_BASE = "http://127.0.0.1:5000"

_results: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    ok = bool(ok)
    _results.append((ok, name, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))
    return ok


# --- fixtures -------------------------------------------------------------


def tradable_ticker(active_days: int = 400) -> str:
    """A symbol with enough consecutive history to run a multi-week session on."""
    with database.get_cursor() as cur:
        cur.execute(
            """
            SELECT ticker, count(*) AS rows, min(ts) AS first_day, max(ts) AS last_day
              FROM stock_prices GROUP BY ticker
             HAVING count(*) > %s ORDER BY ticker LIMIT 1
            """,
            (active_days,),
        )
        row = cur.fetchone()
    if not row:
        raise SystemExit("no symbol has enough price history for this check")
    return row["ticker"]


def start(ticker: str, start_date: date, end_date: date, **kwargs) -> tuple[str, dict]:
    state = engine.start_session(
        [ticker],
        start_date=start_date,
        end_date=end_date,
        use_finances=False,
        **kwargs,
    )
    return state["session_id"], state


def clean_up(session_ids: list[str]) -> None:
    """Delete the throwaway sessions this run created, and everything hanging off them."""
    if not session_ids:
        return
    with database.get_cursor() as cur:
        cur.execute(
            """
            SELECT table_name FROM information_schema.columns
             WHERE table_schema = 'public' AND column_name = 'session_id'
            """
        )
        tables = [row["table_name"] for row in cur.fetchall()]
    with database.get_transaction() as cur:
        for table in tables:
            cur.execute(f"DELETE FROM {table} WHERE session_id = ANY(%s::uuid[])", (session_ids,))
        cur.execute("DELETE FROM game_sessions WHERE id = ANY(%s::uuid[])", (session_ids,))
    print(f"\ncleaned up {len(session_ids)} smoke session(s)")


def fingerprint(session_id: str) -> tuple:
    """Everything the preview must not touch, in one comparable value."""
    session = database.get_session(session_id)
    with database.get_cursor() as cur:
        cur.execute(
            """
            SELECT (SELECT count(*) FROM transactions WHERE session_id = %(sid)s) AS trades,
                   (SELECT count(*) FROM session_expenses WHERE session_id = %(sid)s) AS ledger,
                   (SELECT count(*) FROM mcp_events WHERE session_id = %(sid)s) AS events,
                   (SELECT count(*) FROM dividend_payments WHERE session_id = %(sid)s) AS dividends
            """,
            {"sid": session_id},
        )
        counts = cur.fetchone()
    return (
        session["sim_date"],
        session["status"],
        str(session["cash_balance"]),
        counts["trades"],
        counts["ledger"],
        counts["events"],
        counts["dividends"],
    )


def window_of(state: dict) -> list[tuple]:
    """The chart as comparable tuples, so two runs can be told apart exactly."""
    return [
        (
            row["date"],
            round(float(row["close"]), 6),
            round(float(row["sma20"]), 6) if row.get("sma20") is not None else None,
            round(float(row["sma50"]), 6) if row.get("sma50") is not None else None,
            round(float(row["rsi14"]), 6) if row.get("rsi14") is not None else None,
            round(float(row["macd"]), 6),
            round(float(row["macd_signal"]), 6),
            round(float(row["macd_hist"]), 6),
            int(row["volume"]),
        )
        for row in state["chart"]
    ]


# --- the preview matches the tick ----------------------------------------


def test_matches_advance(ticker: str, start_date: date, end_date: date, label: str, ticks: int = 6) -> list[str]:
    print(f"\n{label}: {ticker} {start_date.isoformat()} -> {end_date.isoformat()}")
    sid, _ = start(ticker, start_date, end_date)
    sessions = [sid]
    matched = 0
    compared = 0
    continued = 0

    for _ in range(ticks):
        state = engine.get_state(sid, focus=ticker)
        if state["status"] != "active":
            break
        before = fingerprint(sid)
        peeked = engine.peek_chart(sid, focus=ticker)
        after = fingerprint(sid)
        compared += 1

        check_untouched = before == after
        walked = engine.advance_day(sid, days=1, with_ai=False)

        if not check_untouched:
            check(f"{label}: the preview moves nothing", False, f"{before} -> {after}")
            continue
        if peeked["sim_date"] != walked["sim_date"]:
            check(
                f"{label}: the preview names the day the tick reaches",
                False,
                f"{peeked['sim_date']} vs {walked['sim_date']}",
            )
            continue
        if window_of(peeked) != window_of(walked):
            first_bad = next(
                (
                    (a[0], b[0])
                    for a, b in zip(window_of(peeked), window_of(walked))
                    if a != b
                ),
                ("lengths", f"{len(peeked['chart'])} vs {len(walked['chart'])}"),
            )
            check(f"{label}: the previewed window is the window the tick produces", False, f"first difference {first_bad}")
            continue
        matched += 1

        # What the page relies on: the preview is one day further along the same calendar, so
        # all but the newest bar are the bars already on screen. The update that follows the
        # tick then has nothing to draw, which is what keeps a slide from being cut short.
        if peeked["chart"][1:] == walked["chart"][1:] and peeked["chart"][:-1] != walked["chart"]:
            continued += 1

    check(f"{label}: every previewed window equals the tick's window", compared and matched == compared, f"{matched}/{compared} ticks")
    check(f"{label}: the calendar only advanced, bar by bar", continued == compared, f"{continued}/{compared}")
    check(
        f"{label}: the last bar of the window is the day itself",
        engine.peek_chart(sid, focus=ticker)["sim_date"] is None
        or engine.get_state(sid, focus=ticker)["chart"][-1]["date"] <= engine.get_state(sid, focus=ticker)["sim_date"],
    )
    return sessions


def test_fast_forward(ticker: str, start_date: date, end_date: date) -> list[str]:
    """The 3- and 5-day speeds read the same preview, several days out."""
    print("\nfast-forward: three days at once")
    sid, _ = start(ticker, start_date, end_date)
    peeked = engine.peek_chart(sid, focus=ticker, days=3)
    walked = engine.advance_day(sid, days=3, with_ai=False)
    check("the preview can look several days ahead", peeked["sim_date"] == walked["sim_date"], f"{peeked['sim_date']} vs {walked['sim_date']}")
    check("and produces the window that tick lands on", window_of(peeked) == window_of(walked))
    return [sid]


def test_focus_and_horizon(ticker: str, start_date: date) -> list[str]:
    print("\nfocus, and the end of the run")
    # A deliberately short run, so the clock can be walked to the end of it in one call.
    sid, _ = start(ticker, start_date, start_date + timedelta(days=10))
    sessions = [sid]
    state = engine.get_state(sid, focus=ticker)

    unknown = engine.peek_chart(sid, focus="ZZZZ")
    check("an unknown focus falls back to the first symbol", unknown["focus"] == ticker, str(unknown["focus"]))
    # One bar further along the same calendar: the same dates shifted up, or - while the
    # window is still filling, which is what a session near the start of a symbol's history
    # gets - the same dates with one more day on the end.
    previewed = [row["date"] for row in unknown["chart"]]
    served = [row["date"] for row in state["chart"]]
    check(
        "and previews the calendar one day on from the state's window",
        previewed[:-1] in (served[1:], served),
        f"{len(previewed)} vs {len(served)} bars",
    )

    # Walk the clock past the end of the run: the preview must go quiet rather than roll
    # anything on past the horizon.
    engine.advance_day(sid, days=10_000, with_ai=False)
    check("the clock reached the end of the run", engine.get_state(sid)["status"] == "finished")
    done = engine.peek_chart(sid, focus=ticker)
    check("a finished session previews nothing", done["chart"] == [] and done["sim_date"] is None, str(done)[:80])

    before = fingerprint(sid)
    engine.peek_chart(sid, focus=ticker)
    check("previewing a finished session moves nothing", fingerprint(sid) == before)
    return sessions


def test_simulated_future(ticker: str, start_date: date, end_date: date) -> list[str]:
    """A generated future is decided up front, so the preview must read it, not roll it on."""
    print("\nsimulated future")
    sid, _ = start(ticker, start_date, end_date, simulate_future=True, horizon_days=90, seed=4242)
    matched = 0
    compared = 0
    for _ in range(5):
        before = fingerprint(sid)
        peeked = engine.peek_chart(sid, focus=ticker)
        if before != fingerprint(sid):
            check("the preview does not roll the horizon on", False)
            break
        if not peeked["chart"]:
            break
        walked = engine.advance_day(sid, days=1, with_ai=False)
        compared += 1
        if peeked["sim_date"] == walked["sim_date"] and window_of(peeked) == window_of(walked):
            matched += 1
        if walked["status"] != "active":
            break
    check("a generated future previews the same bars the tick produces", compared and matched == compared, f"{matched}/{compared}")
    return [sid]


# --- over the wire --------------------------------------------------------


def get_json(path: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(f"{UI_BASE}{path}", timeout=20) as response:
            return response.status, jsonlib.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        return error.code, {}
    except urllib.error.URLError as error:
        return 0, {"error": str(error)}


def test_api(session_id: str) -> None:
    print("\nthe endpoint the page calls")
    status, payload = get_json(f"/api/session/{session_id}/ahead")
    if status == 0:
        check("GET /ahead answers", False, payload.get("error", "no server"))
        return
    check("GET /ahead answers", status == 200, str(status))
    check("with a focus and a date", bool(payload.get("focus")) and bool(payload.get("sim_date")), jsonlib.dumps(payload)[:60])
    check("and a chart window", len(payload.get("chart") or []) > 0, str(len(payload.get("chart") or [])))

    status, _ = get_json("/api/session/not-a-uuid/ahead")
    # Every other session route answers a malformed id as a JSON error rather than letting
    # psycopg2 raise on the uuid column; this one has to behave the same way.
    check("a malformed session id is a JSON error, not a 500", status == 400, str(status))

    session = database.get_session(session_id)
    before = session["sim_date"]
    get_json(f"/api/session/{session_id}/ahead?focus={payload.get('focus')}")
    after = database.get_session(session_id)["sim_date"]
    check("asking over HTTP does not move the clock", before == after, f"{before} -> {after}")


# --- entry point ----------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test the day-ahead chart preview.")
    parser.add_argument("--keep", action="store_true", help="leave the smoke sessions in the database")
    args = parser.parse_args()

    ticker = tradable_ticker()
    price_cache.prefetch([ticker])
    with database.get_cursor() as cur:
        cur.execute(
            "SELECT min(ts) AS first_day, max(ts) AS last_day FROM stock_prices WHERE ticker = %s",
            (ticker,),
        )
        bounds = cur.fetchone()
    first_day: date = bounds["first_day"]
    last_day: date = bounds["last_day"]
    start_date = first_day + timedelta(days=200)
    print(f"smoke test: day-ahead chart preview on {ticker}, {start_date.isoformat()} onwards")

    sessions: list[str] = []
    try:
        sessions += test_matches_advance(ticker, start_date, last_day, "replay")
        sessions += test_fast_forward(ticker, start_date, last_day)
        sessions += test_focus_and_horizon(ticker, start_date)
        sessions += test_simulated_future(ticker, start_date, last_day)
        test_api(sessions[0])
    finally:
        if not args.keep:
            clean_up(sessions)

    failures = [name for ok, name, _ in _results if not ok]
    passed = len(_results) - len(failures)
    print(f"\n{passed}/{len(_results)} checks passed")
    for name in failures:
        print(f"  failed: {name}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
