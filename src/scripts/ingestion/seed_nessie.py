"""Put a demo customer, funding account and standing orders into a Nessie sandbox.

A fresh API key has an empty sandbox, so there is no balance to fund a session with and no
bills to charge against it. This creates one of each, prints the ids, and is safe to re-run:
an existing customer with the same name is reused rather than duplicated.

    uv run python -m scripts.ingestion.seed_nessie
    uv run python -m scripts.ingestion.seed_nessie --balance 25000 --list
"""

import argparse
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import config

FIRST_NAME = "Demo"
LAST_NAME = "Trader"
TIMEOUT = 20

# Ordinary adult money, sized so a $10k account feels the drag without being wiped out in
# a month. recurring_date is the day of the month the standing order comes out.
BILLS = [
    {"payee": "Sunrise Apartments", "nickname": "Rent", "recurring_date": 1, "payment_amount": 1450.00},
    {"payee": "City Power & Light", "nickname": "Utilities", "recurring_date": 5, "payment_amount": 180.00},
    {"payee": "Onyx Auto Finance", "nickname": "Car payment", "recurring_date": 8, "payment_amount": 395.00},
    {"payee": "Northwind Health", "nickname": "Health insurance", "recurring_date": 12, "payment_amount": 320.00},
    {"payee": "Fiber Collective", "nickname": "Internet", "recurring_date": 15, "payment_amount": 75.00},
    {"payee": "Streamline Media", "nickname": "Streaming", "recurring_date": 18, "payment_amount": 24.00},
    {"payee": "Kettle & Co Gym", "nickname": "Gym", "recurring_date": 22, "payment_amount": 45.00},
    {"payee": "Sable Student Loans", "nickname": "Student loan", "recurring_date": 25, "payment_amount": 280.00},
]


def _url(path: str) -> str:
    return f"{config.NESSIE_BASE_URL}{path}"


def _get(path: str):
    response = requests.get(_url(path), params={"key": config.NESSIE_API_KEY}, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


def _post(path: str, body: dict) -> dict:
    response = requests.post(
        _url(path),
        params={"key": config.NESSIE_API_KEY},
        json=body,
        headers={"Content-Type": "application/json"},
        timeout=TIMEOUT,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"POST {path} -> {response.status_code} {response.text[:200]}")
    return response.json().get("objectCreated", {})


def ensure_customer() -> dict:
    for customer in _get("/customers"):
        if customer.get("first_name") == FIRST_NAME and customer.get("last_name") == LAST_NAME:
            return customer
    return _post(
        "/customers",
        {
            "first_name": FIRST_NAME,
            "last_name": LAST_NAME,
            "address": {
                "street_number": "6100",
                "street_name": "Main St",
                "city": "Houston",
                "state": "TX",
                "zip": "77005",
            },
        },
    )


def ensure_account(customer_id: str, balance: float) -> dict:
    for account in _get(f"/customers/{customer_id}/accounts"):
        if account.get("nickname") == "Trading Cash":
            return account
    return _post(
        f"/customers/{customer_id}/accounts",
        {"type": "Checking", "nickname": "Trading Cash", "rewards": 0, "balance": balance},
    )


def ensure_bills(account_id: str) -> list[dict]:
    existing = {bill.get("nickname") for bill in _get(f"/accounts/{account_id}/bills")}
    created = []
    for bill in BILLS:
        if bill["nickname"] in existing:
            continue
        created.append(
            _post(
                f"/accounts/{account_id}/bills",
                {
                    "status": "recurring",
                    "payee": bill["payee"],
                    "nickname": bill["nickname"],
                    "payment_date": "2024-01-01",
                    "recurring_date": bill["recurring_date"],
                    "payment_amount": bill["payment_amount"],
                },
            )
        )
    return created


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed a Nessie sandbox for the trading game.")
    parser.add_argument("--balance", type=float, default=10000.0)
    parser.add_argument("--list", action="store_true", help="only print what is already there")
    args = parser.parse_args()

    if not config.NESSIE_API_KEY:
        raise SystemExit("No NESSIE_API_KEY set - add one to .env first.")
    print(f"sandbox: {config.NESSIE_BASE_URL}")

    customer = ensure_customer()
    account = ensure_account(customer["_id"], args.balance)
    if not args.list:
        created = ensure_bills(account["_id"])
        print(f"created {len(created)} new bill(s)")

    bills = _get(f"/accounts/{account['_id']}/bills")
    monthly = sum(float(bill["payment_amount"]) for bill in bills)
    print(f"customer  {customer['_id']}  {customer['first_name']} {customer['last_name']}")
    print(f"account   {account['_id']}  {account['nickname']}  balance {account['balance']}")
    print(f"bills     {len(bills)}  totalling {monthly:,.2f} a month")
    for bill in sorted(bills, key=lambda b: b["recurring_date"]):
        print(f"   day {bill['recurring_date']:>2}  {bill['nickname']:<18} {bill['payment_amount']:>9,.2f}")
    print()
    print("Point the game at it with:")
    print(f"   NESSIE_USE_MOCK=false")
    print(f"   NESSIE_DEFAULT_CUSTOMER_ID={customer['_id']}")


if __name__ == "__main__":
    main()
