<div align="center">

# Hackrice 2026

</div>

---

Monty Python & HTML 

## What this is

A trading teacher. You replay a real stretch of one stock's history a day at a time,
place trades against the close, and watch the signals (SMA crossovers, RSI, MACD, volume
spikes) that traders actually read. An AI coach gives you a read on what might come next.
When the real data runs out you can roll into a **simulated future**: a generated price
series plus random AI-invented market events that move it.

Prices, sessions, trades and events live in **TigerData** (TimescaleDB).

## Getting started

```sh
cp .env.example .env      # fill in TigerData credentials and a Gemini API key
uv sync

# 1. create the tables and seed the ticker catalog (Nasdaq-100 + a few extras)
uv run python -m scripts.ingestion.sync_universe --init-schema

# 2. download history for the starter tech set
uv run python -m scripts.ingestion.ingest_prices --starter --start 2022-01-01 --end 2024-12-31

./run.sh                  # http://127.0.0.1:5000
```

Anything in the catalog without history shows up greyed out on the landing page. Load
more of it whenever you like:

```sh
# a handful of names
uv run python -m scripts.ingestion.ingest_prices --ticker AMD,AVGO,PLTR --start 2022-01-01 --end 2024-12-31

# the whole Nasdaq-100 (slow - yfinance rate limits, so it paces itself)
uv run python -m scripts.ingestion.ingest_prices --universe nasdaq100 --start 2020-01-01 --end 2025-12-31
```

`GEMINI_API_KEY` is optional for the core game: without it, predictions are skipped and
market events fall back to a locally generated headline, so the simulated market still
moves during a demo.

## Front-end styles

Templates use Tailwind. `src/static/prod/output.css` is a **built, committed** file, so the
app looks right straight after a clone — but if you edit a template or JS file you have to
rebuild it or your new classes will not exist:

```sh
cd src && npm install     # once
npm run build:css         # or: npm run watch:css
```

## Docs

- [`docs/API.md`](docs/API.md) — every route, the session state object, and how the
  simulated future and market events work.
