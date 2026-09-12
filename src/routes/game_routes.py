from datetime import datetime

from flask import Blueprint, jsonify, request

from scripts.database import database
from scripts.game.engine import GameError
from scripts.game import engine

api = Blueprint("api", __name__, url_prefix="/api")


def _parse_date(value: str, field: str):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise GameError(f"{field} must be a date formatted YYYY-MM-DD")


@api.errorhandler(GameError)
def handle_game_error(error: GameError):
    return jsonify({"error": str(error)}), 400


@api.get("/tickers")
def tickers():
    return jsonify({"tickers": database.available_tickers()})


@api.post("/session/start")
def start_session():
    body = request.get_json(silent=True) or {}
    ticker = (body.get("ticker") or "").strip()
    if not ticker:
        raise GameError("ticker is required")
    state = engine.start_session(
        ticker=ticker,
        start_date=_parse_date(body.get("start_date"), "start_date"),
        end_date=_parse_date(body.get("end_date"), "end_date"),
        nessie_customer_id=body.get("nessie_customer_id"),
    )
    return jsonify(state), 201


@api.get("/session/<session_id>/state")
def session_state(session_id: str):
    return jsonify(engine.get_state(session_id))


@api.post("/session/<session_id>/advance")
def advance(session_id: str):
    body = request.get_json(silent=True) or {}
    days = int(body.get("days", 1))
    return jsonify(engine.advance_day(session_id, days=days, with_ai=body.get("with_ai", True)))


@api.post("/session/<session_id>/trade")
def trade(session_id: str):
    body = request.get_json(silent=True) or {}
    return jsonify(
        engine.execute_trade(
            session_id, side=body.get("side", ""), shares=body.get("shares", 0)
        )
    )


@api.post("/session/<session_id>/predict")
def predict(session_id: str):
    prediction = engine.request_prediction(session_id)
    if not prediction:
        return jsonify({"error": "The AI coach is unavailable right now."}), 503
    return jsonify({"prediction": prediction})
