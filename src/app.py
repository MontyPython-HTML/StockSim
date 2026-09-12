import os
from datetime import date, datetime
from decimal import Decimal

from flask import Flask, redirect, render_template, url_for
from flask.json.provider import DefaultJSONProvider

import config
from routes.game_routes import api
from scripts.database import database
from scripts.game.engine import GameError

base_dir = os.path.abspath(os.path.dirname(__file__))
template_dir = os.path.join(base_dir, 'templates')


class GameJSONProvider(DefaultJSONProvider):
    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        if isinstance(o, (date, datetime)):
            return o.isoformat()
        return super().default(o)


app = Flask(__name__, template_folder=template_dir)
app.json = GameJSONProvider(app)
app.register_blueprint(api)


@app.errorhandler(GameError)
def handle_game_error(error: GameError):
    return {"error": str(error)}, 400


@app.route('/')
def home():
    return render_template(
        'index.html',
        tickers=database.available_tickers(),
        funding_source=config.NESSIE_DEFAULT_CUSTOMER_ID,
    )


@app.route('/game/<session_id>')
def game(session_id: str):
    session = database.get_session(session_id)
    if not session:
        return redirect(url_for('home'))
    return render_template('game.html', session_id=session_id, ticker=session['ticker'])


if __name__ == '__main__':
    app.run(debug=True)
