"""Seed the `tickers` catalog from scripts/ingestion/universes.py.

Run this after pulling, and whenever the universe list changes. It is idempotent: rows
are upserted by ticker, so re-running just refreshes names, sectors and flags.

    uv run python -m scripts.ingestion.sync_universe --init-schema
    uv run python -m scripts.ingestion.sync_universe --universe nasdaq100

Existing price history is never touched - the catalog is just the list of symbols the
game knows about, and stock_prices stays whatever you have actually downloaded.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.database import database
from scripts.ingestion import universes


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the TigerData ticker catalog.")
    parser.add_argument(
        "--universe",
        help=f"limit to one universe ({', '.join(universes.universe_names())}); default: all",
    )
    parser.add_argument("--init-schema", action="store_true", help="apply schema.sql first")
    args = parser.parse_args()

    if args.init_schema:
        database.apply_schema()
        print("schema applied")

    rows = universes.rows(args.universe)
    written = database.upsert_tickers(rows)
    stats = database.universe_stats()
    print(
        f"catalog: {written} rows upserted | "
        f"{stats['known']} known, {stats['tech']} tech, {stats['ingested']} with price history"
    )

    pending = [row[0] for row in rows if row[0] not in _ingested_tickers()]
    if pending:
        preview = ", ".join(pending[:8]) + (" ..." if len(pending) > 8 else "")
        print(f"{len(pending)} catalogued symbols still need history: {preview}")
        print(
            "load them with: uv run python -m scripts.ingestion.ingest_prices "
            "--universe nasdaq100 --start 2022-01-01 --end 2024-12-31"
        )


def _ingested_tickers() -> set[str]:
    return {row["ticker"] for row in database.list_universe() if row["has_data"]}


if __name__ == "__main__":
    main()
