"""Fill in the cash dividends on price rows that were ingested before they were stored.

`stock_prices.dividend` has been in the schema from the start, but for a database loaded
before dividends were carried through the ingest, every row says the stock never paid
anything - which quietly switches the game's entire dividend feature off. Nothing errors;
payments simply never happen.

Re-downloading the history would rewrite a few hundred thousand rows per symbol to repair
one column. This walks the symbols that already have prices, asks yfinance once per symbol
for its dividend actions over the range already stored, and updates only the rows that
actually paid.

```sh
uv run python -m scripts.ingestion.backfill_dividends            # every stored symbol
uv run python -m scripts.ingestion.backfill_dividends --ticker AEP,IBM
uv run python -m scripts.ingestion.backfill_dividends --dry-run  # report, change nothing
```
"""

import argparse
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.api import finance
from scripts.database import database

PAUSE_BETWEEN_TICKERS = 0.4


def stored_tickers() -> list[str]:
    """Symbols with history in the database, in catalog order where we can get it."""
    with database.get_cursor() as cur:
        cur.execute(
            """
            SELECT p.ticker
              FROM stock_prices p
              LEFT JOIN tickers t ON t.ticker = p.ticker
             GROUP BY p.ticker, t.universe
             ORDER BY t.universe NULLS LAST, p.ticker
            """
        )
        return [row["ticker"] for row in cur.fetchall()]


def dividends_for(ticker: str) -> list[tuple[date, float]]:
    """The declared dividends inside the range this symbol already has stored."""
    bounds = database.fetch_date_bounds(ticker)
    if not bounds:
        return []
    frame = finance.fetch_ohlcv(ticker, bounds["first_day"], bounds["last_day"] + timedelta(days=1))
    if frame.empty or "Dividends" not in frame.columns:
        return []
    paid = frame[frame["Dividends"].fillna(0) > 0]
    return [(row["Date"], float(row["Dividends"])) for _, row in paid.iterrows()]


def backfill(ticker: str, dry_run: bool) -> tuple[int, int]:
    """Returns (declarations found, rows actually written)."""
    declarations = dividends_for(ticker)
    if dry_run:
        return len(declarations), 0
    return len(declarations), database.update_dividends(ticker, declarations)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Set the dividend column on price rows that were ingested without it.",
        epilog=(
            "examples:\n"
            "  backfill_dividends.py --ticker AEP,IBM\n"
            "  backfill_dividends.py --limit 5 --dry-run\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--ticker", help="comma-separated; defaults to every stored symbol")
    parser.add_argument("--limit", type=int, help="stop after this many symbols")
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    args = parser.parse_args()

    tickers = [t.strip().upper() for t in (args.ticker or "").split(",") if t.strip()] or stored_tickers()
    if args.limit:
        tickers = tickers[: args.limit]
    if not tickers:
        print("nothing to do: no symbols with stored prices")
        return

    print(f"{'checking' if args.dry_run else 'backfilling'} dividends for {len(tickers)} symbol(s)")
    declarations = written = failures = 0
    for position, ticker in enumerate(tickers, start=1):
        print(f"[{position}/{len(tickers)}]", end=" ")
        try:
            found, rows = backfill(ticker, args.dry_run)
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"{ticker}: FAILED ({type(exc).__name__}: {str(exc)[:120]})")
            continue
        declarations += found
        written += rows
        print(f"{ticker}: {found} dividend(s)" + ("" if args.dry_run else f", {rows} row(s) updated"))
        if position < len(tickers):
            time.sleep(PAUSE_BETWEEN_TICKERS)

    verb = "found" if args.dry_run else "updated"
    print(f"done: {declarations} dividend declaration(s) {verb}" + ("" if args.dry_run else f", {written} row(s)"))
    if failures:
        print(f"{failures} symbol(s) failed; run them again individually")


if __name__ == "__main__":
    main()
