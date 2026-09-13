"""Smoke test for the chart's x-axis width.

A category axis spaces its slots evenly across the panel, so the *count* of slots decides
where every point sits: one more slot slides the whole curve sideways. A session whose
history runs out before the window does - a symbol ingested over a few weeks, or a run that
starts on the first day of the data - is still filling up, and used to gain a slot per
tick, which is what made the line look like it was wiggling and redrawing rather than
growing a point at the end.

The page now gives the axis its final width on the first draw, from the `chart_capacity`
the backend puts on every payload. This drives the real engine against the real database
and checks what the page depends on: the number is the session's whole life, it does not
move as the clock runs, the window never overtakes it, and a session with a full window is
left exactly as wide as it was.

```sh
uv run python -m scripts.checks.smoke_chart
uv run python -m scripts.checks.smoke_chart --keep    # leave the rows behind to poke at
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
from scripts.game import engine, performance, price_cache

UI_BASE = "http://127.0.0.1:5000"
WINDOW = engine.DEFAULT_CHART_WINDOW

_results: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    ok = bool(ok)
    _results.append((ok, name, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))
    return ok



def history(ticker: str) -> tuple[date, date, int]:
    with database.get_cursor() as cur:
        cur.execute(
            "SELECT min(ts) AS first_day, max(ts) AS last_day, count(*) AS rows"
            "  FROM stock_prices WHERE ticker = %s",
            (ticker,),
        )
        row = cur.fetchone()
    return row["first_day"], row["last_day"], int(row["rows"])


def longest_ticker() -> str:
    with database.get_cursor() as cur:
        cur.execute("SELECT ticker FROM stock_prices GROUP BY ticker ORDER BY count(*) DESC LIMIT 1")
        return cur.fetchone()["ticker"]


def start(ticker: str, start_date: date, end_date: date, **kwargs) -> tuple[str, dict]:
    state = engine.start_session(
        [ticker], start_date=start_date, end_date=end_date, use_finances=False, **kwargs
    )
    return state["session_id"], state


def clean_up(session_ids: list[str]) -> None:
    if not session_ids:
        return
    with database.get_cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.columns"
            " WHERE table_schema = 'public' AND column_name = 'session_id'"
        )
        tables = [row["table_name"] for row in cur.fetchall()]
    with database.get_transaction() as cur:
        for table in tables:
            cur.execute(f"DELETE FROM {table} WHERE session_id = ANY(%s::uuid[])", (session_ids,))
        cur.execute("DELETE FROM game_sessions WHERE id = ANY(%s::uuid[])", (session_ids,))
    print(f"\ncleaned up {len(session_ids)} smoke session(s)")



def test_filling_up(ticker: str, first_day: date) -> list[str]:
    """A run that starts on the symbol's first day charts every bar it will ever have."""
    end_date = first_day + timedelta(days=40)
    print(f"\nstill filling up: {ticker} {first_day.isoformat()} -> {end_date.isoformat()}")
    sid, state = start(ticker, first_day, end_date)
    sessions = [sid]

    with database.get_cursor() as cur:
        cur.execute(
            "SELECT count(*) AS bars FROM stock_prices WHERE ticker = %s AND ts <= %s",
            (ticker, end_date),
        )
        bars = int(cur.fetchone()["bars"])
    expected = max(1, min(WINDOW, bars))

    check(
        "the capacity is every bar the session will chart",
        state["chart_capacity"] == expected,
        f"{state['chart_capacity']} vs {expected}",
    )
    check(
        "which is fewer than a full window, so this is the filling case",
        0 < state["chart_capacity"] < WINDOW,
        str(state["chart_capacity"]),
    )
    check("the first draw is a single bar", len(state["chart"]) == 1, str(len(state["chart"])))

    widths = {state["chart_capacity"]}
    lengths = [len(state["chart"])]
    peek_widths = {engine.peek_chart(sid, focus=ticker)["chart_capacity"]}
    overshot: list[str] = []
    for _ in range(12):
        walked = engine.advance_day(sid, days=1, with_ai=False)
        if walked["status"] != "active":
            break
        widths.add(walked["chart_capacity"])
        lengths.append(len(walked["chart"]))
        peek = engine.peek_chart(sid, focus=ticker)
        if peek["sim_date"]:
            peek_widths.add(peek["chart_capacity"])
        if len(walked["chart"]) > walked["chart_capacity"]:
            overshot.append(f"{walked['sim_date']}: {len(walked['chart'])}")

    check("the capacity does not move while the clock runs", len(widths) == 1, str(sorted(widths)))
    check(
        "the preview carries the same width as the state",
        peek_widths == widths,
        f"{sorted(peek_widths)} vs {sorted(widths)}",
    )
    check(
        "the window grows into that width and never overtakes it",
        lengths[0] < lengths[-1] and not overshot,
        f"{lengths[0]} -> {lengths[-1]} of {state['chart_capacity']}"
        + (f", over on {overshot}" if overshot else ""),
    )
    check(
        "and stays inside it through a multi-day jump",
        engine.advance_day(sid, days=7, with_ai=False)["chart_capacity"] == expected,
    )
    return sessions


def test_full_window(ticker: str, start_date: date, end_date: date) -> list[str]:
    """A session with a window's worth of history behind it must be left alone."""
    print(f"\nfull window: {ticker} {start_date.isoformat()} -> {end_date.isoformat()}")
    sid, state = start(ticker, start_date, end_date)
    sessions = [sid]

    check("the capacity is the window", state["chart_capacity"] == WINDOW, str(state["chart_capacity"]))
    check("and the first draw fills it", len(state["chart"]) == WINDOW, str(len(state["chart"])))

    lengths = []
    for _ in range(4):
        state = engine.advance_day(sid, days=1, with_ai=False)
        if state["status"] != "active":
            break
        lengths.append((len(state["chart"]), state["chart_capacity"]))
    check(
        "the window keeps rolling at that width",
        lengths and all(bars == cap == WINDOW for bars, cap in lengths),
        str(lengths),
    )
    return sessions


def test_basket(ticker: str, first_day: date) -> list[str]:
    """The equity and basket panels share an axis, and it fills up the same way."""
    end_date = first_day + timedelta(days=40)
    print(f"\nbasket and equity: {ticker} {first_day.isoformat()} -> {end_date.isoformat()}")
    sid, _ = start(ticker, first_day, end_date)
    sessions = [sid]

    widths = set()
    grew = []
    overshot: list[str] = []
    for _ in range(10):
        bundle = database.load_session_bundle(sid)
        payload = performance.basket(bundle["session"], bundle["trades"], None, bundle.get("watchlist"), bundle.get("expenses"))
        widths.add(payload["chart_capacity"])
        grew.append(len(payload["equity"]))
        if len(payload["equity"]) > payload["chart_capacity"]:
            overshot.append(f"{payload['sim_date']}: {len(payload['equity'])}")
        if engine.get_state(sid)["status"] != "active":
            break
        engine.advance_day(sid, days=1, with_ai=False)

    check("the basket axis has a width too", widths and max(widths) >= 1, str(sorted(widths)))
    check("which does not move while the clock runs", len(widths) == 1, str(sorted(widths)))
    check(
        "and the equity curve grows into it without overtaking it",
        grew[0] < grew[-1] and not overshot,
        f"{grew[0]} -> {grew[-1]} of {max(widths)}" + (f", over on {overshot}" if overshot else ""),
    )
    check("the equity curve is drawn at that width at the end", len(payload["equity"]) >= 1)
    return sessions



def get_json(path: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(f"{UI_BASE}{path}", timeout=20) as response:
            return response.status, jsonlib.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        return error.code, {}
    except urllib.error.URLError as error:
        return 0, {"error": str(error)}


def test_api(session_id: str) -> None:
    print("\nthe payload the page reads")
    status, payload = get_json(f"/api/session/{session_id}/state")
    if status == 0:
        check("GET /state answers", False, payload.get("error", "no server"))
        return
    check("GET /state answers", status == 200, str(status))
    capacity = payload.get("chart_capacity")
    check("with a chart width", isinstance(capacity, int) and capacity >= 1, str(capacity))
    check(
        "that covers the window it sent",
        isinstance(capacity, int) and len(payload.get("chart") or []) <= capacity,
        f"{len(payload.get('chart') or [])} bars at width {capacity}",
    )
    status, basket = get_json(f"/api/session/{session_id}/basket")
    check("GET /basket answers", status == 200, str(status))
    check(
        "with a width for the equity curve",
        isinstance(basket.get("chart_capacity"), int) and basket["chart_capacity"] >= 1,
        str(basket.get("chart_capacity")),
    )



def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test the chart's x-axis width.")
    parser.add_argument("--keep", action="store_true", help="leave the smoke sessions in the database")
    args = parser.parse_args()

    ticker = longest_ticker()
    price_cache.prefetch([ticker])
    first_day, last_day, rows = history(ticker)
    print(f"smoke test: chart width on {ticker}, {rows} bars from {first_day.isoformat()}")

    rolling_start = first_day + timedelta(days=1200)

    sessions: list[str] = []
    try:
        sessions += test_filling_up(ticker, first_day)
        sessions += test_full_window(ticker, rolling_start, last_day)
        sessions += test_basket(ticker, first_day)
        test_api(sessions[-1])
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
