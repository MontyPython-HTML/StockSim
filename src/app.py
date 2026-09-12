import os
import pandas as pd
import json
import plotly
from flask import Flask, render_template
from google import genai
from dotenv import load_dotenv
import yfinance as yf
import plotly.graph_objects as go


base_dir = os.path.abspath(os.path.dirname(__file__))
template_dir = os.path.join(base_dir, 'templates')

app = Flask(__name__, template_folder=template_dir)

@app.route('/')
def home():
    return render_template('index.html')




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

