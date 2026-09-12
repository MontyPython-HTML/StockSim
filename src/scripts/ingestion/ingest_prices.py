import argparse
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.api import finance
from scripts.database import database


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def ingest(ticker: str, start: date, end: date) -> int:
    frame = finance.fetch_ohlcv(ticker, start, end)
    rows = finance.to_price_rows(ticker, frame)
    if not rows:
        print(f"{ticker}: no rows returned from yfinance for {start}..{end}")
        return 0
    written = database.upsert_prices(ticker, rows)
    bounds = database.fetch_date_bounds(ticker)
    print(
        f"{ticker}: upserted {written} rows "
        f"(now {bounds['row_count']} total, {bounds['first_day']}..{bounds['last_day']})"
    )
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Load historical OHLCV data into TigerData.")
    parser.add_argument("--ticker", required=True, help="comma-separated, e.g. AAPL,MSFT")
    parser.add_argument("--start", required=True, type=parse_date)
    parser.add_argument("--end", required=True, type=parse_date)
    parser.add_argument("--init-schema", action="store_true", help="apply schema.sql first")
    args = parser.parse_args()

    if args.init_schema:
        database.apply_schema()
        print("schema applied")

    for ticker in [t.strip() for t in args.ticker.split(",") if t.strip()]:
        ingest(ticker, args.start, args.end)


if __name__ == "__main__":
    main()
