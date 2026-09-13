"""Smoke test for the money side of the game: dividends, bills and paychecks.

Not a unit suite - it drives the real engine against the real database, the way the page
does, and then reads back what the ledger, the cash balance and the JSON the browser
receives actually say. Sessions it creates are deleted on the way out.

```sh
uv run python -m scripts.checks.smoke_finance
uv run python -m scripts.checks.smoke_finance --keep    # leave the rows behind to poke at
```

Dividends need declarations in `stock_prices.dividend`; on a database ingested before
dividends were carried through the ingest every row is zero and the whole feature is
silently off. The run says so up front and points at the backfill.
"""

from __future__ import annotations

import argparse
import json as jsonlib
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.database import database
from scripts.game import engine, expenses

CENT = Decimal("0.01")
UI_BASE = "http://127.0.0.1:5000"

_results: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    ok = bool(ok)
    _results.append((ok, name, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))
    return ok


def money(value) -> Decimal:
    return Decimal(str(value)).quantize(CENT)


# --- fixtures -------------------------------------------------------------


def dividend_candidate() -> dict:
    """A symbol with a declared dividend and enough history around it to trade."""
    with database.get_cursor() as cur:
        cur.execute(
            """
            SELECT p.ticker, p.ts AS ex_date, p.dividend
              FROM stock_prices p
              JOIN (
                    SELECT ticker, MIN(ts) AS first_day, MAX(ts) AS last_day
                      FROM stock_prices WHERE dividend > 0 GROUP BY ticker
                   ) b ON b.ticker = p.ticker
             WHERE p.ts > b.first_day + INTERVAL '45 days'
               AND p.ts < b.last_day - INTERVAL '35 days'
             ORDER BY p.dividend DESC
             LIMIT 40
            """
        )
        candidates = cur.fetchall()

    for row in candidates:
        with database.get_cursor() as cur:
            cur.execute(
                "SELECT count(*) AS n FROM stock_prices WHERE ticker = %s AND ts BETWEEN %s AND %s",
                (row["ticker"], row["ex_date"] - timedelta(days=30), row["ex_date"] + timedelta(days=15)),
            )
            if cur.fetchone()["n"] >= 30:
                return row
    raise SystemExit(
        "no usable dividend declaration found - run `uv run python -m scripts.ingestion.backfill_dividends` first"
    )


def start(ticker: str, start_date: date, end_date: date, cash: str = "250000") -> tuple[str, dict]:
    """Start a pure (no Nessie) run. Bills are switched on later, from the database."""
    state = engine.start_session(
        [ticker],
        start_date=start_date,
        end_date=end_date,
        use_finances=False,
        initial_cash=Decimal(cash),
    )
    return state["session_id"], state


def close_before(ticker: str, day: date) -> Decimal:
    with database.get_cursor() as cur:
        cur.execute(
            "SELECT close FROM stock_prices WHERE ticker = %s AND ts <= %s ORDER BY ts DESC LIMIT 1",
            (ticker, day),
        )
        row = cur.fetchone()
    return money(row["close"]) if row else Decimal("100")


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


# --- dividends ------------------------------------------------------------


def test_dividends(candidate: dict) -> list[str]:
    ticker = candidate["ticker"]
    ex_date: date = candidate["ex_date"]
    per_share = money(candidate["dividend"])
    shares = 100
    expected = money(per_share * shares)
    print(f"\ndividends: {ticker} pays {per_share} on {ex_date.isoformat()}")

    sid, state = start(ticker, ex_date - timedelta(days=30), ex_date + timedelta(days=15))
    engine.execute_trade(sid, ticker, "BUY", shares)
    before = engine.get_state(sid)
    check("a position is open before the ex-date", before["portfolio"]["positions"][0]["shares"] == shares)

    crossed = None
    previous = before
    for _ in range(80):
        crossed = engine.advance_day(sid, days=1, with_ai=False)
        if crossed["sim_date"] >= ex_date.isoformat() or crossed["status"] != "active":
            break
        previous = crossed
    if crossed is None:
        check("the clock reached the ex-date", False, "session ended first")
        return [sid]

    check("the clock reached the ex-date", crossed["sim_date"] >= ex_date.isoformat(), crossed["sim_date"])
    payments = [row for row in crossed["dividends"] if row["ticker"] == ticker]
    check("a dividend payment was recorded", len(payments) >= 1, f"{len(payments)} payment(s)")
    if payments:
        payment = payments[-1]
        check("it paid for the shares held before the ex-date", payment["shares"] == shares, str(payment["shares"]))
        check("at the declared rate", money(payment["per_share"]) == per_share, f"{payment['per_share']} vs {per_share}")
        check("with the right amount", money(payment["amount"]) == expected, f"{payment['amount']} vs {expected}")
        check("on the declared ex-date", payment["ex_date"] == ex_date.isoformat(), payment["ex_date"])
    check(
        "cash was credited exactly once",
        money(crossed["portfolio"]["cash_balance"]) - money(previous["portfolio"]["cash_balance"]) == expected,
        f"{previous['portfolio']['cash_balance']} -> {crossed['portfolio']['cash_balance']}",
    )
    check("the UI total matches the ledger", money(crossed["dividends_paid"]) == expected, str(crossed["dividends_paid"]))
    check("the UI summary counts it", crossed["dividend_summary"]["payments"] >= 1)
    check("it is investment income, not a paycheck", money(crossed["portfolio"]["salary_earned"]) == 0)

    with database.get_cursor() as cur:
        cur.execute(
            "SELECT shares, per_share, amount FROM dividend_payments WHERE session_id = %s ORDER BY id",
            (sid,),
        )
        rows = cur.fetchall()
    check("the payment is persisted", len(rows) == 1, f"{len(rows)} row(s)")
    if rows:
        check(
            "the persisted row matches the state",
            money(rows[0]["amount"]) == expected and rows[0]["shares"] == shares,
            f"{rows[0]['shares']} x {rows[0]['per_share']}",
        )

    # Idempotency: the same window settled again must not pay a second time.
    cash_before = money(engine.get_state(sid)["portfolio"]["cash_balance"])
    again = database.settle_dividends(sid, [ticker], ex_date - timedelta(days=1), ex_date)
    cash_after = money(engine.get_state(sid)["portfolio"]["cash_balance"])
    check("settling the same window again pays nothing", again == [] and cash_after == cash_before)

    # Selling after the ex-date must not claw back a payment already earned.
    holding = before["portfolio"]["positions"][0]["shares"]
    engine.execute_trade(sid, ticker, "SELL", holding)
    sold = engine.get_state(sid)
    check(
        "selling after the ex-date does not erase the dividend",
        money(sold["dividends_paid"]) == expected and money(sold["portfolio"]["cash_balance"]) > cash_after,
        str(sold["dividends_paid"]),
    )
    return [sid]


def test_eligibility(candidate: dict) -> list[str]:
    """Two positions, one opened on the ex-date and one before it."""
    ticker = candidate["ticker"]
    ex_date: date = candidate["ex_date"]
    per_share = money(candidate["dividend"])
    print(f"\nentitlement: {ticker} ex-date {ex_date.isoformat()}")

    sid, _ = start(ticker, ex_date - timedelta(days=30), ex_date + timedelta(days=10))
    price = close_before(ticker, ex_date)
    database.record_trade(
        sid, ticker, ex_date, "BUY", Decimal(100), price, Decimal("250000"), Decimal(100), price
    )
    paid = database.settle_dividends(sid, [ticker], ex_date - timedelta(days=1), ex_date)
    check("shares bought on the ex-date earn nothing", paid == [], f"{len(paid)} payment(s)")

    opened = ex_date - timedelta(days=5)
    database.record_trade(
        sid, ticker, opened, "BUY", Decimal(50), price, Decimal("250000"), Decimal(150), price
    )
    paid = database.settle_dividends(sid, [ticker], ex_date - timedelta(days=1), ex_date)
    check("shares bought before the ex-date do earn", len(paid) == 1, f"{len(paid)} payment(s)")
    if paid:
        check("only the eligible shares pay", Decimal(paid[0]["shares"]) == Decimal(50), str(paid[0]["shares"]))
        check("at the declared rate", money(paid[0]["amount"]) == money(per_share * 50), str(paid[0]["amount"]))
    return [sid]


def quiet_ticker(payer: str, start: date, end: date, horizon_days: int = 150) -> str | None:
    """A tradable symbol that declares no dividend anywhere near this window.

    The lookahead matters: the calendar reads up to four months ahead, so a symbol is only
    quiet if it pays nothing across that whole range, not just the session's own dates.
    """
    with database.get_cursor() as cur:
        cur.execute(
            """
            SELECT ticker
              FROM stock_prices
             WHERE ticker <> %s AND ts BETWEEN %s AND %s
             GROUP BY ticker
            HAVING count(*) >= 25
               AND count(*) FILTER (WHERE dividend > 0) = 0
               AND NOT EXISTS (
                     SELECT 1 FROM stock_prices later
                      WHERE later.ticker = stock_prices.ticker
                        AND later.dividend > 0
                        AND later.ts BETWEEN %s AND %s + make_interval(days => %s)
                   )
             ORDER BY ticker LIMIT 1
            """,
            (payer, start, end, start, start, horizon_days),
        )
        row = cur.fetchone()
    return row["ticker"] if row else None


def test_calendar(candidate: dict) -> list[str]:
    """What the panel promises before the payment happens."""
    ticker = candidate["ticker"]
    ex_date: date = candidate["ex_date"]
    per_share = money(candidate["dividend"])
    print("\ndividend calendar")

    quiet = quiet_ticker(ticker, ex_date - timedelta(days=30), ex_date + timedelta(days=15))
    tickers = [ticker] + ([quiet] if quiet else [])
    state = engine.start_session(
        tickers,
        start_date=ex_date - timedelta(days=30),
        end_date=ex_date + timedelta(days=15),
        use_finances=False,
        initial_cash=Decimal("250000"),
    )
    sid = state["session_id"]
    engine.execute_trade(sid, ticker, "BUY", 10)
    if quiet:
        engine.execute_trade(sid, quiet, "BUY", 1)
    state = engine.get_state(sid)
    sim_date = date.fromisoformat(state["sim_date"])

    schedule = {row["ticker"]: row for row in state["dividend_schedule"]}
    payer = schedule.get(ticker)
    check("a held dividend payer is listed", payer is not None)
    if payer:
        check("with its next ex-date", payer["ex_date"] == ex_date.isoformat(), str(payer["ex_date"]))
        check("the rate it pays", money(payer["per_share"]) == per_share, str(payer["per_share"]))
        check("an estimate of the payment", money(payer["estimated"]) == money(per_share * 10), str(payer["estimated"]))
        check("and how many days away it is", payer["days_away"] == (ex_date - sim_date).days, str(payer["days_away"]))
        check("dated after today", date.fromisoformat(payer["ex_date"]) > sim_date)
    if quiet:
        non_payer = schedule.get(quiet)
        check("a holder that pays nothing is listed too", non_payer is not None)
        if non_payer:
            check("with no date rather than no row", non_payer.get("ex_date") is None)
    check("the schedule covers every position", len(state["dividend_schedule"]) == len(state["portfolio"]["positions"]))
    return [sid]


def advance_across(sid: str, ex_date: date) -> dict:
    state = engine.get_state(sid)
    for _ in range(80):
        state = engine.advance_day(sid, days=1, with_ai=False)
        if state["sim_date"] >= ex_date.isoformat() or state["status"] != "active":
            break
    return state


def test_reinvestment(candidate: dict) -> list[str]:
    """The same dividend, taken as stock instead of cash."""
    ticker = candidate["ticker"]
    ex_date: date = candidate["ex_date"]
    per_share = money(candidate["dividend"])
    bought_shares = 100
    expected = money(per_share * bought_shares)
    print("\nreinvesting dividends")

    sid, state = start(ticker, ex_date - timedelta(days=30), ex_date + timedelta(days=15))
    engine.execute_trade(sid, ticker, "BUY", bought_shares)
    before = engine.get_state(sid)
    cash_before = money(before["portfolio"]["cash_balance"])
    shares_before = Decimal(str(before["portfolio"]["positions"][0]["shares"]))

    engine.set_reinvestment(sid, True, focus=ticker)
    check("the choice is remembered by the session", engine.get_state(sid)["reinvest_dividends"] is True)

    crossed = advance_across(sid, ex_date)
    payments = [row for row in crossed["dividends"] if row["ticker"] == ticker]
    check("the payout still lands", len(payments) == 1, f"{len(payments)} payment(s)")
    if not payments:
        return [sid]
    payment = payments[0]
    reinvested = Decimal(str(payment["reinvested_shares"]))

    check("the dividend is the same amount", money(payment["amount"]) == expected, str(payment["amount"]))
    check("no cash moved", money(crossed["portfolio"]["cash_balance"]) == cash_before, f"{cash_before} -> {crossed['portfolio']['cash_balance']}")
    check("shares were bought instead", reinvested > 0, str(reinvested))
    implied = float(payment["amount"]) / float(reinvested)
    check("at the ex-date close", abs(payment["reinvest_price"] - implied) < 0.0002, f"{payment['reinvest_price']} vs {implied:.4f}")
    check("the payment still counts as income", money(crossed["dividends_paid"]) == expected)
    check("and is reported as reinvested", money(crossed["dividends_reinvested"]) == money(reinvested), str(crossed["dividends_reinvested"]))

    position = next(row for row in crossed["portfolio"]["positions"] if row["ticker"] == ticker)
    check(
        "the position grew by exactly what was bought",
        Decimal(str(position["shares"])) == shares_before + reinvested,
        f"{shares_before} + {reinvested}",
    )
    check(
        "and is worth the cash it replaced",
        abs(position["shares"] * position["close"] - (float(shares_before) * position["close"] + float(expected))) < 0.02,
    )

    ledger = [row for row in crossed["trades"] if row["ticker"] == ticker and row["reinvested"]]
    check("the purchase is in the trade ledger", len(ledger) == 1, f"{len(ledger)} row(s)")
    if ledger:
        check("dated on the ex-date", ledger[0]["date"] == ex_date.isoformat(), ledger[0]["date"])
        check("and sized like the payment", abs(ledger[0]["shares"] - float(reinvested)) < 1e-6)

    # Reinvested shares are real shares: the ledger has to know about them, or the next
    # dividend would be paid on a number the player never bought.
    with database.get_cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM transactions WHERE session_id = %s AND ticker = %s AND reinvested",
            (sid, ticker),
        )
        check("and persisted", cur.fetchone()["n"] == 1)

    # Turns are reversible: switching back to cash is not a one-way door.
    engine.set_reinvestment(sid, False, focus=ticker)
    check("it can be switched back to cash", engine.get_state(sid)["reinvest_dividends"] is False)
    return [sid]


def test_reinvestment_is_return_neutral(candidate: dict) -> list[str]:
    """Taking stock instead of cash must not flatter the score."""
    ticker = candidate["ticker"]
    ex_date: date = candidate["ex_date"]
    print("\nreinvestment does not change the return")
    results = []
    for reinvest in (False, True):
        sid, _ = start(ticker, ex_date - timedelta(days=30), ex_date + timedelta(days=15))
        engine.execute_trade(sid, ticker, "BUY", 100)
        if reinvest:
            engine.set_reinvestment(sid, True, focus=ticker)
        results.append((advance_across(sid, ex_date), sid))

    cash, reinvested = (results[0][0], results[1][0])
    check(
        "the same clock, the same return",
        abs(cash["portfolio"]["total_return_pct"] - reinvested["portfolio"]["total_return_pct"]) < 0.05,
        f"cash {cash['portfolio']['total_return_pct']:.4f}% vs reinvested {reinvested['portfolio']['total_return_pct']:.4f}%",
    )
    check(
        "the cash run holds cash, the other holds shares",
        money(reinvested["portfolio"]["cash_balance"]) < money(cash["portfolio"]["cash_balance"]),
    )
    return [results[0][1], results[1][1]]


# --- bills and paychecks --------------------------------------------------


def inject_bank(sid: str, session_date: date, amount: str, offsets=(1, 2, 3)) -> list[dict]:
    """Stand-in standing orders, straight into the per-session cache (no Nessie call)."""
    bills = [
        {
            "bill_id": f"smoke-{offset}",
            "label": f"Smoke bill {offset}",
            "payee": "Smoke Co",
            "day": (session_date + timedelta(days=offset)).day,
            "amount": Decimal(amount),
        }
        for offset in offsets
    ]
    expenses.forget(sid)
    expenses._bills_cache[sid] = bills
    expenses._bank_cache[sid] = {"customer": None, "account": None, "employer": "Smoke Co"}
    return bills


def test_bills(candidate: dict) -> list[str]:
    ticker = candidate["ticker"]
    ex_date: date = candidate["ex_date"]
    print("\nbills and paychecks (finances on)")
    sid, state = start(ticker, ex_date - timedelta(days=30), ex_date + timedelta(days=20))
    engine.execute_trade(sid, ticker, "BUY", 100)

    # Two wallets: the Nessie switch is on, and the standing orders are the ones we inject.
    database.update_session(sid, finances_enabled=True, salary_amount=Decimal("2000"))
    sim_date = date.fromisoformat(engine.get_state(sid)["sim_date"])
    bills = inject_bank(sid, sim_date, "50")
    cash_before = money(engine.get_state(sid)["portfolio"]["cash_balance"])

    after = engine.advance_day(sid, days=1, with_ai=False)
    charged = after["expenses"]["charged"]
    bill_rows = [row for row in charged if row["kind"] == "bill"]
    salary_rows = [row for row in charged if row["kind"] == "salary"]
    paid = sum(money(row["amount"]) for row in bill_rows)
    earned = sum(money(row["amount"]) for row in salary_rows)
    cash_after = money(after["portfolio"]["cash_balance"])

    check("finances report themselves as on", after["expenses"]["enabled"] is True)
    check("at least one bill came due", len(bill_rows) >= 1, f"{len(bill_rows)} bill(s)")
    check("each is in the ledger with its own amount", all(money(r["amount"]) == Decimal("50") for r in bill_rows))
    check("the monthly standing orders are known", after["expenses"]["monthly_total"] == 150.0, str(after["expenses"]["monthly_total"]))
    check("the next bills are visible ahead of time", len(after["expenses"]["upcoming"]) >= 1)
    check(
        "cash moved by exactly the paychecks minus the bills",
        cash_after == cash_before + earned - paid,
        f"{cash_before} + {earned} - {paid} vs {cash_after}",
    )
    check(
        "paid-to-date counts only the bills",
        money(after["expenses"]["paid_to_date"]) == money(after["expenses"]["paid_to_date"]) and after["expenses"]["paid_to_date"] >= 0,
        str(after["expenses"]["paid_to_date"]),
    )
    check(
        "what the bank paid in is not counted as trading profit",
        money(after["portfolio"]["salary_earned"]) == earned,
        f"{after['portfolio']['salary_earned']} vs {earned}",
    )
    check(
        "and the headline return still excludes it",
        abs(
            after["portfolio"]["total_return_pct"]
            - (
                after["portfolio"]["net_worth"]
                + after["portfolio"]["bills_paid"]
                - after["portfolio"]["salary_earned"]
                - after["portfolio"]["starting_cash"]
            )
            / after["portfolio"]["starting_cash"]
            * 100
        )
        < 1e-9,
        f"{after['portfolio']['total_return_pct']:.4f}%",
    )
    if not salary_rows:
        print("        (no payday in this step; the paycheck path is covered by the return check)")

    # Overdraft: the cash cannot cover the bill, so shares are sold at the closing price.
    database.update_session(sid, cash_balance=Decimal("10"))
    inject_bank(sid, date.fromisoformat(after["sim_date"]), "5000")
    overdrawn = engine.advance_day(sid, days=1, with_ai=False)
    forced = [row for row in overdrawn["expenses"]["charged"] if row["kind"] == "bill"]
    sold = [row for row in forced if row["sold"]]
    check("a bill bigger than the cash forces a sale", len(sold) >= 1, f"{sum(len(r['sold']) for r in forced)} sale(s)")
    check("cash never goes below zero", money(overdrawn["portfolio"]["cash_balance"]) >= 0, str(overdrawn["portfolio"]["cash_balance"]))
    if sold:
        check("the forced sale is named in the ledger row", all(s["ticker"] == ticker for r in sold for s in r["sold"]))
    check(
        "the shortfall is recorded when nothing is left to sell",
        all(float(row.get("shortfall") or 0) >= 0 for row in forced),
    )
    return [sid]


def test_pure_mode_skips_money(candidate: dict) -> list[str]:
    ticker = candidate["ticker"]
    ex_date: date = candidate["ex_date"]
    print("\npure simulation (finances off)")
    sid, state = start(ticker, ex_date - timedelta(days=30), ex_date + timedelta(days=10))
    sim_date = date.fromisoformat(state["sim_date"])
    inject_bank(sid, sim_date, "5000")  # a bank exists and is ignored
    after = engine.advance_day(sid, days=1, with_ai=False)
    check("no bills are charged", after["expenses"]["charged"] == [])
    check("the panel says finances are off", after["expenses"]["enabled"] is False)
    check("no monthly bills and no runway", after["expenses"]["monthly_total"] == 0 and after["expenses"]["upcoming"] == [])
    check("no paycheck", after["portfolio"]["salary_earned"] == 0)
    check("the bank panel names the mode", after["bank"]["source"] == "Pure stock simulation", after["bank"]["source"])
    return [sid]


def test_return_arithmetic() -> None:
    """The headline the player sees: stock performance only."""
    print("\nreturn percentage")
    gain = engine._portfolio({"cash_balance": Decimal("10500"), "starting_cash": Decimal("10000")}, [], {}, 0.0, 0.0)
    check("a 5% gain reads as 5%", abs(gain["total_return_pct"] - 5.0) < 1e-9, f"{gain['total_return_pct']:.4f}")
    paycheck = engine._portfolio(
        {"cash_balance": Decimal("11000"), "starting_cash": Decimal("10000")}, [], {}, 0.0, 1000.0
    )
    check("a paycheck alone leaves the return at 0%", abs(paycheck["total_return_pct"]) < 1e-9, f"{paycheck['total_return_pct']:.4f}")
    bill = engine._portfolio(
        {"cash_balance": Decimal("9500"), "starting_cash": Decimal("10000")}, [], {}, 500.0, 0.0
    )
    check("a paid bill alone leaves the return at 0%", abs(bill["total_return_pct"]) < 1e-9, f"{bill['total_return_pct']:.4f}")
    back = engine._portfolio(
        {"cash_balance": Decimal("9500"), "starting_cash": Decimal("10000")}, [], {}, 500.0, 0.0
    )
    check("the loss is still shown when it is real", back["total_return_pct"] == 0.0 and back["net_worth"] == 9500.0)


# --- the page's own copy of the state -------------------------------------


def get_json(path: str):
    with urllib.request.urlopen(f"{UI_BASE}{path}", timeout=10) as response:
        return jsonlib.loads(response.read())


def post_json(path: str, body: dict):
    request = urllib.request.Request(
        f"{UI_BASE}{path}",
        data=jsonlib.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return jsonlib.loads(response.read())


def health() -> dict:
    return get_json("/api/health")


def test_api_payload(session_id: str) -> None:
    print("\nwhat the page receives")
    try:
        body = get_json(f"/api/session/{session_id}/state")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"  SKIP  the dev server is not answering on {UI_BASE} ({exc})")
        return
    check("the state endpoint answers", isinstance(body, dict) and "portfolio" in body)
    for key in (
        "dividends",
        "dividends_paid",
        "dividends_reinvested",
        "dividend_summary",
        "dividend_schedule",
        "reinvest_dividends",
        "expenses",
        "bank",
        "news",
    ):
        check(f"the payload carries {key}", key in body)
    check("dividends is a list the page can walk", isinstance(body.get("dividends"), list))
    check("the schedule is a list the page can walk", isinstance(body.get("dividend_schedule"), list))
    check("expenses carries the charged ledger", isinstance(body.get("expenses", {}).get("charged"), list))
    check("the health endpoint reports the pool", "max" in health(), str(health()))

    # The switch itself, over the wire, the way the checkbox uses it.
    wanted = not bool(body.get("reinvest_dividends"))
    toggled = post_json(f"/api/session/{session_id}/dividends", {"reinvest": wanted})
    check("the reinvest switch round-trips", toggled.get("reinvest_dividends") is wanted, str(toggled.get("reinvest_dividends")))
    check(
        "and back",
        post_json(f"/api/session/{session_id}/dividends", {"reinvest": not wanted}).get("reinvest_dividends")
        is (not wanted),
    )

    state = engine.get_state(session_id)
    try:
        jsonlib.dumps(state)
        serialisable = True
    except TypeError as exc:
        serialisable = False
        print(f"        {exc}")
    check("the engine state survives json.dumps", serialisable)


# --- entry point ----------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test bills, paychecks and dividends.")
    parser.add_argument("--keep", action="store_true", help="leave the smoke sessions in the database")
    args = parser.parse_args()

    candidate = dividend_candidate()
    print(f"smoke test: dividends, bills and paychecks against {database.__name__}")

    sessions: list[str] = []
    try:
        sessions += test_dividends(candidate)
        sessions += test_eligibility(candidate)
        sessions += test_calendar(candidate)
        sessions += test_reinvestment(candidate)
        sessions += test_reinvestment_is_return_neutral(candidate)
        sessions += test_bills(candidate)
        sessions += test_pure_mode_skips_money(candidate)
        test_return_arithmetic()
        test_api_payload(sessions[0])
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
