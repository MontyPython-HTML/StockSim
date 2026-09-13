# API reference

Base URL when running locally: `http://127.0.0.1:5000`

All JSON endpoints live under `/api` ([src/routes/game_routes.py](../src/routes/game_routes.py)). There are also two plain HTML page routes served directly by Flask ([src/app.py](../src/app.py)).

## Conventions

- All request/response bodies are JSON (`Content-Type: application/json`).
- Dates are `YYYY-MM-DD` strings (a trading day from the ingested price history, not a calendar day).
- Money fields are plain JSON numbers (floats), not strings.
- Every endpoint that fails a game rule (bad ticker, insufficient funds, unknown session, etc.) returns:

  ```json
  { "error": "<human-readable message>" }
  ```

  with HTTP `400`.
- **Every `/api` failure is JSON.** A malformed path, the wrong method and even an
  unexpected 500 come back as `{"error": "..."}` rather than Flask's HTML error page, so a
  client can always parse the response. Unhandled failures are still logged in full.
- A malformed `session_id` is treated as an unknown session (`400 Session not found`) rather
  than being handed to the database, where an invalid uuid raised a driver error.

---

## Page routes

### `GET /`

Renders the "start a session" landing page. Passes the ingested tickers, the full ticker
catalog (grouped by sector in the UI, symbols without history are disabled), catalog
stats, the default Nessie funding customer and the training level catalog into the template.
The level track sits above the full-simulation form.

### `GET /game/<session_id>`

Renders the game screen for an existing session. Redirects to `/` if the session id doesn't exist.

---

## The session state object

Several endpoints return the same shape — the full current state of one game session. It looks like this:

```json
{
  "session_id": "29f42d68-23ab-4881-86de-ef6f4baf0f3d",
  "tickers": ["MSFT", "AAPL"],
  "focus": null,
  "sim_date": "2023-03-01",
  "start_date": "2023-03-01",
  "end_date": "2023-12-31",
  "status": "active",
  "today": {
    "date": "2023-03-01",
    "open": 249.13, "high": 250.55, "low": 244.63, "close": 246.27,
    "volume": 27808900,
    "sma20": 254.87, "sma50": 258.02,
    "rsi14": 42.8,
    "macd": -1.92, "macd_signal": -1.55, "macd_hist": -0.37,
    "volume_avg20": 25012340
  },
  "portfolio": {
    "positions": [
      { "ticker": "MSFT", "shares": 20.0, "avg_cost": 246.27,
        "market_value": 4925.40, "unrealized_pl": 0.0 }
    ],
    "market_value": 4925.40,
    "cash_balance": 5074.60,
    "net_worth": 10000.00,
    "starting_cash": 10000.00,
    "total_return_pct": 0.0
  },
  "chart": [
    { "date": "2022-08-05", "close": 274.58, "volume": 21030100,
      "sma20": 271.12, "sma50": 265.90,
      "rsi14": 58.3, "macd": 1.02, "macd_signal": 0.87, "macd_hist": 0.15 }
  ],
  "trades": [
    { "date": "2023-03-01", "ticker": "MSFT", "side": "BUY", "shares": 20.0, "price": 246.27 }
  ],
  "quotes": [
    { "ticker": "MSFT", "close": 246.27, "previous_close": 247.5,
      "change_pct": -0.50, "simulated": false }
  ],
  "signals": [
    { "name": "Golden cross", "direction": "bullish", "message": "...",
      "ticker": "MSFT", "date": "2023-03-01" }
  ],
  "pending_ai": [
    { "kind": "PATTERN_LESSON", "ticker": "MSFT", "pattern": "Golden cross",
      "date": "2023-03-01" }
  ],
  "ai_feed": [
    {
      "id": 42, "type": "PREDICTION", "sim_date": "2023-03-01",
      "payload": {
        "direction": "up", "confidence": 0.75,
        "rationale": "...",
        "referenced_indicators": ["SMA20 above SMA50", "RSI 68"],
        "what_to_watch": "...",
        "disclaimer": "Simulated teaching output. Not financial advice."
      }
    }
  ],
  "patterns": [
    { "slug": "golden_cross", "name": "Golden cross", "family": "trend",
      "tension": "...", "taught": true, "lessons": 1,
      "first_taught": "2023-03-01", "first_ticker": "MSFT" }
  ]
}
```

Notes:
- `today` and `chart` come from [indicators.py](../src/scripts/game/indicators.py) — see that file (or ask about "predictors") for what `sma20`/`sma50`/`rsi14`/`macd*` mean.
- `chart` is a rolling window (default last 180 trading days through `sim_date`), meant for feeding straight into a chart library.
- `ai_feed` entries are whatever the Gemini MCP server (or the local event generator) has logged so far for this session. `type` is `"PREDICTION"`, `"NEWS_EVENT"`, `"MARKET_SHOCK"`, or `"PATTERN_LESSON"` — see [server.py](../src/mcp_server/server.py). A `MARKET_SHOCK` payload carries `bars_affected`, the number of generated sessions its headline actually moved; a `PATTERN_LESSON` payload carries `pattern` (the slug), `what_it_is`/`how_to_spot`/`why_it_matters`/`common_mistake`/`watch_next`, `context` (the indicator readings it was written against), and `source` (`"gemini"` or `"offline"`).
- `patterns` is the whole syllabus in teaching order, each row marked with whether this session has been taught it, how many times, and the first bar and symbol that taught it. See [patterns.py](../src/scripts/game/patterns.py).
- `signals` and `pending_ai` are only present on the `/advance` response: `signals` are the patterns that printed on the bars the clock just walked, and `pending_ai` is what was queued for the background workers (AI runs off the request path, so its output lands in `ai_feed` on a later tick).
- `simulation` describes where prices are coming from. `{ "mode": "real", "ticker": "MSFT" }` while replaying real history; once forked, `"mode": "simulated"` plus the fork date, anchor price, drift, volatility and seed (see [`/api/session/<id>/simulation`](#get-apisessionsession_idsimulation)).
- `status` is `"active"` until `sim_date` reaches `end_date` (or the ticker's last stored trading day), then it flips to `"finished"` and further `/advance` calls become no-ops.

---

## `POST /api/session/start`

Starts a new game session: picks the first available trading day on/after `start_date`, pulls a starting cash balance from Nessie, and creates the session row in TigerData.

**Request body**
```json
{
  "ticker": "MSFT",
  "start_date": "2023-03-01",
  "end_date": "2023-12-31",
  "nessie_customer_id": "mock-customer-001",
  "salary_amount": 2100,
  "simulate_future": false
}
```
- `ticker` — required.
- `start_date`, `end_date` — required, `YYYY-MM-DD`.
- `nessie_customer_id` — optional; defaults to `NESSIE_DEFAULT_CUSTOMER_ID` (`.env`).
- `salary_amount` — optional paycheck paid every other Friday, any amount. Left out, it
  defaults to the customer's latest `Payroll - <employer>` deposit in Nessie; `0` (or less)
  means no salary. See [Paychecks](#paychecks).
- `simulate_future` — optional, default `false`. When `true`, the session forks at the
  end of the real data and keeps going into a generated future of its own; `end_date` in
  the response is then extended to the end of that horizon. Optional `horizon_days`,
  `drift`, `volatility` and `seed` tune it (see
  [`POST /api/session/<id>/simulate`](#post-apisessionsession_idsimulate)).

**Response `201`** — the [session state object](#the-session-state-object), plus a `funding` block:
```json
{
  "...": "...(full state object)",
  "funding": {
    "source": "Nessie (local mock)",
    "account_nickname": "Trading Cash",
    "account_type": "Checking",
    "balance": 10000.0,
    "paycheck": 2100.0
  }
}
```

**Errors (`400`)**
- `ticker is required` — empty/missing ticker.
- `No price data stored for <TICKER>. Ingest it first.` — ticker not in TigerData yet (run `ingest_prices.py`).
- `No <TICKER> trading days between <start> and <end>` — date range doesn't overlap stored data.
- `start_date must be a date formatted YYYY-MM-DD` / same for `end_date`.

---

## `GET /api/session/<session_id>/state`

Fetches the current state of a session — used to resync the page on load/reload.

**Response `200`** — the [session state object](#the-session-state-object).

**Errors (`400`)** — `Session not found` if the id doesn't exist.

---

## `POST /api/session/<session_id>/advance`

Moves the simulation forward one or more trading days.

**Request body**
```json
{ "days": 1, "with_ai": true }
```
- `days` — optional, default `1`. Advances this many trading days per call (the frontend plays days prefetched from [`GET /lookahead`](#get-apisessionsession_idlookahead) one per frame and commits the ones it has shown here in batches, since each call is a network round trip).
- `with_ai` — optional, default `true`. Set `false` to skip rolling for a random news event / scheduled prediction on this call.

**Response `200`** — the [session state object](#the-session-state-object), plus:
```json
{
  "...": "...(full state object, sim_date/today/portfolio/chart already reflect the new day)",
  "signals": [
    { "name": "Golden cross", "direction": "bullish",
      "message": "The 20-day average crossed above the 50-day average...",
      "date": "2023-03-14" }
  ],
  "pending_ai": [{ "kind": "PREDICTION", "ticker": "AAPL" }]
}
```
- `signals` — any technical signals that *fired on this call* (state changes only, e.g. a crossover happening — not "RSI is currently above 70"). Each entry carries the `ticker` it belongs to.
- `pending_ai` — which AI event kinds were just scheduled in the background (`"PREDICTION"`, `"NEWS_EVENT"`, and `"MARKET_SHOCK"` for a session with a simulated future). The AI call itself runs asynchronously via the Gemini MCP server and is **not** in the response yet — poll `GET /state` a few seconds later and check `ai_feed` for the result. If the MCP server is unavailable, `pending_ai` items simply never turn into `ai_feed` entries; the simulation itself is unaffected.
- Advancing past `end_date` is a no-op that just returns the final state with `status: "finished"` and empty `signals`/`pending_ai`.

**Errors (`400`)** — `Session not found`; `days` that is not an integer, or is below 1.

---

## `GET /api/session/<session_id>/lookahead`

The next trading days after the clock, priced but **not** played: nothing is saved. The game page buffers these so it can show one day per frame at an even pace while `/advance` catches the saved clock up in batches. Before a trade, a pause or "Next day" the page commits every day it has shown, so trades always fill on the day on screen.

**Query parameters**
- `after` — optional `YYYY-MM-DD`. Only days after this one are returned (the page passes the last day it already holds). Defaults to the session's `sim_date`.
- `days` — optional, default `1`. How many days to return.

Reads at most 60 trading days past the saved `sim_date`, so a page far ahead of its last `/advance` gets an empty `frames` list until it commits.

**Response `200`**
```json
{
  "sim_date": "2023-03-21",
  "done": false,
  "frames": [
    {
      "date": "2023-03-22",
      "quotes": { "AMZN": { "close": 98.7, "previous_close": 100.61, "change_pct": -1.9, "simulated": false } },
      "rows": { "AMZN": { "date": "2023-03-22", "close": 98.7, "volume": 60000000, "sma20": 95.1, "sma50": 97.0,
                          "rsi14": 52.3, "macd": 0.4, "macd_signal": -0.2, "macd_hist": 0.6 } }
    }
  ]
}
```
- `frames` — walks the same calendar as `/advance`, so advancing N days lands on `frames[N-1].date`. `quotes` holds the same numbers the state's `quotes` will show on that day, for every symbol; `rows` holds a chart row only for symbols that traded that day.
- `done` — `true` when no trading days are left after the returned frames, or the session is not active.
- If `/advance` later reports a different close for a buffered day (a market shock rewrote a simulated future), the page drops its buffer and fetches again.

**Errors (`400`)** — `Session not found`; `after` that is not a date; `days` that is not an integer, or is below 1.

---

## `POST /api/session/<session_id>/trade`

Executes a buy or sell at the current `sim_date`'s closing price.

**Request body**
```json
{ "ticker": "AAPL", "side": "BUY", "shares": 10 }
```
- `ticker` — required, and must be one of this session's watched symbols.
- `side` — `"BUY"` or `"SELL"` (case-insensitive).
- `shares` — number, must be `> 0` and no more than 1,000,000,000.

**Response `200`** — the [session state object](#the-session-state-object) with updated `portfolio`/`trades`.

**Errors (`400`)**
- `<TICKER> is not in this session's watchlist.`
- `side must be BUY or SELL`
- `shares must be greater than zero` / `shares must be a number` /
  `shares must be between 0 and 1,000,000,000` (also catches `NaN` and infinity)
- `Not enough cash: that costs $X and you have $Y` (BUY)
- `You only hold N shares of <TICKER>` (SELL)
- `This session is over. Start a new one to keep trading.` — a `finished` session stops
  accepting trades; the clock is the risk, so it cannot be reopened once it has run out.
  Fork a new future onto it (`/simulate`) to put it back in play.
- `Session not found`

---

## `POST /api/session/<session_id>/predict`

Synchronously asks the Gemini MCP server for a prediction right now (used by the "Ask for a read" button), rather than waiting for the periodic automatic one.

**Response `200`**
```json
{
  "prediction": {
    "direction": "up",
    "confidence": 0.75,
    "rationale": "...",
    "referenced_indicators": ["SMA20 above SMA50", "RSI 68"],
    "what_to_watch": "...",
    "disclaimer": "Simulated teaching output. Not financial advice."
  }
}
```

**Response `503`** — if the MCP server / Gemini is unavailable (subprocess crashed, all API keys failed, etc.):
```json
{ "error": "The AI coach is unavailable right now." }
```

**Errors (`400`)** — `Session not found`.

---

## `GET /api/universe`

The ticker catalog: every symbol the game knows about, not just the ones with history
loaded. Seeded from `src/scripts/ingestion/universes.py` (`nasdaq100` plus a small
`us_tech_extra` group). A catalogued symbol with `has_data: false` needs an
`ingest_prices.py` run; it is not an error.

**Query parameters**
- `universe` — optional, e.g. `nasdaq100`.
- `tech_only` — optional, `true`/`1`/`yes` to return only `is_tech` symbols.

**Response `200`**
```json
{
  "universe": "nasdaq100",
  "stats": { "known": 108, "ingested": 4, "tech": 66 },
  "tickers": [
    { "ticker": "AAPL", "company_name": "Apple", "sector": "Technology",
      "universe": "nasdaq100", "is_tech": true,
      "first_day": "2022-01-03", "last_day": "2024-12-31", "row_count": 753, "has_data": true,
      "description": "Makes the iPhone, Mac computers, iPad and Apple Watch, and sells services like the App Store and Apple Music." },
    { "ticker": "ABNB", "company_name": "Airbnb", "sector": "Consumer Discretionary",
      "universe": "nasdaq100", "is_tech": false,
      "first_day": null, "last_day": null, "row_count": 0, "has_data": false,
      "description": "Runs a website and app where people rent out their homes or rooms to travelers." }
  ]
}
```

`description` is a one- or two-sentence plain-English summary of the company, written for
beginners, from [`descriptions.py`](../src/scripts/ingestion/descriptions.py). A ticker without
one gets a generic line built from its name and sector. The game screen shows it in the hover
card on watchlist tiles, holdings, the chart title and the newspaper's price box.

---

# The simulated future

Real history stops at the last ingested trading day. A session can *fork* at that point
into a generated future (see [`simulation.py`](../src/scripts/game/simulation.py)). The
generated bars live in `simulated_prices`, keyed by `session_id`, and are never written
into `stock_prices` — the shared dataset stays real for every player, and each player's
invented future is theirs alone.

Generate one either up front (`"simulate_future": true` on `/api/session/start`) or
later (`POST /api/session/<id>/simulate`).

## `POST /api/session/<session_id>/simulate`

Forks an existing session onto its own generated future, at the last real trading day it
can see. Calling it again on a session that already has a future **lengthens** that
future instead: only `horizon_days` is honoured, because the seed, anchor price and fork
date are part of the session's identity once the player has traded in it. Re-rolling them
would silently rewrite candles the player already acted on. A `finished` session is put
back to `active` when it is forked.

**Request body** — all optional; anything omitted is estimated from the ticker's own history.
```json
{
  "horizon_days": 252,
  "drift": 0.0005,
  "volatility": 0.02,
  "seed": 42
}
```
- `horizon_days` — how many generated sessions to create (default `SIMULATION_HORIZON_DAYS`, 252).
- `drift` — daily log drift. Omitted: measured from the last year of real returns and
  capped between -20% and +30% annualised.
- `volatility` — daily log volatility. Omitted: measured from real returns.
- `seed` — omitted: random. Supplying one makes the whole future reproducible.

The horizon grows on demand: if the player reaches the last generated bar, another 252
sessions are appended automatically, and calling this endpoint again is the explicit way
to ask for more (`{"horizon_days": 504}`).

**Response `201`** — the [session state object](#the-session-state-object), with
`simulation.mode` now `"simulated"` and `end_date` extended to the end of the generated
horizon.

**Errors (`400`)** — `Session not found`, or `no real history for <TICKER> at or before <date>`.

---

## `GET /api/session/<session_id>/simulation`

The fork parameters, how far the future currently runs, and every shock applied.

**Response `200`** — when the session is simulating:
```json
{
  "active": true,
  "session_id": "...",
  "config": {
    "ticker": "NVDA", "fork_date": "2024-12-31", "anchor_price": 134.29,
    "horizon_days": 252, "drift": 0.00104, "volatility": 0.0328, "mean_reversion": 0.15,
    "annualized_drift_pct": 29.98, "annualized_volatility_pct": 52.07,
    "seed": 42, "generator": "gbm"
  },
  "bounds": { "first_day": "2023-01-03", "last_day": "2024-12-31", "row_count": 750 },
  "generated_bars": 252,
  "shocks": [
    { "event_id": 118, "first_bar": "2025-02-04", "last_bar": "2025-02-14", "bars": 244,
      "low_close": 131.4, "high_close": 148.9,
      "payload": { "headline": "...", "sentiment": 0.62, "magnitude": 0.71, "source": "gemini" } }
  ]
}
```
`shocks` has one entry per event (newest first), not per bar: a single headline reprices
dozens of generated sessions, so it reports the window it touched instead of each candle.

**Response `200`** — when the session is still on real data:
```json
{ "active": false, "session_id": "..." }
```

**Errors (`400`)** — `Session not found`.

---

## `POST /api/session/<session_id>/shock`

Injects one specific market event, bypassing Gemini. Same code path as an AI-generated
event, just with the numbers handed in — useful when a demo needs a particular headline,
or when the API is slow.

**Request body**
```json
{
  "ticker": "NVDA",
  "scope": "sector",
  "headline": "NVDA lands a multi-billion dollar sovereign AI deal",
  "summary": "optional one-liner",
  "sentiment": 0.85,
  "magnitude": 0.8,
  "decay_days": 8,
  "lesson": "optional teaching line"
}
```
- `sentiment` — required, `-1`..`1` (bearish..bullish).
- `magnitude` — required, `0`..`1`.
- `scope` — how wide the story lands. Defaults to `ticker`.
  - `ticker` — only the named symbol moves.
  - `sector` — every symbol in the session's basket filed under the same sector as
    `ticker` moves (the sector is looked up from the `tickers` catalog; override it by
    passing `sector` explicitly).
  - `market` — the whole basket moves.
- `decay_days` — optional, default `10`.

Each affected symbol is repriced by the same story with a stable per-symbol multiplier
(`SHOCK_PEER_SPREAD`, ±45% of magnitude by default), so a sector move is correlated
without every chart being a copy of the same candle. Real (unforked) symbols are skipped:
shared history is never rewritten.

**Response `201`**
```json
{
  "ticker": "NVDA", "scope": "sector", "sector": "Technology",
  "headline": "...", "sentiment": 0.85, "magnitude": 0.8,
  "decay_days": 8, "source": "manual", "fictional": true,
  "applied": true, "bars_affected": 244, "event_id": 118,
  "affected_tickers": ["NVDA", "AAPL", "AMD"],
  "affected": [
    { "ticker": "NVDA", "bars_affected": 244, "magnitude": 0.62 },
    { "ticker": "AAPL", "bars_affected": 244, "magnitude": 0.81 }
  ]
}
```
```

How the shock lands: `sentiment x magnitude x SHOCK_IMPACT_SCALE` is the total repricing
(18% at maximum by default), of which `SHOCK_IMMEDIATE_SHARE` (40%) is priced on the day
the story breaks and the rest bleeds in as extra drift over `decay_days`. The day-one
bar also gets a volume spike.

**Errors (`400`)**
- `sentiment and magnitude are required`
- `sentiment must be between -1 and 1` / `magnitude must be between 0 and 1`
- `This session is replaying real data; fork a simulated future first.`
- `Session not found`

---

## Quick reference

| Method | Path                              | Purpose                                   |
|--------|-----------------------------------|--------------------------------------------|
| GET    | `/`                                | Start-session landing page (HTML)          |
| GET    | `/game/<session_id>`               | Game screen (HTML)                         |
| GET    | `/api/universe`                    | Ticker catalog (with/without history)      |
| POST   | `/api/session/start`               | Start a new session                        |
| GET    | `/api/session/<id>/state`          | Fetch current session state                |
| POST   | `/api/session/<id>/advance`        | Advance N trading days                     |
| GET    | `/api/session/<id>/lookahead`      | Next days' prices, nothing saved           |
| POST   | `/api/session/<id>/trade`          | Buy/sell at today's close                  |
| POST   | `/api/session/<id>/predict`        | Ask the AI coach for a prediction now      |
| POST   | `/api/session/<id>/simulate`       | Fork onto a generated future               |
| GET    | `/api/session/<id>/simulation`     | Fork parameters, bounds, applied shocks    |
| POST   | `/api/session/<id>/shock`          | Inject one specific market event           |
| GET    | `/api/session/<id>/basket`         | Rebased basket comparison + equity curve   |
| GET    | `/api/session/<id>/news`           | One day's Daily Ledger edition             |
| GET    | `/api/levels`                      | Training level catalog                     |
| POST   | `/api/levels/<number>/start`       | Start a training level                     |

---

## Performance notes

The database here is remote, so a request's cost is mostly its number of round trips. Three
things keep a clock tick cheap, and each one is a contract worth preserving:

- **Generated futures are cached in-process.** `simulation` keeps a symbol's stored bars
  (`_bars_cache`), its assembled future (`_future_cache`) and the full history-spliced frame
  with indicators recomputed (`_merged_cache`), keyed by `(session_id, ticker)`. Every write
  to a future must call `simulation.invalidate(session_id, ticker)` — `ensure_future`,
  `extend_horizon`, `apply_shock` and `fork_session` all do. Skip that call and a shock will
  be silently swallowed by a stale frame. Rebuilding instead of caching cost 24-29 round
  trips per tick; it is 5-11 now.
- **Writes are one statement, not one per row.** `executemany` sends a round trip per row:
  a single 252-bar shock took ~17s through it, because the database is across the network.
  Bulk writes go through `execute_values` with a `VALUES` list, which is also why the
  rescale statement casts its batch columns (an all-NULL `event_id` infers as `text` and
  `coalesce(text, bigint)` is a type error).
- **AI runs off the request path.** `engine._schedule_ai` hands work to a small thread pool
  and the result lands in `mcp_events`, so it shows up in `ai_feed` on a later tick. The
  frontend relies on that: it does not poll while the clock is running, because the next
  `advance` response already carries the updated feed.

Handlers that already hold a session bundle should pass what it contains —
`price_source.for_watchlist(session, bundle["watchlist"])` and
`performance.basket(session, trades, window, bundle["watchlist"])` both skip a query that
way.

## curl examples

```bash
# start a session
curl -X POST http://127.0.0.1:5000/api/session/start \
  -H "Content-Type: application/json" \
  -d '{"ticker":"MSFT","start_date":"2023-03-01","end_date":"2023-12-31"}'

# start a session that keeps going past the real data
curl -X POST http://127.0.0.1:5000/api/session/start \
  -H "Content-Type: application/json" \
  -d '{"ticker":"NVDA","start_date":"2024-06-01","end_date":"2024-12-31","simulate_future":true}'

# advance 5 trading days
curl -X POST http://127.0.0.1:5000/api/session/<session_id>/advance \
  -H "Content-Type: application/json" -d '{"days":5}'

# buy 10 shares
curl -X POST http://127.0.0.1:5000/api/session/<session_id>/trade \
  -H "Content-Type: application/json" -d '{"side":"BUY","shares":10}'

# ask the AI coach for a read
curl -X POST http://127.0.0.1:5000/api/session/<session_id>/predict

# move the simulated market with a headline
curl -X POST http://127.0.0.1:5000/api/session/<session_id>/shock \
  -H "Content-Type: application/json" \
  -d '{"headline":"Regulators open a probe","sentiment":-0.7,"magnitude":0.6}'
```

---

## File map

Where each piece of this API actually lives, and the exact handler for each endpoint:

| Path | What it is |
|---|---|
| [`src/app.py`](../src/app.py) | Flask app, page routes (`/`, `/game/<id>`), JSON error handler |
| [`src/routes/game_routes.py`](../src/routes/game_routes.py) | All `/api/*` route handlers (thin — parses the request, calls `engine`) |
| [`src/scripts/game/engine.py`](../src/scripts/game/engine.py) | Game rules: sessions, trades, advancing days, scheduling AI calls |
| [`src/scripts/game/indicators.py`](../src/scripts/game/indicators.py) | SMA/RSI/MACD/volume math and signal detection |
| [`src/scripts/game/patterns.py`](../src/scripts/game/patterns.py) | The pattern syllabus: lesson text per pattern, and which one to teach next |
| [`src/scripts/game/levels.py`](../src/scripts/game/levels.py) | Training levels: catalog, picking a stretch of history, grading trades |
| [`src/scripts/game/news.py`](../src/scripts/game/news.py) | The Daily Ledger: chart-based stories, filler, and daily editions |
| [`src/scripts/game/performance.py`](../src/scripts/game/performance.py) | Portfolio views: the rebased basket comparison and the account equity curve |
| [`src/scripts/game/price_cache.py`](../src/scripts/game/price_cache.py) | In-memory price/indicator cache per ticker |
| [`src/scripts/game/price_source.py`](../src/scripts/game/price_source.py) | Picks real vs simulated prices for a session; the surface `engine.py` talks to |
| [`src/scripts/game/simulation.py`](../src/scripts/game/simulation.py) | Generates a session's private future and applies event shocks to it |
| [`src/scripts/game/events.py`](../src/scripts/game/events.py) | Random market events: asks Gemini, falls back to a local headline, applies the shock |
| [`src/scripts/ingestion/universes.py`](../src/scripts/ingestion/universes.py) | The ticker universe seed data (Nasdaq-100 + extras) |
| [`src/scripts/ingestion/sync_universe.py`](../src/scripts/ingestion/sync_universe.py) | CLI to seed/refresh the `tickers` catalog |
| [`src/scripts/database/database.py`](../src/scripts/database/database.py) | All TigerData (TimescaleDB) reads/writes |
| [`src/scripts/database/schema.sql`](../src/scripts/database/schema.sql) | Table definitions |
| [`src/scripts/api/nessie.py`](../src/scripts/api/nessie.py) | Nessie client (mock or real, same interface) |
| [`src/scripts/api/gemini_mcp_client.py`](../src/scripts/api/gemini_mcp_client.py) | Flask-side MCP client (talks to the MCP server subprocess) |
| [`src/mcp_server/server.py`](../src/mcp_server/server.py) | The MCP server — tools Gemini/Flask can call |
| [`src/mcp_server/gemini_tools.py`](../src/mcp_server/gemini_tools.py) | Gemini prompt building + calling |
| [`src/scripts/ingestion/ingest_prices.py`](../src/scripts/ingestion/ingest_prices.py) | CLI to load historical prices into TigerData |
| [`src/static/js/game.js`](../src/static/js/game.js) | Frontend: calls the API, drives play/pause/speed |
| [`src/static/js/chart-setup.js`](../src/static/js/chart-setup.js) | Frontend: TradingView Lightweight Charts setup (price, RSI and MACD share a crosshair; hover readouts) |
| [`src/templates/index.html`](../src/templates/index.html) | Start-session page |
| [`src/templates/game.html`](../src/templates/game.html) | Game screen |

| Method | Path | Handler |
|---|---|---|
| GET | `/` | [`app.py:84`](../src/app.py#L84) `home()` |
| GET | `/game/<session_id>` | [`app.py:96`](../src/app.py#L96) `game()` |
| GET | `/api/universe` | [`game_routes.py:117`](../src/routes/game_routes.py#L117) `universe()` |
| GET | `/api/ai/status` | [`game_routes.py:136`](../src/routes/game_routes.py#L136) `ai_status()` |
| POST | `/api/session/start` | [`game_routes.py:166`](../src/routes/game_routes.py#L166) `start_session()` |
| GET | `/api/session/<id>/state` | [`game_routes.py:183`](../src/routes/game_routes.py#L183) `session_state()` |
| GET | `/api/session/<id>/basket` | [`game_routes.py:195`](../src/routes/game_routes.py#L195) `session_basket()` |
| POST | `/api/session/<id>/advance` | [`game_routes.py:211`](../src/routes/game_routes.py#L211) `advance()` |
| POST | `/api/session/<id>/trade` | [`game_routes.py:224`](../src/routes/game_routes.py#L224) `trade()` |
| POST | `/api/session/<id>/predict` | [`game_routes.py:238`](../src/routes/game_routes.py#L238) `predict()` |
| POST | `/api/session/<id>/simulate` | [`game_routes.py:247`](../src/routes/game_routes.py#L247) `simulate()` |
| GET | `/api/session/<id>/simulation` | [`game_routes.py:263`](../src/routes/game_routes.py#L263) `simulation_detail()` |
| POST | `/api/session/<id>/shock` | [`game_routes.py:301`](../src/routes/game_routes.py#L301) `inject_shock()` |

---

## Bills and the bank account

Starting cash comes from a Nessie account, and so do that account's standing orders and its
paycheck. As the clock passes each bill's day of the month the money leaves the same cash
balance the player trades with. Cash never goes below zero: a bill the cash cannot cover
makes the bank sell shares (see [Cash never goes below zero](#cash-never-goes-below-zero)).

Relevant files: [`src/scripts/game/expenses.py`](../src/scripts/game/expenses.py) (the rules),
[`src/scripts/api/nessie.py`](../src/scripts/api/nessie.py) (the client),
[`src/scripts/ingestion/seed_nessie.py`](../src/scripts/ingestion/seed_nessie.py) (sandbox setup).

**Nessie base URL is `https://api.nessieisreal.com`** — the API refuses connections on
port 80, which is what made it look like the service was down.

Seed an empty sandbox (creates a customer, a funding account and eight recurring bills):

```bash
uv run python -m scripts.ingestion.seed_nessie
```

Then set `NESSIE_USE_MOCK=false` and `NESSIE_DEFAULT_CUSTOMER_ID=<printed id>` in `.env`.
With `NESSIE_USE_MOCK=true` the same shapes are served from
[`src/temp/nessie_mock_data.json`](../src/temp/nessie_mock_data.json), bills included.

### New fields on the session state object

```json
{
  "portfolio": {
    "bills_paid": 3739.0,
    "salary_earned": 4200.0,
    "trading_return_pct": 0.0,
    "total_return_pct": 4.61,
    "overdrawn": false
  },
  "expenses": {
    "bill_count": 8,
    "monthly_total": 2769.0,
    "paid_to_date": 3739.0,
    "missed_to_date": 0.0,
    "earned_to_date": 4200.0,
    "paycheck": { "amount": 2100.0, "every_days": 14, "monthly": 4550.0,
                  "employer": "Northwind Logistics" },
    "net_monthly": 1781.0,
    "months_of_runway": null,
    "overdrawn": false,
    "upcoming": [
      { "kind": "salary", "label": "Paycheck", "payee": "Northwind Logistics",
        "due_date": "2023-02-24", "amount": 2100.0, "days_away": 7 },
      { "kind": "bill", "label": "Rent", "payee": "Sunrise Apartments",
        "due_date": "2023-03-01", "amount": 1450.0, "days_away": 12 }
    ],
    "charged": [
      { "kind": "bill", "label": "Utilities", "payee": "City Power & Light",
        "due_date": "2023-01-05", "amount": 180.0, "shortfall": 0.0,
        "cash_after": 9820.0, "sold": [] }
    ]
  },
  "bank": {
    "source": "Nessie (local mock)",
    "customer": { "id": "mock-customer-001", "name": "Demo Trader",
                  "city": "Houston", "state": "TX" },
    "account": { "id": "mock-account-001", "nickname": "Trading Cash", "type": "Checking",
                 "number": "••••0001", "balance": 10000.0 },
    "employer": "Northwind Logistics"
  }
}
```

- `total_return_pct` is the account: trading result **minus** bills **plus** paychecks. It is
  the number that decides whether the player went broke.
- `trading_return_pct` takes bills and paychecks back out, so good stock picking still reads
  as good stock picking and a salary never passes for it.
- `months_of_runway` is cash divided by what the bills cost once the paycheck is netted off;
  `null` when the paycheck covers the bills or there are none.
- `paid_to_date` is what actually left the account for bills, `missed_to_date` what they still
  owed after every share was sold, and `earned_to_date` the paychecks.
- `bank` is the Nessie customer behind the session, looked up once per session.
- A trade in `trades` carries `"forced": true` when the bank sold it to cover a bill.

`POST /api/session/<id>/advance` additionally returns **`charged`** — the bills taken and
paychecks paid on this tick, each with the cash left after it and any shares the bank sold:

```json
{ "charged": [
    { "kind": "bill", "label": "Rent", "due_date": "2023-02-01", "amount": 1450.0,
      "shortfall": 0.0, "cash_after": 211.4,
      "sold": [ { "ticker": "AAPL", "shares": 12.0, "price": 143.97, "date": "2023-01-31" } ] },
    { "kind": "salary", "label": "Paycheck", "payee": "Northwind Logistics",
      "due_date": "2023-02-03", "amount": 2100.0, "shortfall": 0.0,
      "cash_after": 2311.4, "sold": [] }
] }
```

Charges are idempotent: `session_expenses` is unique on `(session_id, bill_id, due_date)`, so
re-advancing over a day that already paid rent never pays it twice.

### Cash never goes below zero

Bills and paychecks are settled in date order in one transaction that row-locks the session
and its holdings (`database.settle_cash_flows`), before the new `sim_date` is saved. A
paycheck lands before a bill due the same day. When a bill is due and the cash cannot cover
it, the bank sells whole shares from the largest position down, at the last close on or
before the due date, until the bill is covered; those sells go into `transactions` with
`forced = true`. If the shares run out first, the bill takes the remaining cash, `cash_after`
is `0`, and the rest is stored as `shortfall`.

### Paychecks

`game_sessions.salary_amount` is paid every other Friday, starting with the first Friday
after the session's `start_date`, as a `kind = "salary"` row on the same ledger as the bills.
The start page fills it in from the customer's latest Nessie deposit whose description
contains `Payroll` (the text after the dash is the employer). The mock fixture has one per
customer, and `seed_nessie` creates one in a live sandbox.

### `GET /api/bank/profiles`

The Nessie customers a run can start as, default customer first and at most six, used by the
start page's **Your bank** picker. Cached for five minutes.

```json
{
  "source": "Nessie (local mock)",
  "default_customer_id": "mock-customer-001",
  "pay_interval_days": 14,
  "profiles": [
    {
      "id": "mock-customer-001", "name": "Demo Trader", "city": "Houston", "state": "TX",
      "account": { "id": "mock-account-001", "nickname": "Trading Cash", "type": "Checking",
                   "number": "••••0001", "balance": 10000.0 },
      "bills": { "count": 8, "monthly_total": 2769.0 },
      "paycheck": { "amount": 2100.0, "employer": "Northwind Logistics" }
    }
  ]
}
```

Returns `503` with `{"error": "Nessie is unavailable: ..."}` when the bank cannot be reached.

## Teacher mode and the walkthrough

Both are front-end only — no extra endpoints. `advance` already reports a queued lesson in
`pending_ai` as `{"kind": "PATTERN_LESSON", "ticker", "pattern", "date"}`. When Teacher mode
is on, [`game.js`](../src/static/js/game.js) pauses the clock on that tick, opens the coach
card in a waiting state, and polls `GET /state` until the matching `PATTERN_LESSON` lands in
`ai_feed`. If the AI never answers it gives up after 30s and lets the player carry on; if
Gemini is unreachable the lesson still arrives from the built-in syllabus
([`patterns.py`](../src/scripts/game/patterns.py)) with `source: "offline"`.

The walkthrough is a fourteen-step tour over the real panels, shown automatically on a first
visit and re-openable from **How this works** in the header.

Signals from `/advance` do not pop up as notifications. They go into the **Signals** drawer on
the right edge, whose tab counts the unread ones; the walkthrough points it out. The **News** tab
sits above it. Walkthrough steps for panels the session hides are skipped, and a training level
opens on its briefing card instead of the walkthrough.

## The Daily Ledger (news)

News lives behind the **News** tab on the right edge of the game screen. Opening it pauses the
clock and shows that day's paper; **Earlier** / **Later**, the date picker or the arrow keys page
back through the last 20 trading days. Closing it leaves the clock paused, and **Resume the
clock** appears when it had been running. The tab's badge counts stories newer than the last
edition read.

`GET /state` and `POST /advance` include **`news`**: newest first, at most 40 stories from the
last 15 trading days of every watched symbol ([`news.py`](../src/scripts/game/news.py)). The tab
uses it for the unread badge; it is `[]` in training levels without news.

```json
{ "news": [
    { "id": "3f1c9a0b2e7d", "date": "2023-03-01", "ticker": "MSFT", "company": "Microsoft",
      "section": "Markets", "source": "Ledger Markets Desk",
      "headline": "MSFT tumbles 4.6% in heavy selling",
      "body": "Microsoft closed at $246.27, down 4.6% on the day, on volume about 2.1 times its 20-day average. The Ledger could not tie the move to any single announcement.",
      "major": true },
    { "id": "a81d44c0f913", "date": "2023-03-01", "ticker": null, "company": null,
      "section": "Around town", "source": "Metro Business Journal",
      "headline": "Regional bank opens a branch in Tampa",
      "body": "The branch will offer extended Saturday hours.", "major": false }
] }
```

- **Major** stories are read off the real chart - a move of at least 4%, volume at 2.5x its
  20-day average, the first 52-week closing high or low in more than 10 sessions, a 5- or
  8-session streak - and never given an invented cause, plus the `NEWS_EVENT` and
  `MARKET_SHOCK` headlines from `ai_feed`.
- **Filler** is invented: routine company news and analyst notes (up to two per stock per day),
  clickbait opinion columns, and local business stories with no ticker ("Around town").
- An edition's lead is drawn from the day's loudest stories - big moves and clickbait alike - so
  its position does not give away which story matters.
- Stories are seeded by session, symbol and date, so they never change between refreshes and
  never appear before their date. The page does not show `major`: sorting is the exercise.

### `GET /api/session/<session_id>/news`

Query: `date` (optional, `YYYY-MM-DD`). It snaps to the latest played trading day on or before
that date, so a future date returns today's paper and nothing is shown ahead of the clock.

```json
{
  "date": "2023-03-01", "sim_date": "2023-03-01", "is_latest": true,
  "dates": ["2023-03-01", "2023-02-28", "..."],
  "previous": "2023-02-28", "next": null,
  "lead": { "...": "one story, same shape as in news" },
  "stories": [ { "...": "the rest of the day's stories" } ],
  "closing_bell": [
    { "ticker": "MSFT", "company": "Microsoft", "close": 246.27, "change_pct": -4.6, "volume_ratio": 2.1 }
  ]
}
```

Returns `400` for a training level that does not include news (levels 1-4).

## Training levels

Five short, graded runs on real history, meant to be played before the full simulation. Each one
switches on only the tools it teaches ([`levels.py`](../src/scripts/game/levels.py)).

| # | Level | Tools on screen | A trade follows the rule when | Days | Stocks |
|---|---|---|---|---|---|
| 1 | RSI only | RSI | buy at RSI ≤ 35, sell at RSI ≥ 65 | 90 | 1 |
| 2 | MACD only | MACD | it comes within 3 days of MACD crossing its signal line in that direction | 90 | 1 |
| 3 | The basket | basket chart, equity | after it you hold 3+ stocks from 2+ sectors | 120 | 5 |
| 4 | RSI + MACD | RSI, MACD | a MACD cross within 5 days **and** RSI ≤ 40 (buy) / ≥ 60 (sell) in the last 10 days | 120 | 1 |
| 5 | Everything | every panel, news, AI coach | it matches the rule of level 1, 2 or 4 | 150 | 4 |

- Every level starts with $10,000, no Nessie bills or paychecks, and Teacher mode on.
- Signals, pattern lessons and the Pattern school list only cover the level's own patterns, and
  levels 1-4 schedule no AI predictions or headlines.
- A level refuses `/simulate`; levels 1-4 also refuse `/predict` and `/news` (`400`).
- Each run picks a fresh stretch from 2010 onward where the level's setups actually happen: a buy
  setup before a sell setup for the single-stock levels, RSI extremes somewhere in the basket for
  level 5, and a basket spread across sectors for levels 3 and 5.

### `GET /api/levels`

`{"levels": [...]}`. Each entry has `number`, `count`, `slug`, `title`, `tagline`, `tools`,
`panels`, `patterns`, `brief` (paragraphs), `buy_rule`, `sell_rule`, `trading_days`,
`basket_size` and `goal_labels`.

### `POST /api/levels/<number>/start`

Body (optional): `{"seed": 42}` to replay the same stretch. Returns `201` with the normal session
state; an unknown level number returns `400`.

### `level` on the session state object

`null` for a full simulation. For a level it is the catalog entry plus live grading:

```json
{
  "level": {
    "number": 1, "count": 5, "title": "RSI only", "panels": ["patterns", "rsi", "signals"],
    "trading_days": 90, "days_played": 41, "finished": false,
    "goals": [
      { "id": "finish", "label": "Play all 90 days", "met": false, "progress": "41 of 90 days" },
      { "id": "follow", "label": "Make 2 trades that follow the rule", "met": false, "progress": "1 of 2 so far" },
      { "id": "profit", "label": "End with more money than you started with", "met": false, "progress": "+2.31% so far" }
    ],
    "stars": 0, "passed": false, "benchmark_pct": null, "next_level": 2,
    "trades": [
      { "date": "2018-11-20", "ticker": "ADBE", "side": "BUY", "shares": 18.0, "price": 221.4,
        "followed": true, "why": "RSI was 31. The rule says 35 or lower." }
    ]
  }
}
```

- `panels` names what the page shows: `rsi`, `macd`, `basket`, `trend` (the SMA lines), `volume`,
  `news`, `ai`, `signals` and `patterns`. `sim` and `bank` are never part of a level.
- Stars are awarded when the run finishes, one per goal met. A level is **passed** when it has
  finished with the `follow` goal met. Level 5's third goal is beating an equal-weight buy-and-hold
  of its basket (`benchmark_pct`).
- Like any session, a level finishes on the tick after its last trading day.

Progress (best stars, passed) is kept per browser in `localStorage` under
`tradingTeacher.levels`. The start page locks each level until the one before it is passed and
offers **Unlock all** and **Reset progress**. The results card offers **Replay level** and
**Next level**.
