<div align="center">

# Hackrice 2026

**StockSim** — a trading teacher that runs on real market history.

<sub>Monty Python & HTML</sub>

</div>

---

## What this is

Pick a **basket** of stocks and replay a real stretch of their history a day at a time,
trading against the close while watching the signals traders actually read — SMA
crossovers, RSI, MACD, volume spikes. The chart follows one symbol at a time; beside it you
get the whole basket rebased to 100, so correlated names look correlated, and your account
equity day by day.

Three things make it a teacher rather than a chart viewer:

- **The coach is a curriculum.** When one of *your* stocks prints a pattern (golden cross,
  death cross, the MACD crossovers, RSI extremes, a volume spike), a lesson card explains
  the pattern that is on *your* chart, quoting that bar's numbers. A pattern you have never
  been shown is taught on sight; one you have met comes back only after enough sessions
  have passed to be worth re-reading. The **Pattern school** panel tracks the syllabus per
  session.
- **The market keeps going.** When the real data runs out you roll into a **simulated
  future** — a generated price series plus AI-invented market events that move it. Those
  events have a **scope**: a company story moves one symbol, a sector story every name you
  hold in that sector, a macro story the whole basket. Five semiconductor names are not
  five bets.
- **The money is real-shaped.** Starting cash, a paycheck and eight recurring bills come
  from a Nessie bank account, and as the clock passes each bill's day the money leaves the
  same balance you trade with. Cash never goes below zero: a bill it cannot cover makes the
  bank sell shares.

The **training levels** on the landing page are five short graded runs that switch off every
panel they are not teaching and score each trade against one rule, for learning the game
before playing it.

Prices, sessions, trades, dividends and events live in **TigerData** (TimescaleDB).

## Quick start

```sh
uv sync                     # installs the project and fetches Python 3.14 if needed

cp .env.example .env        # then fill in the TigerData connection — see Configuration

# 1. create the tables and seed the ticker catalog (Nasdaq-100 + a few extras)
uv run python -m scripts.ingestion.sync_universe --init-schema

# 2. load price history for the curated starter set
uv run python -m scripts.ingestion.ingest_prices --starter --start 2022-01-01 --end 2024-12-31

# 3. run it
./run.sh                    # http://127.0.0.1:5000   (Windows: run.bat)
```

`uv sync` is the only install step. The stylesheet is committed, so the app looks right
straight after a clone and you do not need Node until you change a template.

### Prerequisites

| You need | Why |
|---|---|
| [`uv`](https://docs.astral.sh/uv/) | Runs the app, the CLIs and the checks. It reads `.python-version` and fetches Python 3.14 itself. |
| A PostgreSQL / TimescaleDB database | Everything persistent lives there. TigerData is what this was built against. |
| Node *(optional)* | Only to rebuild the stylesheet after editing templates. |

## Configuration

Everything is environment variables, read through `src/config.py`. `.env.example` is the
annotated full list — this is the short version of what actually matters.

**Required**

| Variable | Notes |
|---|---|
| `PGHOST` `PGPORT` `PGUSER` `PGPASSWORD` `PGDATABASE` | The database. `PGSSLMODE=require` for a hosted instance. |
| `TIMESCALE_SERVICE_URL` | An alternative to the `PG*` set if your provider hands you one URL. |

**Optional, but you probably want it**

| Variable | Default | Notes |
|---|---|---|
| `GEMINI_API_KEY` | *(unset)* | Turns on the AI coach, AI-invented market events and Gemini-taught lessons. `GEMINI_API_KEY2` is read as a second key. |
| `GEMINI_MODEL` | `gemini-3.6-flash` | Which model the coach and event generator use. |
| `NESSIE_USE_MOCK` | `true` | `true` serves the bank from `src/temp/nessie_mock_data.json` — bills included, no network. |
| `NESSIE_API_KEY`, `NESSIE_DEFAULT_CUSTOMER_ID` | | Needed once `NESSIE_USE_MOCK=false`, after `seed_nessie` has created a sandbox customer. |

**Tuning** — `.env.example` documents each one properly; the groups are the simulated future
(`SIMULATION_*`), the events that bend it (`SHOCK_*`), the pattern curriculum
(`PATTERN_*`), gameplay pacing (`RANDOM_EVENT_PROBABILITY`, `PREDICTION_INTERVAL_DAYS`,
`MIN_DAYS_BETWEEN_EVENTS`, `MAX_WATCHLIST`) and the connection pool (`DB_POOL_*`).

### Running without an API key

This is a supported configuration, not a degraded one. With no `GEMINI_API_KEY`:

- predictions are skipped and the coach panel says so, disabling its button rather than
  failing silently (`GET /api/ai/status` reports why);
- market events fall back to a locally generated headline, so the simulated market still
  moves during a demo;
- pattern lessons come from the built-in syllabus in `src/scripts/game/patterns.py`.

The AI runs through an **MCP server** the Flask app spawns as a subprocess
(`python -m mcp_server.server`, stdio transport), so a model call never blocks a request.

## Loading market data

`ingest_prices` is the only way prices get in. `--start` and `--end` are required, and
anything in the catalog without history shows up greyed out on the landing page.

```sh
# the curated 50-name tech starter set
uv run python -m scripts.ingestion.ingest_prices --starter --start 2022-01-01 --end 2024-12-31

# a handful of names
uv run python -m scripts.ingestion.ingest_prices --ticker AMD,AVGO,PLTR --start 2022-01-01 --end 2024-12-31

# every ticker in a universe (comma-separate for several: nasdaq100,us_tech_extra)
uv run python -m scripts.ingestion.ingest_prices --universe nasdaq100 --start 2020-01-01 --end 2025-12-31
```

Pass `--start` far enough back to get real depth: the landing page offers each symbol's own
full stored range, and "all history" on a 1980 listing reaches back to 1980. A whole-universe
ingest is slow on purpose — yfinance rate-limits, so the loader paces itself.

Three more one-shot CLIs, none of them needed for the quick start:

| Command | What it does |
|---|---|
| `uv run python -m scripts.ingestion.sync_universe --init-schema` | Applies `schema.sql` and seeds/refreshes the ticker catalog. `--universe` limits it to one. |
| `uv run python -m scripts.ingestion.seed_nessie` | Creates a Nessie sandbox customer, a funding account and eight recurring bills. `--balance`, `--random`, `--seed`, `--list`. |
| `uv run python -m scripts.ingestion.backfill_dividends` | Fills the dividend history the ex-date schedule and reinvestment read. `--ticker`, `--limit`, `--dry-run`. |

## The checks

Four suites in `src/scripts/checks/`. They are not unit tests — they drive the real engine
against the real database the way the page does, then read back what the ledger, the cash
balance and the JSON the browser receives actually say, and delete the sessions they create.

| Suite | Covers | Needs the app running? |
|---|---|---|
| `smoke_finance` | Dividends, bills, paychecks, the return split | yes |
| `smoke_ahead` | The day-ahead chart preview matching the tick that follows it | yes |
| `smoke_chart` | Chart x-axis width, so a growing series does not re-space itself | yes |
| `smoke_database` | Pool saturation, timeouts, dead connections | no |

```sh
./run.sh &                  # three of the four talk to http://127.0.0.1:5000

cd src
uv run python -m scripts.checks.smoke_database
uv run python -m scripts.checks.smoke_finance
uv run python -m scripts.checks.smoke_ahead
uv run python -m scripts.checks.smoke_chart
```

`smoke_ahead` and `smoke_chart` take `--keep` to leave their rows behind to poke at. Each
suite prints `n/n checks passed` and exits non-zero on any failure.

## Project layout

```
src/
  app.py                    Flask app, the two page routes, the JSON error handler
  config.py                 every setting, read from the environment
  routes/game_routes.py     all /api/* handlers (thin — they parse, then call engine)
  mcp_server/               the MCP server and its Gemini tools
  scripts/
    game/                   engine (sessions, trades, the clock), indicators,
                            price_source / simulation (real vs generated prices),
                            events, expenses, dividends, patterns, levels, news,
                            performance, price_cache
    database/               schema.sql, and every read and write
    api/                    nessie, finance, and the MCP client
    ingestion/              the CLIs above, plus the ticker universes
    checks/                 the four smoke suites
  templates/                index.html (setup) and game.html (the game screen)
  static/
    input/input.css         Tailwind source        -> prod/output.css (committed build)
    js/game.js              the frontend: play/pause/speed, trades, panels
    js/chart-setup.js       TradingView Lightweight Charts: price, RSI, MACD,
                            the rebased basket and the equity curve
public/                     the standalone marketing page — static HTML/CSS/JS, not served by Flask
```

The two page routes are `/` (pick a basket or a training level) and `/game/<session_id>`.
`public/` is not served by Flask — it is a static landing page with its own CSS and JS.

## Front-end styles

Templates use Tailwind v4. `src/static/prod/output.css` is a **built, committed** file, so
the app looks right straight after a clone — but if you edit a template or a JS file, a new
class will not exist until you rebuild:

```sh
cd src
npm install          # once
npm run build:css    # or: npm run watch:css
```

## Troubleshooting

**The clock stops and the page says to retry.** Every request thread shares one small
connection pool, and `DB_POOL_MAX` (12) is how many ticks can be in flight at once. A full
pool returns `503` with `Retry-After` rather than hanging. `GET /api/health` reports pool
usage without a debugger.

**A code change did not take effect.** Both launchers pass `--no-reload`, so the server does
not pick up edits on its own. Restart it.

**Nessie looks down.** The base URL is `https://api.nessieisreal.com`; the API refuses
connections on port 80, which is what makes it look like an outage.

**Bills or salaries are missing for a session.** Finances come from a Nessie customer.
Either keep `NESSIE_USE_MOCK=true`, or run `seed_nessie` and point
`NESSIE_DEFAULT_CUSTOMER_ID` at the customer it prints.

**A symbol is greyed out on the landing page.** It is in the catalog but has no price
history — run `ingest_prices` for it.

## Docs

- [`docs/API.md`](docs/API.md) — every route, the full session state object, how the
  simulated future and its shocks work, bills and the bank account, the Daily Ledger,
  teacher mode, the training levels, and a file-by-file map.
- [`.env.example`](.env.example) — every setting, annotated.
