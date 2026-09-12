CREATE TABLE IF NOT EXISTS stock_prices (
    ticker      TEXT NOT NULL,
    ts          DATE NOT NULL,
    open        NUMERIC(14,4) NOT NULL,
    high        NUMERIC(14,4) NOT NULL,
    low         NUMERIC(14,4) NOT NULL,
    close       NUMERIC(14,4) NOT NULL,
    adj_close   NUMERIC(14,4),
    volume      BIGINT NOT NULL,
    PRIMARY KEY (ticker, ts)
);

-- Chunk interval is set explicitly and generously on purpose. Daily bars for a handful
-- of symbols span decades: at the seven-day default, full history across the catalog
-- produced 3,377 chunks, and every query that could not prune by date ran out of memory
-- in the planner (dropping the table needed a lock per chunk, which the instance refused).
-- Ten-year chunks put the same data in seven.
SELECT create_hypertable(
    'stock_prices', 'ts',
    chunk_time_interval => INTERVAL '10 years',
    if_not_exists => TRUE,
    migrate_data => TRUE
);

-- Heals a database created before the above: only affects chunks created from now on.
SELECT set_chunk_time_interval('stock_prices', INTERVAL '10 years');

CREATE INDEX IF NOT EXISTS idx_stock_prices_ticker_ts ON stock_prices (ticker, ts DESC);

-- A session is a portfolio run over a watchlist of symbols, not a single ticker -
-- which symbol a session holds lives in session_tickers below.
CREATE TABLE IF NOT EXISTS game_sessions (
    id                  UUID PRIMARY KEY,
    user_label          TEXT NOT NULL DEFAULT 'anonymous',
    start_date          DATE NOT NULL,
    end_date            DATE NOT NULL,
    sim_date            DATE NOT NULL,
    starting_cash       NUMERIC(14,2) NOT NULL,
    cash_balance        NUMERIC(14,2) NOT NULL,
    nessie_customer_id  TEXT,
    nessie_account_id   TEXT,
    status              TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','finished')),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The symbols a player chose to trade. sort_order preserves pick order so the first
-- one picked is the default chart focus.
CREATE TABLE IF NOT EXISTS session_tickers (
    session_id  UUID NOT NULL REFERENCES game_sessions(id) ON DELETE CASCADE,
    ticker      TEXT NOT NULL,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    added_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (session_id, ticker)
);

-- Sessions used to carry a single `ticker`. Move those into the watchlist, then drop
-- the column. Guarded so re-running this file after the drop is a no-op.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'game_sessions' AND column_name = 'ticker'
    ) THEN
        INSERT INTO session_tickers (session_id, ticker, sort_order)
        SELECT id, ticker, 0 FROM game_sessions
        ON CONFLICT DO NOTHING;
        ALTER TABLE game_sessions DROP COLUMN ticker;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS portfolio_holdings (
    session_id  UUID NOT NULL REFERENCES game_sessions(id) ON DELETE CASCADE,
    ticker      TEXT NOT NULL,
    shares      NUMERIC(18,6) NOT NULL DEFAULT 0,
    avg_cost    NUMERIC(14,4) NOT NULL DEFAULT 0,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (session_id, ticker)
);

CREATE TABLE IF NOT EXISTS transactions (
    id          BIGSERIAL PRIMARY KEY,
    session_id  UUID NOT NULL REFERENCES game_sessions(id) ON DELETE CASCADE,
    ticker      TEXT NOT NULL,
    trade_date  DATE NOT NULL,
    side        TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
    shares      NUMERIC(18,6) NOT NULL,
    price       NUMERIC(14,4) NOT NULL,
    cash_after  NUMERIC(14,2) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_transactions_session ON transactions (session_id, trade_date);

-- Composite PK: a hypertable's partitioning column must be part of the primary key.
CREATE TABLE IF NOT EXISTS mcp_events (
    id          BIGSERIAL,
    session_id  UUID NOT NULL REFERENCES game_sessions(id) ON DELETE CASCADE,
    event_type  TEXT NOT NULL CHECK (event_type IN ('PREDICTION','NEWS_EVENT','MARKET_SHOCK')),
    ticker      TEXT NOT NULL,
    sim_date    DATE NOT NULL,
    payload     JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id, created_at)
);

-- Existing deployments were created before MARKET_SHOCK existed, and
-- CREATE TABLE IF NOT EXISTS will not widen their check constraint for them.
ALTER TABLE mcp_events DROP CONSTRAINT IF EXISTS mcp_events_event_type_check;
ALTER TABLE mcp_events ADD CONSTRAINT mcp_events_event_type_check
    CHECK (event_type IN ('PREDICTION','NEWS_EVENT','MARKET_SHOCK'));

SELECT create_hypertable(
    'mcp_events', 'created_at',
    chunk_time_interval => INTERVAL '1 year',
    if_not_exists => TRUE,
    migrate_data => TRUE
);

CREATE INDEX IF NOT EXISTS idx_mcp_events_session ON mcp_events (session_id, created_at DESC);


-- ---------------------------------------------------------------------------
-- Ticker universe
-- ---------------------------------------------------------------------------

-- The catalog of symbols the game knows about, independent of whether we have
-- downloaded history for them yet. Seeded from scripts/ingestion/universes.py;
-- stock_prices only ever holds rows for symbols we actually ingested.
CREATE TABLE IF NOT EXISTS tickers (
    ticker        TEXT PRIMARY KEY,
    company_name  TEXT NOT NULL,
    sector        TEXT,
    universe      TEXT NOT NULL DEFAULT 'nasdaq100',
    is_tech       BOOLEAN NOT NULL DEFAULT TRUE,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    added_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tickers_universe ON tickers (universe, ticker);
CREATE INDEX IF NOT EXISTS idx_tickers_tech ON tickers (is_tech) WHERE is_tech;


-- ---------------------------------------------------------------------------
-- Per-user simulated future
-- ---------------------------------------------------------------------------

-- One row per session that has forked off the end of real history. Everything in
-- here belongs to a single player's session and is never written back into
-- stock_prices, so the shared, real dataset stays trustworthy for every player.
CREATE TABLE IF NOT EXISTS user_simulations (
    session_id      UUID NOT NULL REFERENCES game_sessions(id) ON DELETE CASCADE,
    ticker          TEXT NOT NULL,
    fork_date       DATE NOT NULL,
    -- The real close we roll forward from; stored so re-ingesting history can never
    -- silently change a future that a player has already been trading in.
    anchor_price    NUMERIC(14,4) NOT NULL,
    horizon_days    INTEGER NOT NULL DEFAULT 252 CHECK (horizon_days > 0),
    drift           NUMERIC(10,6) NOT NULL DEFAULT 0,
    volatility      NUMERIC(10,6) NOT NULL DEFAULT 0.018,
    mean_reversion  NUMERIC(10,6) NOT NULL DEFAULT 0.15,
    generator       TEXT NOT NULL DEFAULT 'gbm',
    seed            BIGINT NOT NULL,
    last_generated  DATE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- One future per symbol: a session watches several tickers and each one forks on
    -- its own, so the key has to include the ticker.
    PRIMARY KEY (session_id, ticker)
);

-- Earlier versions keyed this table on session_id alone.
ALTER TABLE user_simulations DROP CONSTRAINT IF EXISTS user_simulations_pkey;
ALTER TABLE user_simulations ADD CONSTRAINT user_simulations_pkey
    PRIMARY KEY (session_id, ticker);

-- Deliberately not a hypertable: a session's future is a few hundred cheap rows
-- read by primary key, and the composite PK below is what we actually query on.
CREATE TABLE IF NOT EXISTS simulated_prices (
    session_id  UUID NOT NULL REFERENCES game_sessions(id) ON DELETE CASCADE,
    ticker      TEXT NOT NULL,
    ts          DATE NOT NULL,
    open        NUMERIC(14,4) NOT NULL,
    high        NUMERIC(14,4) NOT NULL,
    low         NUMERIC(14,4) NOT NULL,
    close       NUMERIC(14,4) NOT NULL,
    volume      BIGINT NOT NULL,
    -- Set when a Gemini shock bent this bar, so a headline can be traced to its candles.
    event_id    BIGINT,
    PRIMARY KEY (session_id, ticker, ts)
);

CREATE INDEX IF NOT EXISTS idx_simulated_prices_session
    ON simulated_prices (session_id, ticker, ts);
