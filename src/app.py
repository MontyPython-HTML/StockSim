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

