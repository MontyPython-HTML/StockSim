import math
import os
import threading
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from flask import Flask, redirect, render_template, url_for
from flask.json.provider import DefaultJSONProvider

import config
from routes.game_routes import api
from scripts.api import nessie
from scripts.database import database
from scripts.game import levels
from scripts.game.engine import GameError

base_dir = os.path.abspath(os.path.dirname(__file__))
template_dir = os.path.join(base_dir, 'templates')


def _finite(value):
    """Replace NaN and Infinity with null, all the way down.

    Python's JSON encoder writes non-finite floats as the bare tokens NaN/Infinity, which
    are not valid JSON: the browser's JSON.parse then rejects the entire payload and the
    page fails with nothing on screen. One unwarmed indicator column used to be enough to
    do that, so the last thing every response passes through is this.
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: _finite(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite(item) for item in value]
    return value


class GameJSONProvider(DefaultJSONProvider):
    def default(self, o):
        if isinstance(o, Decimal):
            return float(o) if o.is_finite() else None
        if isinstance(o, (date, datetime)):
            return o.isoformat()
        return super().default(o)

    def dumps(self, obj, **kwargs):
        return super().dumps(_finite(obj), **kwargs)


app = Flask(__name__, template_folder=template_dir)
app.json = GameJSONProvider(app)
app.register_blueprint(api)


with app.app_context():
    try:
        database.apply_schema()
    except Exception:  # noqa: BLE001
        app.logger.warning("could not apply schema at startup", exc_info=True)

    try:
        app.logger.info("database pool ready: %s", database.warm_up())
    except Exception:  # noqa: BLE001
        app.logger.warning("could not open the database pool at startup", exc_info=True)


def _warm_ai_import() -> None:
    """Import the MCP stack in the background so the first click does not pay for it.

    The engine imports the MCP client lazily to keep this app's boot path clear of it,
    but that only moved the cost onto the player: the first tick that schedules anything
    waits on ~1s of imports while it holds the interpreter lock. Doing it here keeps boot
    fast and the first tick fast.
    """
    try:
        from scripts.api import gemini_mcp_client  # noqa: F401
    except Exception:  # noqa: BLE001
        app.logger.warning("MCP client import failed; AI features will report it", exc_info=True)


threading.Thread(target=_warm_ai_import, name="warm-ai-import", daemon=True).start()


@app.errorhandler(GameError)
def handle_game_error(error: GameError):
    return {"error": str(error)}, 400


def _catalog() -> tuple[list[dict], dict]:
    """The ticker catalog, tolerating a database that has not been migrated yet.

    `tickers` arrives with schema.sql, so a teammate who pulls this branch before
    running the setup script would otherwise get a 500 on the landing page instead of
    the "no data yet" banner.
    """
    try:
        return database.list_universe(), database.universe_stats()
    except Exception:  # noqa: BLE001
        app.logger.warning("ticker catalog unavailable; run scripts.ingestion.sync_universe")
        return [], {}


@app.route('/')
def home():
    catalog, stats = _catalog()
    return render_template(
        'index.html',
        universe=catalog,
        stats=stats,
        max_watchlist=config.MAX_WATCHLIST,
        funding_source=nessie.source_label(),
        levels=levels.catalog(),
    )


@app.route('/game/<session_id>')
def game(session_id: str):
    try:
        UUID(session_id)
    except ValueError:
        return redirect(url_for('home'))
    session = database.get_session(session_id)
    if not session:
        return redirect(url_for('home'))
    return render_template('game.html', session_id=session_id)


if __name__ == '__main__':
    app.run(debug=True)

