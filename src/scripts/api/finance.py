from datetime import date, timedelta

import pandas as pd
import yfinance as yf


def fetch_ohlcv(ticker: str, start: date, end: date) -> pd.DataFrame:
    #end param is exclusive
    # Dividends are what the game pays the player on an ex-date, so asking for the actions
    # explicitly matters: they ride along on this same request, and leaving it to a default
    # would let a yfinance change zero out every future payment without a single error.
    raw = yf.Ticker(ticker).history(
        start=start.isoformat(),
        end=(end + timedelta(days=1)).isoformat(),
        interval="1d",
        auto_adjust=False,
        actions=True,
    )
    if raw.empty:
        return raw
    raw = raw.reset_index()
    raw["Date"] = pd.to_datetime(raw["Date"]).dt.date
    return raw


def to_price_rows(ticker: str, frame: pd.DataFrame) -> list[tuple]:
    if frame.empty:
        return []
    adj_column = "Adj Close" if "Adj Close" in frame.columns else "Close"
    dividend_column = "Dividends" if "Dividends" in frame.columns else None
    return [
        (
            ticker.upper(),
            row["Date"],
            float(row["Open"]),
            float(row["High"]),
            float(row["Low"]),
            float(row["Close"]),
            float(row[adj_column]),
            float(row[dividend_column]) if dividend_column and pd.notna(row[dividend_column]) else 0.0,
            int(row["Volume"]),
        )
        for _, row in frame.iterrows()
        if pd.notna(row["Close"])
    ]
