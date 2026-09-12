import argparse
import sys
import time
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.api import finance
from scripts.database import database
from scripts.ingestion import universes

# yfinance is rate-limited and occasionally just refuses a symbol; sleeping between
# tickers costs a few seconds and reliably avoids the 429s a tight loop produces.
PAUSE_BETWEEN_TICKERS = 0.4


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def ingest(ticker: str, start: date, end: date) -> int:
    # The whole body is guarded: one delisted symbol, one throttled download or one
    # transient database error must not abandon the other hundred in the batch.
    try:
        frame = finance.fetch_ohlcv(ticker, start, end)
        rows = finance.to_price_rows(ticker, frame)
        if not rows:
            print(f"{ticker}: no rows returned from yfinance for {start}..{end}")
            return 0
        written = database.upsert_prices(ticker, rows)
        bounds = database.fetch_date_bounds(ticker)
    except Exception as exc:  # noqa: BLE001 - see above
        print(f"{ticker}: FAILED ({type(exc).__name__}: {str(exc)[:120]})")
        return 0
    print(
        f"{ticker}: upserted {written} rows "
        f"(now {bounds['row_count']} total, {bounds['first_day']}..{bounds['last_day']})"
    )
    return written


def resolve_tickers(args) -> list[str]:
    """Which symbols this run should download, in the order given."""
    wanted: list[str] = []
    if args.ticker:
        wanted.extend(t.strip().upper() for t in args.ticker.split(",") if t.strip())
    if args.starter:
        wanted.extend(universes.STARTER_TICKERS)
    if args.universe:
        for name in (part.strip() for part in args.universe.split(",")):
            if name:
                wanted.extend(universes.tickers(name))
    # Dedupe but keep the caller's ordering so `--ticker AAPL --universe nasdaq100`
    # downloads the interesting name first.
    seen: set[str] = set()
    return [t for t in wanted if not (t in seen or seen.add(t))]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Load historical OHLCV data into TigerData.",
        epilog=(
            "examples:\n"
            "  ingest_prices.py --ticker AAPL,MSFT --start 2022-01-01 --end 2024-12-31\n"
            "  ingest_prices.py --starter --start 2022-01-01 --end 2024-12-31\n"
            "  ingest_prices.py --universe nasdaq100 --start 2020-01-01 --end 2025-12-31\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--ticker", help="comma-separated, e.g. AAPL,MSFT")
    parser.add_argument("--start", required=True, type=parse_date)
    parser.add_argument("--end", required=True, type=parse_date)
    parser.add_argument(
        "--universe",
        help=(
            "every ticker in a universe, comma-separated for several "
            f"({', '.join(universes.universe_names())})"
        ),
    )
    parser.add_argument(
        "--starter",
        action="store_true",
        help=f"the curated {len(universes.STARTER_TICKERS)}-name tech starter set",
    )
    parser.add_argument(
        "--init-schema",
        action="store_true",
        help="apply schema.sql and seed the ticker catalog first",
    )
    args = parser.parse_args()

    if not (args.ticker or args.universe or args.starter):
        parser.error("nothing to do: pass --ticker, --starter and/or --universe")

    if args.init_schema:
        database.apply_schema()
        seeded = database.upsert_tickers(universes.rows())
        print(f"schema applied, {seeded} tickers in the catalog")

    tickers = resolve_tickers(args)
    print(f"ingesting {len(tickers)} ticker(s) over {args.start}..{args.end}")
    total = 0
    for position, ticker in enumerate(tickers, start=1):
        print(f"[{position}/{len(tickers)}]", end=" ")
        total += ingest(ticker, args.start, args.end)
        if position < len(tickers):
            time.sleep(PAUSE_BETWEEN_TICKERS)
    print(f"done: {total} rows across {len(tickers)} ticker(s)")


if __name__ == "__main__":
    main()
