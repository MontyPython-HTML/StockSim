import json
from decimal import Decimal
from functools import lru_cache

import requests

import config

TIMEOUT_SECONDS = 10


@lru_cache(maxsize=1)
def _fixture() -> dict:
    return json.loads(config.NESSIE_FIXTURE_PATH.read_text())


def _get(path: str) -> list[dict]:
    """Every collection endpoint this client uses answers with a JSON array."""
    response = requests.get(
        f"{config.NESSIE_BASE_URL}{path}",
        params={"key": config.NESSIE_API_KEY},
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, list) else [payload]


def get_customers() -> list[dict]:
    if config.NESSIE_USE_MOCK:
        return _fixture()["customers"]
    return _get("/customers")


def get_customer(customer_id: str) -> dict | None:
    if config.NESSIE_USE_MOCK:
        return next((c for c in _fixture()["customers"] if c["_id"] == customer_id), None)
    found = _get(f"/customers/{customer_id}")
    return found[0] if found else None


def get_customer_accounts(customer_id: str) -> list[dict]:
    if config.NESSIE_USE_MOCK:
        return [a for a in _fixture()["accounts"] if a["customer_id"] == customer_id]
    return _get(f"/customers/{customer_id}/accounts")


def get_account_bills(account_id: str) -> list[dict]:
    """The account's standing orders - rent, subscriptions, a car payment.

    `recurring_date` is a day of the month, which is what makes these playable: the
    simulated clock knows when each one comes due and takes it out of the same cash the
    player is trying to invest.
    """
    if config.NESSIE_USE_MOCK:
        return [b for b in _fixture().get("bills", []) if b["account_id"] == account_id]
    return _get(f"/accounts/{account_id}/bills")


def get_account_purchases(account_id: str) -> list[dict]:
    """One-off card spending already on the account, newest first."""
    if config.NESSIE_USE_MOCK:
        return [p for p in _fixture().get("purchases", []) if p["account_id"] == account_id]
    return _get(f"/accounts/{account_id}/purchases")


def get_account_deposits(account_id: str) -> list[dict]:
    """Money paid in. A "Payroll - <employer>" deposit is what the game reads as a paycheck."""
    if config.NESSIE_USE_MOCK:
        return [d for d in _fixture().get("deposits", []) if d["account_id"] == account_id]
    return _get(f"/accounts/{account_id}/deposits")


def pick_funding_account(customer_id: str) -> dict:
    accounts = get_customer_accounts(customer_id)
    if not accounts:
        raise ValueError(f"Nessie customer {customer_id} has no accounts")
    checking = [a for a in accounts if a.get("type") == "Checking"]
    return (checking or accounts)[0]


def get_starting_funds(customer_id: str) -> tuple[Decimal, dict]:
    account = pick_funding_account(customer_id)
    return Decimal(str(account["balance"])), account


def source_label() -> str:
    return "Nessie (local mock)" if config.NESSIE_USE_MOCK else "Nessie (live)"
