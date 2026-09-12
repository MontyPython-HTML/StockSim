import json
from decimal import Decimal
from functools import lru_cache

import requests

import config

TIMEOUT_SECONDS = 10


@lru_cache(maxsize=1)
def _fixture() -> dict:
    return json.loads(config.NESSIE_FIXTURE_PATH.read_text())


def _get(path: str) -> list | dict:
    response = requests.get(
        f"{config.NESSIE_BASE_URL}{path}",
        params={"key": config.NESSIE_API_KEY},
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()


def get_customers() -> list[dict]:
    if config.NESSIE_USE_MOCK:
        return _fixture()["customers"]
    return _get("/customers")


def get_customer_accounts(customer_id: str) -> list[dict]:
    if config.NESSIE_USE_MOCK:
        return [a for a in _fixture()["accounts"] if a["customer_id"] == customer_id]
    return _get(f"/customers/{customer_id}/accounts")


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
