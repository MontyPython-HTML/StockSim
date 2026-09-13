"""Put a demo customer, funding account, standing orders and a paycheck into a Nessie sandbox.

A fresh API key has an empty sandbox, so there is no balance to fund a session with, no
bills to charge against it and no payroll to pay it. This creates one of each, prints the ids, and is safe to re-run:
an existing customer with the same name is reused rather than duplicated.

--random instead creates a brand-new customer every run: a balance of at least $5,000, a
take-home salary, and a random set of bills sized by how much of a spender they are.

    uv run python -m scripts.ingestion.seed_nessie
    uv run python -m scripts.ingestion.seed_nessie --balance 25000 --list
    uv run python -m scripts.ingestion.seed_nessie --random [--seed 7]
"""

import argparse
import random
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import config

FIRST_NAME = "Demo"
LAST_NAME = "Trader"
TIMEOUT = 20

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

PAYROLL = {"amount": 2100.00, "description": "Payroll - Northwind Logistics"}

FIRST_NAMES = ["Avery", "Jordan", "Maya", "Diego", "Priya", "Marcus", "Elena", "Kai", "Nora", "Samir", "Tessa", "Owen"]
LAST_NAMES = ["Nguyen", "Patel", "Okafor", "Reyes", "Kim", "Hart", "Silva", "Brooks", "Larsen", "Chen", "Duarte", "Webb"]
PLACES = [
    ("Houston", "TX", "77005"), ("Austin", "TX", "78701"), ("Denver", "CO", "80202"),
    ("Chicago", "IL", "60601"), ("Atlanta", "GA", "30303"), ("Seattle", "WA", "98101"),
    ("Phoenix", "AZ", "85004"), ("Columbus", "OH", "43215"),
]
STREETS = ["Main St", "Oak Ave", "Elm St", "Maple Dr", "Cedar Ln", "Park Blvd", "Lakeview Rd"]
EMPLOYERS = [
    "Northwind Logistics", "Bluebird Health", "Summit Analytics", "Riverbend Schools",
    "Copperline Energy", "Juniper Software", "Harborview Hospital",
]
LANDLORDS = ["Sunrise Apartments", "Maple Court Lofts", "Parkside Residences"]

HABITS = {"frugal": 0.6, "typical": 1.0, "big spender": 1.7}

SPENDING = [
    ("Utilities", ["City Power & Light", "Metro Energy Co"], (90, 220), 1.0, False),
    ("Phone", ["Beacon Wireless", "Orbit Mobile"], (35, 95), 0.95, False),
    ("Internet", ["Fiber Collective", "Skyline Broadband"], (45, 90), 0.9, False),
    ("Health insurance", ["Northwind Health", "Evergreen Health Plans"], (150, 380), 0.6, False),
    ("Car payment", ["Onyx Auto Finance", "Keystone Motor Credit"], (250, 520), 0.55, False),
    ("Car insurance", ["Guardian Auto Insurance", "Pinecrest Mutual"], (90, 190), 1.0, False),
    ("Student loan", ["Sable Student Loans", "Crescent Loan Servicing"], (150, 450), 0.45, False),
    ("Credit card", ["Capital Card Services", "Meridian Card"], (150, 600), 0.75, True),
    ("Food delivery", ["DashPlate", "Grubline"], (40, 180), 0.6, True),
    ("Streaming", ["Streamline Media", "Nimbus TV"], (12, 45), 0.85, True),
    ("Music", ["Tempo Music"], (10, 17), 0.5, True),
    ("Gym", ["Kettle & Co Gym", "Ironworks Fitness"], (25, 90), 0.5, True),
    ("Gaming", ["Pixelvault Pass"], (10, 30), 0.35, True),
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


def ensure_payroll(account_id: str) -> dict | None:
    for deposit in _get(f"/accounts/{account_id}/deposits"):
        if "payroll" in str(deposit.get("description", "")).lower():
            return None
    return _post(
        f"/accounts/{account_id}/deposits",
        {
            "medium": "balance",
            "transaction_date": "2024-01-05",
            "status": "completed",
            "amount": PAYROLL["amount"],
            "description": PAYROLL["description"],
        },
    )


def random_profile(rng: random.Random) -> dict:
    """A plausible adult: take-home pay, at least $5,000 in the bank, and bills that fit the pay."""
    monthly_pay = rng.randrange(3800, 8501, 50)
    habit = rng.choice(list(HABITS))
    city, state, zip_code = rng.choice(PLACES)

    bills = [
        {
            "nickname": "Rent",
            "payee": rng.choice(LANDLORDS),
            "payment_amount": round(monthly_pay * rng.uniform(0.25, 0.33), -1),
            "recurring_date": 1,
            "optional": False,
        }
    ]
    has_car = False
    for nickname, payees, (low, high), chance, optional in SPENDING:
        if nickname == "Car insurance" and not has_car:
            continue
        if rng.random() > chance:
            continue
        has_car = has_car or nickname == "Car payment"
        amount = rng.uniform(low, high) * (HABITS[habit] if optional else 1.0)
        bills.append(
            {
                "nickname": nickname,
                "payee": rng.choice(payees),
                "payment_amount": round(amount, 2),
                "recurring_date": rng.randint(2, 28),
                "optional": optional,
            }
        )

    extras = sorted((bill for bill in bills if bill["optional"]), key=lambda bill: bill["payment_amount"])
    while extras and sum(bill["payment_amount"] for bill in bills) > monthly_pay * 0.9:
        bills.remove(extras.pop())

    return {
        "first_name": rng.choice(FIRST_NAMES),
        "last_name": rng.choice(LAST_NAMES),
        "address": {
            "street_number": str(rng.randint(100, 9999)),
            "street_name": rng.choice(STREETS),
            "city": city,
            "state": state,
            "zip": zip_code,
        },
        "balance": rng.randrange(5000, 40001, 250),
        "habit": habit,
        "monthly_pay": monthly_pay,
        "paycheck": round(monthly_pay * 12 / 26),
        "employer": rng.choice(EMPLOYERS),
        "bills": sorted(bills, key=lambda bill: bill["recurring_date"]),
    }


def create_random_customer(profile: dict) -> tuple[dict, dict]:
    customer = _post(
        "/customers",
        {"first_name": profile["first_name"], "last_name": profile["last_name"], "address": profile["address"]},
    )
    account = _post(
        f"/customers/{customer['_id']}/accounts",
        {"type": "Checking", "nickname": "Everyday Checking", "rewards": 0, "balance": profile["balance"]},
    )
    for bill in profile["bills"]:
        _post(
            f"/accounts/{account['_id']}/bills",
            {
                "status": "recurring",
                "payee": bill["payee"],
                "nickname": bill["nickname"],
                "payment_date": "2024-01-01",
                "recurring_date": bill["recurring_date"],
                "payment_amount": bill["payment_amount"],
            },
        )
    _post(
        f"/accounts/{account['_id']}/deposits",
        {
            "medium": "balance",
            "transaction_date": "2024-01-05",
            "status": "completed",
            "amount": profile["paycheck"],
            "description": f"Payroll - {profile['employer']}",
        },
    )
    return customer, account


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed a Nessie sandbox for the trading game.")
    parser.add_argument("--balance", type=float, default=10000.0)
    parser.add_argument("--list", action="store_true", help="only print what is already there")
    parser.add_argument("--random", action="store_true", help="create a new customer with random money habits")
    parser.add_argument("--seed", type=int, help="make --random reproducible")
    args = parser.parse_args()

    if not config.NESSIE_API_KEY:
        raise SystemExit("No NESSIE_API_KEY set - add one to .env first.")
    print(f"sandbox: {config.NESSIE_BASE_URL}")

    if args.random:
        profile = random_profile(random.Random(args.seed))
        customer, account = create_random_customer(profile)
        print(
            f"created a {profile['habit']} customer taking home {profile['monthly_pay']:,.2f} a month "
            f"(paycheck {profile['paycheck']:,.2f} every two weeks from {profile['employer']})"
        )
        account = {**account, **_get(f"/accounts/{account['_id']}")}
    else:
        customer = ensure_customer()
        account = ensure_account(customer["_id"], args.balance)
        if not args.list:
            created = ensure_bills(account["_id"])
            print(f"created {len(created)} new bill(s)")
            if ensure_payroll(account["_id"]):
                print("created a payroll deposit")

    bills = _get(f"/accounts/{account['_id']}/bills")
    monthly = sum(float(bill["payment_amount"]) for bill in bills)
    print(f"customer  {customer['_id']}  {customer['first_name']} {customer['last_name']}")
    print(f"account   {account['_id']}  {account['nickname']}  balance {account['balance']}")
    print(f"bills     {len(bills)}  totalling {monthly:,.2f} a month")
    for bill in sorted(bills, key=lambda b: b["recurring_date"]):
        print(f"   day {bill['recurring_date']:>2}  {bill['nickname']:<18} {bill['payment_amount']:>9,.2f}")
    payroll = [
        deposit for deposit in _get(f"/accounts/{account['_id']}/deposits")
        if "payroll" in str(deposit.get("description", "")).lower()
    ]
    print(f"payroll   {len(payroll)} deposit(s)" + (f", latest {payroll[-1]['amount']:,.2f}" if payroll else ""))
    print()
    print("Point the game at it with:")
    print(f"   NESSIE_USE_MOCK=false")
    print(f"   NESSIE_DEFAULT_CUSTOMER_ID={customer['_id']}")


if __name__ == "__main__":
    main()
