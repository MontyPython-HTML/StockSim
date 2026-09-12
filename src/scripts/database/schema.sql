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

SELECT create_hypertable('stock_prices', 'ts', if_not_exists => TRUE, migrate_data => TRUE);

CREATE INDEX IF NOT EXISTS idx_stock_prices_ticker_ts ON stock_prices (ticker, ts DESC);

CREATE TABLE IF NOT EXISTS game_sessions (
    id                  UUID PRIMARY KEY,
    user_label          TEXT NOT NULL DEFAULT 'anonymous',
    ticker              TEXT NOT NULL,
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
    event_type  TEXT NOT NULL CHECK (event_type IN ('PREDICTION','NEWS_EVENT')),
    ticker      TEXT NOT NULL,
    sim_date    DATE NOT NULL,
    payload     JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id, created_at)
);

SELECT create_hypertable('mcp_events', 'created_at', if_not_exists => TRUE, migrate_data => TRUE);

CREATE INDEX IF NOT EXISTS idx_mcp_events_session ON mcp_events (session_id, created_at DESC);
