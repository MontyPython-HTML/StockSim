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

---

## Page routes

### `GET /`

Renders the "start a session" landing page. Passes the list of ingested tickers (from TigerData) and the default Nessie funding customer into the template.

### `GET /game/<session_id>`

Renders the game screen for an existing session. Redirects to `/` if the session id doesn't exist.

---

## The session state object

Several endpoints return the same shape — the full current state of one game session. It looks like this:

```json
{
  "session_id": "29f42d68-23ab-4881-86de-ef6f4baf0f3d",
  "ticker": "MSFT",
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
    { "date": "2023-03-01", "side": "BUY", "shares": 20.0, "price": 246.27 }
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
  ]
}
```

Notes:
- `today` and `chart` come from [indicators.py](../src/scripts/game/indicators.py) — see that file (or ask about "predictors") for what `sma20`/`sma50`/`rsi14`/`macd*` mean.
- `chart` is a rolling window (default last 180 trading days through `sim_date`), meant for feeding straight into a chart library.
- `ai_feed` entries are whatever the Gemini MCP server has logged so far for this session (`event_type` is `"PREDICTION"` or `"NEWS_EVENT"`) — see [server.py](../src/mcp_server/server.py).
- `status` is `"active"` until `sim_date` reaches `end_date` (or the ticker's last stored trading day), then it flips to `"finished"` and further `/advance` calls become no-ops.

---

## `GET /api/tickers`

Lists every ticker currently ingested into TigerData, with its stored date range.

**Response `200`**
```json
{
  "tickers": [
    { "ticker": "AAPL", "first_day": "2022-01-03", "last_day": "2024-12-31", "row_count": 753 },
    { "ticker": "MSFT", "first_day": "2022-01-03", "last_day": "2024-12-31", "row_count": 753 }
  ]
}
```

---

## `POST /api/session/start`

Starts a new game session: picks the first available trading day on/after `start_date`, pulls a starting cash balance from Nessie, and creates the session row in TigerData.

**Request body**
```json
{
  "ticker": "MSFT",
  "start_date": "2023-03-01",
  "end_date": "2023-12-31",
  "nessie_customer_id": "mock-customer-001"
}
```
- `ticker` — required.
- `start_date`, `end_date` — required, `YYYY-MM-DD`.
- `nessie_customer_id` — optional; defaults to `NESSIE_DEFAULT_CUSTOMER_ID` (`.env`).

**Response `201`** — the [session state object](#the-session-state-object), plus a `funding` block:
```json
{
  "...": "...(full state object)",
  "funding": {
    "source": "Nessie (local mock)",
    "account_nickname": "Trading Cash",
    "account_type": "Checking",
    "balance": 10000.0
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
- `days` — optional, default `1`. Advances this many trading days per call (the frontend batches more days per call at higher playback speeds instead of ticking faster, since each call is a network round trip).
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
  "pending_ai": ["PREDICTION"]
}
```
- `signals` — any technical signals that *fired on this call* (state changes only, e.g. a crossover happening — not "RSI is currently above 70").
- `pending_ai` — which AI event kinds were just scheduled in the background (`"PREDICTION"` and/or `"NEWS_EVENT"`). The AI call itself runs asynchronously via the Gemini MCP server and is **not** in the response yet — poll `GET /state` a few seconds later and check `ai_feed` for the result. If the MCP server is unavailable, `pending_ai` items simply never turn into `ai_feed` entries; the simulation itself is unaffected.
- Advancing past `end_date` is a no-op that just returns the final state with `status: "finished"` and empty `signals`/`pending_ai`.

**Errors (`400`)** — `Session not found`.

---

## `POST /api/session/<session_id>/trade`

Executes a buy or sell at the current `sim_date`'s closing price.

**Request body**
```json
{ "side": "BUY", "shares": 10 }
```
- `side` — `"BUY"` or `"SELL"` (case-insensitive).
- `shares` — number, must be `> 0`.

**Response `200`** — the [session state object](#the-session-state-object) with updated `portfolio`/`trades`.

**Errors (`400`)**
- `side must be BUY or SELL`
- `shares must be greater than zero` / `shares must be a number`
- `Not enough cash: that costs $X and you have $Y` (BUY)
- `You only hold N shares of <TICKER>` (SELL)
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

## Quick reference

| Method | Path                              | Purpose                                   |
|--------|-----------------------------------|--------------------------------------------|
| GET    | `/`                                | Start-session landing page (HTML)          |
| GET    | `/game/<session_id>`               | Game screen (HTML)                         |
| GET    | `/api/tickers`                     | List ingested tickers                      |
| POST   | `/api/session/start`               | Start a new session                        |
| GET    | `/api/session/<id>/state`          | Fetch current session state                |
| POST   | `/api/session/<id>/advance`        | Advance N trading days                     |
| POST   | `/api/session/<id>/trade`          | Buy/sell at today's close                  |
| POST   | `/api/session/<id>/predict`        | Ask the AI coach for a prediction now      |

## curl examples

```bash
# start a session
curl -X POST http://127.0.0.1:5000/api/session/start \
  -H "Content-Type: application/json" \
  -d '{"ticker":"MSFT","start_date":"2023-03-01","end_date":"2023-12-31"}'

# advance 5 trading days
curl -X POST http://127.0.0.1:5000/api/session/<session_id>/advance \
  -H "Content-Type: application/json" -d '{"days":5}'

# buy 10 shares
curl -X POST http://127.0.0.1:5000/api/session/<session_id>/trade \
  -H "Content-Type: application/json" -d '{"side":"BUY","shares":10}'

# ask the AI coach for a read
curl -X POST http://127.0.0.1:5000/api/session/<session_id>/predict
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
| [`src/scripts/game/price_cache.py`](../src/scripts/game/price_cache.py) | In-memory price/indicator cache per ticker |
| [`src/scripts/database/database.py`](../src/scripts/database/database.py) | All TigerData (TimescaleDB) reads/writes |
| [`src/scripts/database/schema.sql`](../src/scripts/database/schema.sql) | Table definitions |
| [`src/scripts/api/nessie.py`](../src/scripts/api/nessie.py) | Nessie client (mock or real, same interface) |
| [`src/scripts/api/gemini_mcp_client.py`](../src/scripts/api/gemini_mcp_client.py) | Flask-side MCP client (talks to the MCP server subprocess) |
| [`src/mcp_server/server.py`](../src/mcp_server/server.py) | The MCP server — tools Gemini/Flask can call |
| [`src/mcp_server/gemini_tools.py`](../src/mcp_server/gemini_tools.py) | Gemini prompt building + calling |
| [`src/scripts/ingestion/ingest_prices.py`](../src/scripts/ingestion/ingest_prices.py) | CLI to load historical prices into TigerData |
| [`src/static/js/game.js`](../src/static/js/game.js) | Frontend: calls the API, drives play/pause/speed |
| [`src/static/js/chart-setup.js`](../src/static/js/chart-setup.js) | Frontend: Chart.js setup |
| [`src/templates/index.html`](../src/templates/index.html) | Start-session page |
| [`src/templates/game.html`](../src/templates/game.html) | Game screen |

| Method | Path | Handler |
|---|---|---|
| GET | `/` | [`app.py:37`](../src/app.py#L37) `home()` |
| GET | `/game/<session_id>` | [`app.py:46`](../src/app.py#L46) `game()` |
| GET | `/api/tickers` | [`game_routes.py:25`](../src/routes/game_routes.py#L25) `tickers()` |
| POST | `/api/session/start` | [`game_routes.py:30`](../src/routes/game_routes.py#L30) `start_session()` |
| GET | `/api/session/<id>/state` | [`game_routes.py:45`](../src/routes/game_routes.py#L45) `session_state()` |
| POST | `/api/session/<id>/advance` | [`game_routes.py:50`](../src/routes/game_routes.py#L50) `advance()` |
| POST | `/api/session/<id>/trade` | [`game_routes.py:57`](../src/routes/game_routes.py#L57) `trade()` |
| POST | `/api/session/<id>/predict` | [`game_routes.py:67`](../src/routes/game_routes.py#L67) `predict()` |
