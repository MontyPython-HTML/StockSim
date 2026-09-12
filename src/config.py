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
INDICATOR_LOOKBACK_DAYS = 200
