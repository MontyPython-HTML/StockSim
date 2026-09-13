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
            # numeric columns arrive as Decimal, including the odd NaN one.
            return float(o) if o.is_finite() else None
        if isinstance(o, (date, datetime)):
            return o.isoformat()
        return super().default(o)

    def dumps(self, obj, **kwargs):
        return super().dumps(_finite(obj), **kwargs)


app = Flask(__name__, template_folder=template_dir)
app.json = GameJSONProvider(app)
app.register_blueprint(api)


# schema.sql is idempotent and carries the migrations, so applying it once at boot keeps
# a long-lived shared database (or a teammate's stale checkout) in step with the code
# instead of failing on a column the code no longer writes. A database that is missing
# or unreachable is a display problem the landing page already tolerates.
with app.app_context():
    try:
        database.apply_schema()
    except Exception:  # noqa: BLE001 - never block startup on the schema step
        app.logger.warning("could not apply schema at startup", exc_info=True)

    # Dial the database now, on the main thread, rather than from whichever request
    # happens to arrive first: the pool's connections are made under a lock, so the first
    # page load otherwise pays for all of them and every page load beside it waits.
    try:
        app.logger.info("database pool ready: %s", database.warm_up())
    except Exception:  # noqa: BLE001 - a missing database is reported per request
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
    except Exception:  # noqa: BLE001 - an unusable MCP stack is reported per request
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
    except Exception:  # noqa: BLE001 - a missing catalog is a display problem, not a crash
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
    # A malformed id is a missing session, not a crash: the id goes straight into a uuid
    # column, so /game/garbage raised InvalidTextRepresentation and served a 500 page
    # instead of sending the player back to the picker.
    try:
        UUID(session_id)
    except ValueError:
        return redirect(url_for('home'))
    session = database.get_session(session_id)
    if not session:
        return redirect(url_for('home'))
    # The session's symbols live in session_tickers now, so the page is handed only its
    # id and fetches its own state - a session's focus starts on the first watchlist name.
    return render_template('game.html', session_id=session_id)





def build_chart(symbol):
    df = yf.download(symbol, period="6mo")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)

    fig = go.Figure(data=[go.Candlestick(
        x=df.index,
        open=df["Open"], high=df["High"],
        low=df["Low"], close=df["Close"],
        increasing_line_color="#0f7a45",
        decreasing_line_color="#b3261e",
    )])
    fig.update_layout(
        margin=dict(l=40, r=10, t=10, b=30),
        xaxis_rangeslider_visible=False,
        plot_bgcolor="white", paper_bgcolor="white",
        showlegend=False, height=400,
    )
    fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])], gridcolor="#e5e7eb")
    fig.update_yaxes(gridcolor="#e5e7eb")

    return fig.to_html(
        full_html=False,
        include_plotlyjs="cdn",
        config={"displayModeBar": False, "responsive": True},
    )


if __name__ == '__main__':
    app.run(debug=True)

