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

GEMINI_API_KEYS = [
    key for key in (os.getenv("GEMINI_API_KEY"), os.getenv("GEMINI_API_KEY2")) if key
]
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")

NESSIE_BASE_URL = os.getenv("NESSIE_BASE_URL", "http://api.nessieisreal.com").rstrip("/")
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
