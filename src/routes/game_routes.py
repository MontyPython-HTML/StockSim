from datetime import date, datetime, timedelta
from uuid import UUID

from flask import Blueprint, jsonify, request
from werkzeug.exceptions import HTTPException

import config
from scripts.api import gemini_mcp_client
from scripts.database import database
from scripts.game import engine, events, performance, price_source
from scripts.game.engine import AIUnavailable, GameError

api = Blueprint("api", __name__, url_prefix="/api")


@api.before_request
def _reject_malformed_session_id():
    """A malformed id is a missing session, not a driver crash.

    Every /session/<session_id> route hands the id straight to a query, and the column is
    a uuid, so "not-a-uuid" made psycopg2 raise InvalidTextRepresentation -- an HTML 500
    that the front end cannot even parse as JSON.
    """
    session_id = (request.view_args or {}).get("session_id")
    if session_id is not None:
        try:
            UUID(str(session_id))
        except (ValueError, AttributeError, TypeError):
            raise GameError("Session not found")


@api.app_errorhandler(HTTPException)
def _json_http_error(error: HTTPException):
    """Answer API errors in JSON; leave the HTML pages alone.

    The front end parses every response with JSON.parse, so an HTML error page surfaces as
    a baffling syntax error instead of the actual problem.
    """
    if not request.path.startswith("/api/"):
        return error
    return jsonify({"error": error.description or error.name}), error.code


# --- request parsing ------------------------------------------------------


def _parse_date(value: str, field: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise GameError(f"{field} must be a date formatted YYYY-MM-DD")


def _as_date(value) -> date:
    """Accept either a date (from the driver) or an ISO string (from JSON)."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value), "%Y-%m-%d").date()


def _optional_int(value, field: str) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise GameError(f"{field} must be an integer")


def _optional_float(value, field: str) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise GameError(f"{field} must be a number")


def _focus_argument(body: dict) -> str | None:
    return (request.args.get("focus") or body.get("focus") or "").strip().upper() or None


def _day_count(value) -> int:
    """Days to move the clock, validated like every other numeric field.

    `int(body.get("days", 1))` let a non-numeric string through as a 500, and silently
    turned days=0 into a one-day advance - the opposite of what was asked for.
    """
    days = _optional_int(value, "days")
    if days is None:
        return 1
    if days < 1:
        raise GameError("days must be at least 1")
    return days


# --- errors ---------------------------------------------------------------


@api.errorhandler(GameError)
def handle_game_error(error: GameError):
    return jsonify({"error": str(error)}), 400


@api.errorhandler(AIUnavailable)
def handle_ai_error(error: AIUnavailable):
    # 503 + the real reason: the button press that triggered this has a player watching,
    # so a missing key or a dead MCP subprocess must say so out loud.
    return jsonify({"error": str(error)}), 503


# --- catalog --------------------------------------------------------------


@api.get("/universe")
def universe():
    """Every symbol we know about, not just the ones with history downloaded.

    `has_data` is what the UI keys off of: a known ticker with no rows is a one-command
    ingest away, not an error.
    """
    tech_only = request.args.get("tech_only", "").lower() in ("1", "true", "yes")
    return jsonify(
        {
            "universe": request.args.get("universe") or None,
            "stats": database.universe_stats(),
            "tickers": database.list_universe(
                universe=request.args.get("universe") or None, tech_only=tech_only
            ),
        }
    )


@api.get("/ai/status")
def ai_status():
    """Whether the AI coach can answer, and why not when it cannot.

    Only probes the MCP subprocess when a key exists, so the default (keyless) install
    does not pay a subprocess spawn on every page load.
    """
    if not config.GEMINI_API_KEYS:
        return jsonify(
            {
                "configured": False,
                "model": config.GEMINI_MODEL,
                "available": False,
                "detail": f"{gemini_mcp_client.NO_KEY_HINT} {gemini_mcp_client.KEY_FIX_HINT}",
            }
        )
    available, detail = gemini_mcp_client.probe()
    return jsonify(
        {
            "configured": True,
            "model": config.GEMINI_MODEL,
            "available": available,
            "detail": detail,
        }
    )


# --- sessions -------------------------------------------------------------


@api.post("/session/start")
def start_session():
    body = request.get_json(silent=True) or {}
    state = engine.start_session(
        tickers=body.get("tickers"),
        start_date=_parse_date(body.get("start_date"), "start_date"),
        end_date=_parse_date(body.get("end_date"), "end_date"),
        nessie_customer_id=body.get("nessie_customer_id"),
        simulate_future=bool(body.get("simulate_future")),
        horizon_days=_optional_int(body.get("horizon_days"), "horizon_days"),
        drift=_optional_float(body.get("drift"), "drift"),
        volatility=_optional_float(body.get("volatility"), "volatility"),
        seed=_optional_int(body.get("seed"), "seed"),
    )
    return jsonify(state), 201


@api.get("/session/<session_id>/state")
def session_state(session_id: str):
    return jsonify(
        engine.get_state(
            session_id,
            focus=(request.args.get("focus") or "").strip().upper() or None,
            window_days=_optional_int(request.args.get("window"), "window")
            or engine.DEFAULT_CHART_WINDOW,
        )
    )


@api.get("/session/<session_id>/basket")
def session_basket(session_id: str):
    """Every watched symbol rebased to 100, plus the account's equity over the window."""
    bundle = database.load_session_bundle(session_id)
    session = bundle["session"]
    if not session:
        raise GameError("Session not found")
    return jsonify(
        performance.basket(
            session,
            bundle["trades"],
            _optional_int(request.args.get("window"), "window"),
            # The watchlist is already in the bundle this request just loaded.
            bundle.get("watchlist"),
            bundle.get("expenses"),
        )
    )


@api.post("/session/<session_id>/advance")
def advance(session_id: str):
    body = request.get_json(silent=True) or {}
    return jsonify(
        engine.advance_day(
            session_id,
            days=_day_count(body.get("days")),
            with_ai=body.get("with_ai", True),
            focus=_focus_argument(body),
        )
    )


@api.post("/session/<session_id>/trade")
def trade(session_id: str):
    body = request.get_json(silent=True) or {}
    return jsonify(
        engine.execute_trade(
            session_id,
            ticker=body.get("ticker", ""),
            side=body.get("side", ""),
            shares=body.get("shares", 0),
            focus=_focus_argument(body),
        )
    )


@api.post("/session/<session_id>/predict")
def predict(session_id: str):
    body = request.get_json(silent=True) or {}
    return jsonify({"prediction": engine.request_prediction(session_id, body.get("ticker"))})


# --- simulated future -----------------------------------------------------


@api.post("/session/<session_id>/simulate")
def simulate(session_id: str):
    """Fork this session's whole watchlist onto generated futures."""
    body = request.get_json(silent=True) or {}
    return jsonify(
        engine.fork_simulation(
            session_id,
            focus=_focus_argument(body),
            horizon_days=_optional_int(body.get("horizon_days"), "horizon_days"),
            drift=_optional_float(body.get("drift"), "drift"),
            volatility=_optional_float(body.get("volatility"), "volatility"),
            seed=_optional_int(body.get("seed"), "seed"),
        )
    ), 201


@api.get("/session/<session_id>/simulation")
def simulation_detail(session_id: str):
    """The fork parameters, how far each future runs, and every shock applied."""
    session = database.get_session(session_id)
    if not session:
        raise GameError("Session not found")

    rows = database.get_simulations(session_id)
    focus = (request.args.get("focus") or "").strip().upper()
    if not focus or not any(row["ticker"] == focus for row in rows):
        focus = rows[0]["ticker"] if rows else None

    payload = {
        "active": bool(rows),
        "session_id": session_id,
        "focus": focus,
        "tickers": [row["ticker"] for row in rows],
    }
    if not rows:
        return jsonify(payload)

    source = price_source.for_session(session, focus)
    window_end = source.config.fork_date + timedelta(days=365 * 30)
    payload.update(
        {
            "config": source.config.describe(),
            "bounds": source.bounds(),
            "generated_bars": len(
                database.fetch_simulated_prices(
                    session_id, focus, source.config.fork_date, window_end
                )
            ),
            "shocks": database.simulated_shock_log(session_id, focus),
        }
    )
    return jsonify(payload)


@api.post("/session/<session_id>/shock")
def inject_shock(session_id: str):
    """Inject one specific event, bypassing Gemini.

    Same code path as an AI-generated event, just with the numbers handed in - useful when
    a demo needs a particular headline, or when the API is slow. `scope` decides how wide
    it lands: one symbol, every watched name in a sector, or the whole basket.
    """
    session = database.get_session(session_id)
    if not session:
        raise GameError("Session not found")

    body = request.get_json(silent=True) or {}
    scope = str(body.get("scope") or events.SCOPE_TICKER).strip().lower()
    if scope not in events.SCOPES:
        raise GameError(f"scope must be one of {', '.join(events.SCOPES)}")

    sources = price_source.for_watchlist(session)
    simulated = {ticker: source for ticker, source in sources.items() if source.kind == "simulated"}

    ticker = (body.get("ticker") or "").strip().upper()
    if not ticker:
        if not simulated:
            raise GameError("This session is replaying real data; fork a simulated future first.")
        ticker = next(iter(simulated))
    if ticker not in sources:
        raise GameError(f"{ticker} is not in this session's watchlist.")

    sectors = engine.session_sectors(sources)
    targets = events.resolve_targets(simulated, {"scope": scope, "ticker": ticker,
                                                 "sector": sectors.get(ticker, {}).get("sector")}, sectors)
    if not targets:
        raise GameError(
            f"{ticker} is replaying real data; fork a simulated future first."
            if scope == events.SCOPE_TICKER
            else f"No simulated symbol matches a {scope}-wide event yet; fork a future first."
        )

    sentiment = _optional_float(body.get("sentiment"), "sentiment")
    magnitude = _optional_float(body.get("magnitude"), "magnitude")
    if sentiment is None or magnitude is None:
        raise GameError("sentiment and magnitude are required")
    if not -1 <= sentiment <= 1:
        raise GameError("sentiment must be between -1 and 1")
    if not 0 <= magnitude <= 1:
        raise GameError("magnitude must be between 0 and 1")

    decay_days = _optional_int(body.get("decay_days"), "decay_days")
    if decay_days is not None and decay_days < 1:
        # `or 10` used to swallow a 0 and quietly run a different event than asked for.
        raise GameError("decay_days must be at least 1")

    sector = body.get("sector") or sectors.get(ticker, {}).get("sector")
    event = {
        "ticker": ticker,
        "scope": scope,
        "sector": sector,
        "as_of": _as_date(session["sim_date"]).isoformat(),
        "headline": body.get("headline") or "Simulator-injected market event",
        "summary": body.get("summary", ""),
        "sentiment": sentiment,
        "magnitude": magnitude,
        "decay_days": decay_days if decay_days is not None else config.SHOCK_DECAY_DAYS,
        "lesson": body.get("lesson", ""),
        "fictional": True,
        "source": "manual",
    }
    return jsonify(
        events.apply(session_id, sources, _as_date(session["sim_date"]), event, sectors)
    ), 201
