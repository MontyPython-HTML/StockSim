"""Real life, billed against the money you were going to invest.

The starting cash comes out of a Nessie account, and so do that account's standing orders:
rent, a car payment, insurance, the streaming bundle nobody cancels. As the simulated clock
passes each bill's day of the month it is taken out of the same cash balance the player is
trying to buy stock with, and a paycheck from the account's payroll deposits comes in every
two weeks.

Cash never goes below zero. When a bill comes due and the cash cannot cover it, the bank
sells shares at that day's close to pay it - at whatever price the market happens to be
offering, which is the lesson a chart cannot teach on its own. With nothing left to sell,
the bill takes what cash there is and the rest is recorded as missed.

Bank details are read from Nessie once per session and cached: they are configuration, not
market data, and a session that has already started should not silently change shape
because somebody edited the sandbox mid-run.
"""

import calendar
import logging
import threading
import time
from datetime import date, timedelta
from decimal import ROUND_CEILING, Decimal

import config
from scripts.api import nessie
from scripts.database import database

log = logging.getLogger(__name__)

PAY_INTERVAL_DAYS = 14
PAYDAY_WEEKDAY = 4
SALARY_BILL_ID = "salary"
PROFILE_LIMIT = 6
PROFILE_TTL_SECONDS = 300
CENT = Decimal("0.01")

_bills_cache: dict[str, list[dict]] = {}
_bank_cache: dict[str, dict] = {}
_profiles: dict = {"at": 0.0, "rows": None}
_profiles_lock = threading.Lock()


def _as_date(value) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))



def normalise_bills(raw_bills: list[dict]) -> list[dict]:
    bills: list[dict] = []
    for raw in raw_bills:
        day = raw.get("recurring_date")
        amount = raw.get("payment_amount")
        if not day or amount in (None, ""):
            continue
        bills.append(
            {
                "bill_id": str(raw.get("_id") or raw.get("nickname")),
                "label": raw.get("nickname") or raw.get("payee") or "Bill",
                "payee": raw.get("payee"),
                "day": max(1, min(28, int(day))),
                "amount": Decimal(str(amount)),
            }
        )
    bills.sort(key=lambda bill: (bill["day"], bill["label"]))
    return bills


def paycheck_from_deposits(deposits: list[dict]) -> dict | None:
    """The latest "Payroll - <employer>" deposit: its amount is the paycheck."""
    payroll = [
        deposit
        for deposit in deposits
        if "payroll" in str(deposit.get("description", "")).lower() and deposit.get("amount")
    ]
    if not payroll:
        return None
    latest = max(payroll, key=lambda deposit: str(deposit.get("transaction_date", "")))
    _, _, employer = str(latest["description"]).partition("-")
    return {"amount": float(latest["amount"]), "employer": employer.strip() or None}


def default_paycheck(account_id: str) -> Decimal:
    try:
        paycheck = paycheck_from_deposits(nessie.get_account_deposits(account_id))
    except Exception as exc:  # noqa: BLE001
        log.warning("Nessie deposits unavailable for %s: %s", account_id, exc)
        return Decimal("0")
    return Decimal(str(paycheck["amount"])).quantize(CENT) if paycheck else Decimal("0")


def _describe_customer(customer: dict) -> dict:
    address = customer.get("address") or {}
    name = f"{customer.get('first_name', '')} {customer.get('last_name', '')}".strip()
    return {
        "id": customer.get("_id"),
        "name": name or None,
        "city": address.get("city"),
        "state": address.get("state"),
    }


def _describe_account(account: dict) -> dict:
    number = str(account.get("account_number") or "")
    return {
        "id": account.get("_id"),
        "nickname": account.get("nickname"),
        "type": account.get("type"),
        "number": f"••••{number[-4:]}" if number else None,
        "balance": float(account.get("balance") or 0),
    }


def bank_profiles() -> list[dict]:
    """Nessie customers a run can start as, default first, with balance, bills and paycheck."""
    with _profiles_lock:
        if _profiles["rows"] is not None and time.monotonic() - _profiles["at"] < PROFILE_TTL_SECONDS:
            return _profiles["rows"]

    customers = sorted(
        nessie.get_customers(),
        key=lambda customer: customer.get("_id") != config.NESSIE_DEFAULT_CUSTOMER_ID,
    )
    rows = []
    for customer in customers[:PROFILE_LIMIT]:
        try:
            account = nessie.pick_funding_account(customer["_id"])
            bills = normalise_bills(nessie.get_account_bills(account["_id"]))
            paycheck = paycheck_from_deposits(nessie.get_account_deposits(account["_id"]))
        except Exception as exc:  # noqa: BLE001
            log.warning("skipping Nessie customer %s: %s", customer.get("_id"), exc)
            continue
        rows.append(
            {
                **_describe_customer(customer),
                "account": _describe_account(account),
                "bills": {
                    "count": len(bills),
                    "monthly_total": float(sum(bill["amount"] for bill in bills)),
                },
                "paycheck": paycheck,
            }
        )

    with _profiles_lock:
        _profiles.update(at=time.monotonic(), rows=rows)
    return rows


def bank_for(session: dict) -> dict:
    """The Nessie customer behind a session, their funding account and their employer."""
    if not session.get("finances_enabled", True):
        return {"customer": None, "account": None, "employer": None}
    session_id = str(session["id"])
    if session_id in _bank_cache:
        return _bank_cache[session_id]

    customer_id = session.get("nessie_customer_id")
    account_id = session.get("nessie_account_id")
    bank = {"customer": None, "account": None, "employer": None}
    try:
        if customer_id:
            customer = nessie.get_customer(customer_id)
            bank["customer"] = _describe_customer(customer) if customer else None
            account = next(
                (row for row in nessie.get_customer_accounts(customer_id) if row.get("_id") == account_id),
                None,
            )
            bank["account"] = _describe_account(account) if account else None
        if account_id:
            paycheck = paycheck_from_deposits(nessie.get_account_deposits(account_id))
            bank["employer"] = paycheck["employer"] if paycheck else None
    except Exception as exc:  # noqa: BLE001
        log.warning("Nessie profile unavailable for %s: %s", customer_id, exc)
    _bank_cache[session_id] = bank
    return bank


def bills_for(session: dict) -> list[dict]:
    """The account's standing orders, normalised and cached per session."""
    if not session.get("finances_enabled", True):
        return []
    session_id = str(session["id"])
    if session_id in _bills_cache:
        return _bills_cache[session_id]

    account_id = session.get("nessie_account_id")
    bills: list[dict] = []
    if account_id:
        try:
            bills = normalise_bills(nessie.get_account_bills(account_id))
        except Exception as exc:  # noqa: BLE001
            log.warning("Nessie bills unavailable for %s: %s", account_id, exc)
    _bills_cache[session_id] = bills
    return bills


def forget(session_id: str) -> None:
    _bills_cache.pop(str(session_id), None)
    _bank_cache.pop(str(session_id), None)



def _due_dates(day: int, after: date, through: date) -> list[date]:
    """Every occurrence of a day-of-month strictly after `after`, up to `through`.

    Clamped to the length of each month so a 31st lands on the 28th in February rather
    than being skipped for four months of the year.
    """
    if through <= after:
        return []
    hits: list[date] = []
    cursor = date(after.year, after.month, 1)
    while cursor <= through:
        last = calendar.monthrange(cursor.year, cursor.month)[1]
        due = date(cursor.year, cursor.month, min(day, last))
        if after < due <= through:
            hits.append(due)
        cursor = date(cursor.year + (cursor.month == 12), (cursor.month % 12) + 1, 1)
    return hits


def _paydays(start: date, after: date, through: date) -> list[date]:
    """Every other Friday from the first one after the run starts, in (after, through]."""
    payday = start + timedelta(days=(PAYDAY_WEEKDAY - start.weekday()) % 7 or 7)
    if after >= payday:
        periods = (after - payday).days // PAY_INTERVAL_DAYS + 1
        payday += timedelta(days=periods * PAY_INTERVAL_DAYS)
    hits: list[date] = []
    while payday <= through:
        hits.append(payday)
        payday += timedelta(days=PAY_INTERVAL_DAYS)
    return hits


def due_between(session: dict, after: date, through: date) -> list[dict]:
    """Every bill and paycheck in (after, through], oldest first, paycheck first on a shared day."""
    flows: list[dict] = []
    for bill in bills_for(session):
        for due in _due_dates(bill["day"], after, through):
            flows.append({**bill, "kind": "bill", "due_date": due})

    salary = Decimal(str(session.get("salary_amount") or 0))
    if salary > 0:
        employer = bank_for(session)["employer"]
        for payday in _paydays(_as_date(session["start_date"]), after, through):
            flows.append(
                {
                    "bill_id": SALARY_BILL_ID,
                    "kind": "salary",
                    "label": "Paycheck",
                    "payee": employer,
                    "amount": salary,
                    "due_date": payday,
                }
            )

    flows.sort(key=lambda flow: (flow["due_date"], flow["kind"] != "salary", flow["label"]))
    return flows



def plan_sales(
    holdings: dict[str, Decimal], prices: dict[str, Decimal], needed: Decimal
) -> list[tuple[str, Decimal]]:
    """Whole shares from the largest position down, just enough to raise `needed`."""
    ranked = sorted(
        (ticker for ticker in holdings if prices.get(ticker)),
        key=lambda ticker: holdings[ticker] * prices[ticker],
        reverse=True,
    )
    sales: list[tuple[str, Decimal]] = []
    for ticker in ranked:
        if needed <= 0:
            break
        price = prices[ticker]
        shares = min(holdings[ticker], (needed / price).to_integral_value(rounding=ROUND_CEILING))
        if shares <= 0:
            continue
        sales.append((ticker, shares))
        needed -= (shares * price).quantize(CENT)
    return sales


def apply_due(session: dict, previous_date: date, sim_date: date, watchlist: list[str]) -> list[dict]:
    """Settle the bills and paychecks the clock just crossed. Returns what actually happened."""
    flows = due_between(session, previous_date, sim_date)
    if not flows:
        return []

    session_id = str(session["id"])
    quote_cache: dict[date, dict[str, dict]] = {}

    def prices_on(day: date) -> dict[str, dict]:
        if day not in quote_cache:
            quote_cache[day] = {
                ticker: {"price": Decimal(str(quote["close"])), "date": date.fromisoformat(quote["date"])}
                for ticker, quote in database.latest_quotes(session_id, watchlist, day).items()
                if quote.get("close")
            }
        return quote_cache[day]

    applied, cash = database.settle_cash_flows(session_id, flows, prices_on, plan_sales)
    session["cash_balance"] = cash
    return [ledger_row(row) for row in applied]


def ledger_row(row: dict) -> dict:
    return {
        "kind": row.get("kind") or "bill",
        "label": row["label"],
        "payee": row.get("payee"),
        "due_date": _as_date(row["due_date"]).isoformat(),
        "amount": float(row["amount"]),
        "shortfall": float(row.get("shortfall") or 0),
        "cash_after": float(row["cash_after"]),
        "sold": row.get("sold") or [],
    }


def upcoming(session: dict, sim_date: date, within_days: int = 45, limit: int = 6) -> list[dict]:
    """The next few bills and paychecks, so the player can see the hit coming before it lands."""
    horizon = sim_date + timedelta(days=within_days)
    return [
        {
            "kind": flow["kind"],
            "label": flow["label"],
            "payee": flow.get("payee"),
            "due_date": flow["due_date"].isoformat(),
            "amount": float(flow["amount"]),
            "days_away": (flow["due_date"] - sim_date).days,
        }
        for flow in due_between(session, sim_date - timedelta(days=1), horizon)
    ][:limit]


def summary(session: dict, sim_date: date, ledger: list[dict]) -> dict:
    bills = bills_for(session)
    monthly_bills = float(sum(bill["amount"] for bill in bills))
    paycheck = float(session.get("salary_amount") or 0)
    monthly_income = round(paycheck * 26 / 12, 2)
    net_burn = monthly_bills - monthly_income
    cash = float(session["cash_balance"])
    bill_rows = [row for row in ledger if (row.get("kind") or "bill") == "bill"]
    finances_enabled = bool(session.get("finances_enabled", True))
    return {
        "enabled": finances_enabled,
        "monthly_total": monthly_bills if finances_enabled else 0,
        "bill_count": len(bills) if finances_enabled else 0,
        "paid_to_date": sum(float(row["amount"]) for row in bill_rows) if finances_enabled else 0,
        "missed_to_date": sum(float(row.get("shortfall") or 0) for row in bill_rows) if finances_enabled else 0,
        "earned_to_date": sum(float(row["amount"]) for row in ledger if row.get("kind") == "salary") if finances_enabled else 0,
        "paycheck": {
            "amount": paycheck,
            "every_days": PAY_INTERVAL_DAYS,
            "monthly": monthly_income,
            "employer": bank_for(session)["employer"],
        }
        if finances_enabled and paycheck > 0
        else None,
        "net_monthly": round(monthly_income - monthly_bills, 2) if finances_enabled else 0,
        "upcoming": upcoming(session, sim_date) if finances_enabled else [],
        "overdrawn": cash < 0,
        "months_of_runway": round(cash / net_burn, 1) if finances_enabled and net_burn > 0 else None,
    }
