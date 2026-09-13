import os
from pathlib import Path

from dotenv import load_dotenv

SRC_DIR = Path(__file__).resolve().parent
REPO_ROOT = SRC_DIR.parent

load_dotenv(REPO_ROOT / ".env")
load_dotenv(REPO_ROOT / "tiger-cloud-stocksave-credentials.env")
##two envs for testing 

def _build_dsn() -> str:
    dsn = os.getenv("TIMESCALE_SERVICE_URL")
    if dsn:
        return dsn
    host = os.getenv("PGHOST")
    if not host:
        raise RuntimeError(
            "No database credentials found. Set TIMESCALE_SERVICE_URL (or the PG* vars) "
            "in .env or tiger-cloud-stocksave-credentials.env - see .env.example."
        )
    return (
        f"postgres://{os.getenv('PGUSER', 'tsdbadmin')}:{os.getenv('PGPASSWORD', '')}"
        f"@{host}:{os.getenv('PGPORT', '5432')}/{os.getenv('PGDATABASE', 'tsdb')}"
        f"?sslmode={os.getenv('PGSSLMODE', 'require')}"
    )


DATABASE_DSN = _build_dsn()

# --- the connection pool --------------------------------------------------
# Every request thread shares one pool, and a checkout that cannot be satisfied is what
# turns a burst of page loads (or several tabs) into a clock that never moves. The pool
# is sized here rather than in the access layer so it can be tuned without a code change,
# and every acquire has a deadline: waiting forever is never the right answer.
DB_POOL_MIN = int(os.getenv("DB_POOL_MIN", "2"))
DB_POOL_MAX = int(os.getenv("DB_POOL_MAX", "12"))
# Seconds to establish a connection. Without it a black-holed network leaves a thread
# waiting on a socket for the operating system's default, which is measured in minutes.
DB_CONNECT_TIMEOUT = int(os.getenv("DB_CONNECT_TIMEOUT", "10"))
# Ceiling on a single statement. A query that runs away holds a connection out of the
# pool for as long as it likes, and that is how a slow query becomes an outage.
DB_STATEMENT_TIMEOUT_MS = int(os.getenv("DB_STATEMENT_TIMEOUT_MS", "20000"))
# How long a thread may wait for a free connection before being told to retry.
DB_POOL_WAIT_SECONDS = float(os.getenv("DB_POOL_WAIT_SECONDS", "10"))
# A pooled connection that has been sitting idle may already be dead - a server restart, a
# dropped socket, a load balancer reaping it - and nothing says so until a query is run on
# it. Past this many seconds of idleness the connection gets one cheap round trip before
# it is trusted with the player's next click.
DB_POOL_IDLE_PING_SECONDS = float(os.getenv("DB_POOL_IDLE_PING_SECONDS", "30"))

GEMINI_API_KEYS = [
    key for key in (os.getenv("GEMINI_API_KEY"), os.getenv("GEMINI_API_KEY2")) if key
]
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")

# https, not http: the API refuses connections on port 80, which is what made it look
# like the service was down.
NESSIE_BASE_URL = os.getenv("NESSIE_BASE_URL", "https://api.nessieisreal.com").rstrip("/")
NESSIE_API_KEY = os.getenv("NESSIE_API_KEY", "")
NESSIE_USE_MOCK = os.getenv("NESSIE_USE_MOCK", "true").lower() == "true"
NESSIE_DEFAULT_CUSTOMER_ID = os.getenv("NESSIE_DEFAULT_CUSTOMER_ID", "mock-customer-001")
NESSIE_FIXTURE_PATH = SRC_DIR / "temp" / "nessie_mock_data.json"

RANDOM_EVENT_PROBABILITY = float(os.getenv("RANDOM_EVENT_PROBABILITY", "0.15"))
PREDICTION_INTERVAL_DAYS = int(os.getenv("PREDICTION_INTERVAL_DAYS", "10"))
MIN_DAYS_BETWEEN_EVENTS = int(os.getenv("MIN_DAYS_BETWEEN_EVENTS", "3"))

# How many symbols a single session may watch. Each one costs a price frame per tick and
# a chart on screen, so the cap is what keeps a tick responsive.
MAX_WATCHLIST = int(os.getenv("MAX_WATCHLIST", "10"))

# --- the simulated future -------------------------------------------------
# A session can fork off the end of real history into its own generated series.
# Every knob is env-overridable so the simulation can be tuned live during a demo.
SIMULATION_HORIZON_DAYS = int(os.getenv("SIMULATION_HORIZON_DAYS", "252"))
SIMULATION_VOLATILITY = float(os.getenv("SIMULATION_VOLATILITY", "0.018"))
SIMULATION_MEAN_REVERSION = float(os.getenv("SIMULATION_MEAN_REVERSION", "0.15"))

# How far a maximally-confident, maximally-bullish headline (sentiment 1 x magnitude 1)
# reprices the forward curve in total. 0.18 keeps even a "market-moving" story under a
# fifth of the price, spread across several sessions.
SHOCK_IMPACT_SCALE = float(os.getenv("SHOCK_IMPACT_SCALE", "0.18"))

# Share of the move that is priced on the day the story breaks; the remainder bleeds in
# as extra drift over the following sessions.
SHOCK_IMMEDIATE_SHARE = float(os.getenv("SHOCK_IMMEDIATE_SHARE", "0.4"))
SHOCK_PROBABILITY = float(os.getenv("SHOCK_PROBABILITY", "0.10"))
SHOCK_MIN_DAYS_BETWEEN = int(os.getenv("SHOCK_MIN_DAYS_BETWEEN", "5"))
SHOCK_DECAY_DAYS = int(os.getenv("SHOCK_DECAY_DAYS", "10"))

# How often a generated story is about more than the one company in focus. Without these
# a basket of ten names still moves one line at a time, which does not look like a market:
# real news is usually sector or macro news that drags every correlated name with it.
SHOCK_SECTOR_PROBABILITY = float(os.getenv("SHOCK_SECTOR_PROBABILITY", "0.35"))
SHOCK_MARKET_PROBABILITY = float(os.getenv("SHOCK_MARKET_PROBABILITY", "0.12"))

# How far a shared story varies between the symbols it hits, as a fraction of magnitude.
# 0 would move every name by exactly the same amount, which reads as a bug; this keeps
# them correlated but not identical - the high-beta names swing harder, as they should.
SHOCK_PEER_SPREAD = float(os.getenv("SHOCK_PEER_SPREAD", "0.45"))

# When Gemini is unreachable (no API key, no network in the room), fall back to a
# locally generated headline so the simulation still demonstrates random events.
SIMULATION_OFFLINE_EVENTS = os.getenv("SIMULATION_OFFLINE_EVENTS", "true").lower() == "true"

# --- pattern curriculum ---------------------------------------------------
# When a signal fires in one of the player's own symbols, the coach teaches the pattern
# behind it. A pattern the player has never been shown is taught immediately; one they
# have already met waits PATTERN_REPEAT_GAP_DAYS sessions before being revisited, which
# is what keeps the feed teaching new material instead of the same card every week.
PATTERN_LESSONS = os.getenv("PATTERN_LESSONS", "true").lower() == "true"
PATTERN_REPEAT_GAP_DAYS = int(os.getenv("PATTERN_REPEAT_GAP_DAYS", "20"))
# Chance of a refresher lesson when every pattern on screen has already been taught.
PATTERN_REPEAT_PROBABILITY = float(os.getenv("PATTERN_REPEAT_PROBABILITY", "0.5"))
