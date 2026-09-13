CREATE TABLE IF NOT EXISTS stock_prices (
    ticker      TEXT NOT NULL,
    ts          DATE NOT NULL,
    open        NUMERIC(14,4) NOT NULL,
    high        NUMERIC(14,4) NOT NULL,
    low         NUMERIC(14,4) NOT NULL,
    close       NUMERIC(14,4) NOT NULL,
    adj_close   NUMERIC(14,4),
    dividend    NUMERIC(14,6) NOT NULL DEFAULT 0,
    volume      BIGINT NOT NULL,
    PRIMARY KEY (ticker, ts)
);

SELECT create_hypertable(
    'stock_prices', 'ts',
    chunk_time_interval => INTERVAL '10 years',
    if_not_exists => TRUE,
    migrate_data => TRUE
);

SELECT set_chunk_time_interval('stock_prices', INTERVAL '10 years');

CREATE INDEX IF NOT EXISTS idx_stock_prices_ticker_ts ON stock_prices (ticker, ts DESC);

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

ALTER TABLE game_sessions ADD COLUMN IF NOT EXISTS finances_enabled BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE game_sessions ADD COLUMN IF NOT EXISTS level SMALLINT;


CREATE TABLE IF NOT EXISTS session_tickers (
    session_id  UUID NOT NULL REFERENCES game_sessions(id) ON DELETE CASCADE,
    ticker      TEXT NOT NULL,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    added_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (session_id, ticker)
);

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

CREATE TABLE IF NOT EXISTS mcp_events (
    id          BIGSERIAL,
    session_id  UUID NOT NULL REFERENCES game_sessions(id) ON DELETE CASCADE,
    event_type  TEXT NOT NULL CHECK (event_type IN ('PREDICTION','NEWS_EVENT','MARKET_SHOCK','PATTERN_LESSON')),
    ticker      TEXT NOT NULL,
    sim_date    DATE NOT NULL,
    payload     JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id, created_at)
);

ALTER TABLE mcp_events DROP CONSTRAINT IF EXISTS mcp_events_event_type_check;
ALTER TABLE mcp_events ADD CONSTRAINT mcp_events_event_type_check
    CHECK (event_type IN ('PREDICTION','NEWS_EVENT','MARKET_SHOCK','PATTERN_LESSON'));

SELECT create_hypertable(
    'mcp_events', 'created_at',
    chunk_time_interval => INTERVAL '1 year',
    if_not_exists => TRUE,
    migrate_data => TRUE
);

CREATE INDEX IF NOT EXISTS idx_mcp_events_session ON mcp_events (session_id, created_at DESC);



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



CREATE TABLE IF NOT EXISTS user_simulations (
    session_id      UUID NOT NULL REFERENCES game_sessions(id) ON DELETE CASCADE,
    ticker          TEXT NOT NULL,
    fork_date       DATE NOT NULL,
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
    PRIMARY KEY (session_id, ticker)
);

ALTER TABLE user_simulations DROP CONSTRAINT IF EXISTS user_simulations_pkey;
ALTER TABLE user_simulations ADD CONSTRAINT user_simulations_pkey
    PRIMARY KEY (session_id, ticker);

CREATE TABLE IF NOT EXISTS simulated_prices (
    session_id  UUID NOT NULL REFERENCES game_sessions(id) ON DELETE CASCADE,
    ticker      TEXT NOT NULL,
    ts          DATE NOT NULL,
    open        NUMERIC(14,4) NOT NULL,
    high        NUMERIC(14,4) NOT NULL,
    low         NUMERIC(14,4) NOT NULL,
    close       NUMERIC(14,4) NOT NULL,
    volume      BIGINT NOT NULL,
    event_id    BIGINT,
    PRIMARY KEY (session_id, ticker, ts)
);

CREATE INDEX IF NOT EXISTS idx_simulated_prices_session
    ON simulated_prices (session_id, ticker, ts);

CREATE TABLE IF NOT EXISTS session_expenses (
    id          BIGSERIAL PRIMARY KEY,
    session_id  UUID NOT NULL REFERENCES game_sessions(id) ON DELETE CASCADE,
    bill_id     TEXT NOT NULL,
    label       TEXT NOT NULL,
    payee       TEXT,
    due_date    DATE NOT NULL,
    amount      NUMERIC(14,2) NOT NULL,
    cash_after  NUMERIC(14,2) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (session_id, bill_id, due_date)
);

CREATE INDEX IF NOT EXISTS idx_session_expenses_session
    ON session_expenses (session_id, due_date);

ALTER TABLE session_expenses ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'bill';
ALTER TABLE session_expenses ADD COLUMN IF NOT EXISTS shortfall NUMERIC(14,2) NOT NULL DEFAULT 0;
ALTER TABLE session_expenses DROP CONSTRAINT IF EXISTS session_expenses_kind_check;
ALTER TABLE session_expenses ADD CONSTRAINT session_expenses_kind_check
    CHECK (kind IN ('bill', 'salary'));

ALTER TABLE game_sessions ADD COLUMN IF NOT EXISTS salary_amount NUMERIC(14,2) NOT NULL DEFAULT 0;

ALTER TABLE transactions ADD COLUMN IF NOT EXISTS forced BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE stock_prices ADD COLUMN IF NOT EXISTS dividend NUMERIC(14,6) NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS dividend_payments (
    id          BIGSERIAL PRIMARY KEY,
    session_id  UUID NOT NULL REFERENCES game_sessions(id) ON DELETE CASCADE,
    ticker      TEXT NOT NULL,
    ex_date     DATE NOT NULL,
    shares      NUMERIC(18,6) NOT NULL,
    per_share   NUMERIC(14,6) NOT NULL,
    amount      NUMERIC(14,2) NOT NULL,
    cash_after  NUMERIC(14,2) NOT NULL,
    UNIQUE (session_id, ticker, ex_date)
);

CREATE INDEX IF NOT EXISTS idx_dividend_payments_session
    ON dividend_payments (session_id, ex_date);

ALTER TABLE game_sessions ADD COLUMN IF NOT EXISTS reinvest_dividends BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE transactions ADD COLUMN IF NOT EXISTS reinvested BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE dividend_payments ADD COLUMN IF NOT EXISTS reinvested_shares NUMERIC(18,6) NOT NULL DEFAULT 0;
ALTER TABLE dividend_payments ADD COLUMN IF NOT EXISTS reinvest_price NUMERIC(14,4);
