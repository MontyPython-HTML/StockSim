import os
from pathlib import Path

from dotenv import load_dotenv

SRC_DIR = Path(__file__).resolve().parent
REPO_ROOT = SRC_DIR.parent

load_dotenv(REPO_ROOT / ".env")
load_dotenv(REPO_ROOT / "tiger-cloud-stocksave-credentials.env")

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

DB_POOL_MIN = int(os.getenv("DB_POOL_MIN", "2"))
DB_POOL_MAX = int(os.getenv("DB_POOL_MAX", "12"))
DB_CONNECT_TIMEOUT = int(os.getenv("DB_CONNECT_TIMEOUT", "10"))
DB_STATEMENT_TIMEOUT_MS = int(os.getenv("DB_STATEMENT_TIMEOUT_MS", "20000"))
DB_POOL_WAIT_SECONDS = float(os.getenv("DB_POOL_WAIT_SECONDS", "10"))
DB_POOL_IDLE_PING_SECONDS = float(os.getenv("DB_POOL_IDLE_PING_SECONDS", "30"))

GEMINI_API_KEYS = [
    key for key in (os.getenv("GEMINI_API_KEY"), os.getenv("GEMINI_API_KEY2")) if key
]
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")

NESSIE_BASE_URL = os.getenv("NESSIE_BASE_URL", "https://api.nessieisreal.com").rstrip("/")
NESSIE_API_KEY = os.getenv("NESSIE_API_KEY", "")
NESSIE_USE_MOCK = os.getenv("NESSIE_USE_MOCK", "true").lower() == "true"
NESSIE_DEFAULT_CUSTOMER_ID = os.getenv("NESSIE_DEFAULT_CUSTOMER_ID", "mock-customer-001")
NESSIE_FIXTURE_PATH = SRC_DIR / "temp" / "nessie_mock_data.json"

RANDOM_EVENT_PROBABILITY = float(os.getenv("RANDOM_EVENT_PROBABILITY", "0.15"))
PREDICTION_INTERVAL_DAYS = int(os.getenv("PREDICTION_INTERVAL_DAYS", "10"))
MIN_DAYS_BETWEEN_EVENTS = int(os.getenv("MIN_DAYS_BETWEEN_EVENTS", "3"))

MAX_WATCHLIST = int(os.getenv("MAX_WATCHLIST", "10"))

SIMULATION_HORIZON_DAYS = int(os.getenv("SIMULATION_HORIZON_DAYS", "252"))
SIMULATION_VOLATILITY = float(os.getenv("SIMULATION_VOLATILITY", "0.018"))
SIMULATION_MEAN_REVERSION = float(os.getenv("SIMULATION_MEAN_REVERSION", "0.15"))

SHOCK_IMPACT_SCALE = float(os.getenv("SHOCK_IMPACT_SCALE", "0.18"))

SHOCK_IMMEDIATE_SHARE = float(os.getenv("SHOCK_IMMEDIATE_SHARE", "0.4"))
SHOCK_PROBABILITY = float(os.getenv("SHOCK_PROBABILITY", "0.10"))
SHOCK_MIN_DAYS_BETWEEN = int(os.getenv("SHOCK_MIN_DAYS_BETWEEN", "5"))
SHOCK_DECAY_DAYS = int(os.getenv("SHOCK_DECAY_DAYS", "10"))

SHOCK_SECTOR_PROBABILITY = float(os.getenv("SHOCK_SECTOR_PROBABILITY", "0.35"))
SHOCK_MARKET_PROBABILITY = float(os.getenv("SHOCK_MARKET_PROBABILITY", "0.12"))

SHOCK_PEER_SPREAD = float(os.getenv("SHOCK_PEER_SPREAD", "0.45"))

SIMULATION_OFFLINE_EVENTS = os.getenv("SIMULATION_OFFLINE_EVENTS", "true").lower() == "true"

PATTERN_LESSONS = os.getenv("PATTERN_LESSONS", "true").lower() == "true"
PATTERN_REPEAT_GAP_DAYS = int(os.getenv("PATTERN_REPEAT_GAP_DAYS", "20"))
PATTERN_REPEAT_PROBABILITY = float(os.getenv("PATTERN_REPEAT_PROBABILITY", "0.5"))
