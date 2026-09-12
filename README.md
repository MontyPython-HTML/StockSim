<div align="center">

# Hackrice 2026

</div>

---

Monty Python & HTML 

## What this is

A trading teacher. You pick a **basket** of stocks, replay a real stretch of their history
a day at a time, and trade any of them against the close while watching the signals
traders actually read (SMA crossovers, RSI, MACD, volume spikes). The chart follows one
symbol at a time; alongside it you get the whole basket rebased to 100, so correlated
names look correlated, and your account equity day by day. An AI coach gives you a read on
what might come next. When the real data runs out you can roll into a **simulated
future**: a generated price series plus random AI-invented market events that move it.

Those events have a **scope**. A company story moves one symbol, a sector story moves every
name you hold in that sector, and a macro story moves the whole basket — which is the point:
five semiconductor names are not five bets.

The coach is also a **curriculum**, not just a feed. When one of your own stocks prints a
pattern (golden cross, death cross, the MACD crossovers, RSI extremes, a volume spike), a
lesson card explains the pattern that is on *your* chart, quoting that bar's numbers. A
pattern you have never been shown is taught on sight; one you have met comes back only
after enough sessions have passed that it is worth re-reading. The **Pattern school**
panel tracks the whole syllabus per session, so you can see what you have learned and what
is still ahead of you. With no `GEMINI_API_KEY` the lessons come from the built-in
syllabus in `src/scripts/game/patterns.py`; with a key, Gemini teaches the same pattern in
context.

Signals print on the chart as you play and collect in a **drawer** on the right edge —
closed by default so the charts keep their width, with an unread count on the tab.

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

Pass `--start` far enough back to get real depth: the landing page offers each symbol's
full stored range, and "all history" on a 1980 listing reaches back to 1980. `--universe`
takes more than one name, e.g. `--universe nasdaq100 us_tech_extra`.

`GEMINI_API_KEY` is optional for the core game: without it, predictions are skipped and
market events fall back to a locally generated headline, so the simulated market still
moves during a demo, and pattern lessons fall back to the built-in syllabus. The game
screen says so in the AI coach panel and disables the button rather than failing silently.

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
