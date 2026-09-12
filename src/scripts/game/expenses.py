"""Real life, billed against the money you were going to invest.

The starting cash comes out of a Nessie account, and so do that account's standing orders:
rent, a car payment, insurance, the streaming bundle nobody cancels. As the simulated clock
passes each bill's day of the month it is taken out of the same cash balance the player is
trying to buy stock with.

That is the lesson the rest of the game cannot teach on its own. A chart rewards being
fully invested; a bank account does not. Put every dollar into equities on day one and the
first of the month arrives anyway - the balance goes red, and nothing can be bought until
something is sold, usually at whatever price the market happens to be offering that day.

Bills are read from Nessie once per session and cached: they are configuration, not market
data, and a session that has already started should not silently change shape because
somebody edited the sandbox mid-run.
"""

import calendar
import logging
from datetime import date, timedelta
from decimal import Decimal

from scripts.api import nessie
from scripts.database import database

log = logging.getLogger(__name__)

# A session with no reachable bank is still playable; it just has no bills.
_bills_cache: dict[str, list[dict]] = {}


def _as_date(value) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def bills_for(session: dict) -> list[dict]:
    """The account's standing orders, normalised and cached per session."""
    session_id = str(session["id"])
    if session_id in _bills_cache:
        return _bills_cache[session_id]

    account_id = session.get("nessie_account_id")
    bills: list[dict] = []
    if account_id:
        try:
            for raw in nessie.get_account_bills(account_id):
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
        except Exception as exc:  # noqa: BLE001 - a bank outage must not stop the clock
            log.warning("Nessie bills unavailable for %s: %s", account_id, exc)

    bills.sort(key=lambda bill: (bill["day"], bill["label"]))
    _bills_cache[session_id] = bills
    return bills


def forget(session_id: str) -> None:
    _bills_cache.pop(str(session_id), None)


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


def due_between(session: dict, after: date, through: date) -> list[dict]:
    """Every bill occurrence in (after, through], oldest first."""
    charges: list[dict] = []
    for bill in bills_for(session):
        for due in _due_dates(bill["day"], after, through):
            charges.append({**bill, "due_date": due})
    charges.sort(key=lambda charge: (charge["due_date"], charge["label"]))
    return charges


def apply_due(session: dict, previous_date: date, sim_date: date) -> list[dict]:
    """Charge whatever came due while the clock moved. Returns what was actually taken.

    Cash is allowed to go negative on purpose: being overdrawn is the outcome the lesson
    is about, and refusing the charge would quietly teach that bills wait for you.
    """
    charges = due_between(session, previous_date, sim_date)
    if not charges:
        return []

    cash = Decimal(str(session["cash_balance"]))
    prepared = []
    for charge in charges:
        cash -= charge["amount"]
        prepared.append(
            {
                "bill_id": charge["bill_id"],
                "label": charge["label"],
                "payee": charge.get("payee"),
                "due_date": charge["due_date"],
                "amount": charge["amount"],
                "cash_after": cash,
            }
        )

    applied = database.charge_expenses(str(session["id"]), prepared, cash)
    if len(applied) != len(prepared):
        # Some were already on the ledger, so the running balance above over-counted.
        # The stored row carries the truth; take the last one written.
        cash = Decimal(str(applied[-1]["cash_after"])) if applied else Decimal(str(session["cash_balance"]))
    session["cash_balance"] = cash
    return [
        {
            "label": row["label"],
            "payee": row["payee"],
            "due_date": _as_date(row["due_date"]).isoformat(),
            "amount": float(row["amount"]),
            "cash_after": float(row["cash_after"]),
        }
        for row in applied
    ]


def upcoming(session: dict, sim_date: date, within_days: int = 45, limit: int = 6) -> list[dict]:
    """The next few bills, so the player can see the hit coming before it lands."""
    horizon = sim_date + timedelta(days=within_days)
    return [
        {
            "label": charge["label"],
            "payee": charge.get("payee"),
            "due_date": charge["due_date"].isoformat(),
            "amount": float(charge["amount"]),
            "days_away": (charge["due_date"] - sim_date).days,
        }
        for charge in due_between(session, sim_date - timedelta(days=1), horizon)
    ][:limit]


def summary(session: dict, sim_date: date, charged: list[dict]) -> dict:
    bills = bills_for(session)
    monthly = float(sum(bill["amount"] for bill in bills))
    cash = float(session["cash_balance"])
    return {
        "monthly_total": monthly,
        "bill_count": len(bills),
        "paid_to_date": float(sum(float(row["amount"]) for row in charged)),
        "upcoming": upcoming(session, sim_date),
        "overdrawn": cash < 0,
        # How long the cash on hand lasts at this burn rate. The number that should make a
        # player think twice before going all-in.
        "months_of_runway": round(cash / monthly, 1) if monthly > 0 else None,
    }
