import pandas as pd

RSI_PERIOD = 14
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
VOLUME_SPIKE_MULTIPLE = 2.0


def compute_all(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    close = out["close"]

    out["sma20"] = close.rolling(20).mean()
    out["sma50"] = close.rolling(50).mean()

    delta = close.diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)
    avg_gain = gains.ewm(alpha=1 / RSI_PERIOD, adjust=False, min_periods=RSI_PERIOD).mean()
    avg_loss = losses.ewm(alpha=1 / RSI_PERIOD, adjust=False, min_periods=RSI_PERIOD).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    out["rsi14"] = (100 - 100 / (1 + rs)).fillna(100.0).where(avg_gain.notna())

    ema_fast = close.ewm(span=MACD_FAST, adjust=False).mean()
    ema_slow = close.ewm(span=MACD_SLOW, adjust=False).mean()
    out["macd"] = ema_fast - ema_slow
    out["macd_signal"] = out["macd"].ewm(span=MACD_SIGNAL, adjust=False).mean()
    out["macd_hist"] = out["macd"] - out["macd_signal"]

    out["volume_avg20"] = out["volume"].rolling(20).mean()
    return out


def _crossed_above(series: pd.Series, other: pd.Series) -> bool:
    return (
        pd.notna(series.iloc[-1])
        and pd.notna(other.iloc[-1])
        and pd.notna(series.iloc[-2])
        and pd.notna(other.iloc[-2])
        and series.iloc[-2] <= other.iloc[-2]
        and series.iloc[-1] > other.iloc[-1]
    )


def detect_signals(df: pd.DataFrame) -> list[dict]:
    if len(df) < 2:
        return []

    signals: list[dict] = []
    sma20, sma50 = df["sma20"], df["sma50"]
    macd, macd_signal = df["macd"], df["macd_signal"]
    rsi = df["rsi14"]

    if _crossed_above(sma20, sma50):
        signals.append(
            {
                "name": "Golden cross",
                "direction": "bullish",
                "message": "The 20-day average price moved above the 50-day average. The stock "
                "may be turning upward.",
            }
        )
    elif _crossed_above(sma50, sma20):
        signals.append(
            {
                "name": "Death cross",
                "direction": "bearish",
                "message": "The 20-day average price moved below the 50-day average. The stock "
                "may be turning downward.",
            }
        )

    if _crossed_above(macd, macd_signal):
        signals.append(
            {
                "name": "MACD bullish crossover",
                "direction": "bullish",
                "message": "The blue MACD line crossed above the yellow line. The stock is "
                "picking up speed upward. This is an early hint, so it can be wrong.",
            }
        )
    elif _crossed_above(macd_signal, macd):
        signals.append(
            {
                "name": "MACD bearish crossover",
                "direction": "bearish",
                "message": "The blue MACD line crossed below the yellow line. The stock is "
                "slowing down. Many traders see this as a warning.",
            }
        )

    if pd.notna(rsi.iloc[-1]) and pd.notna(rsi.iloc[-2]):
        if rsi.iloc[-2] <= 70 < rsi.iloc[-1]:
            signals.append(
                {
                    "name": "RSI overbought",
                    "direction": "bearish",
                    "message": f"RSI went above 70 (now {rsi.iloc[-1]:.0f}). The stock went up "
                    "fast and may cool off.",
                }
            )
        elif rsi.iloc[-2] >= 30 > rsi.iloc[-1]:
            signals.append(
                {
                    "name": "RSI oversold",
                    "direction": "bullish",
                    "message": f"RSI went below 30 (now {rsi.iloc[-1]:.0f}). The stock went down "
                    "fast and may bounce back.",
                }
            )

    avg_volume = df["volume_avg20"]
    if pd.notna(avg_volume.iloc[-1]) and pd.notna(avg_volume.iloc[-2]):
        spiking = df["volume"].iloc[-1] > VOLUME_SPIKE_MULTIPLE * avg_volume.iloc[-1]
        was_spiking = df["volume"].iloc[-2] > VOLUME_SPIKE_MULTIPLE * avg_volume.iloc[-2]
        if spiking and not was_spiking:
            multiple = df["volume"].iloc[-1] / avg_volume.iloc[-1]
            signals.append(
                {
                    "name": "Volume spike",
                    "direction": "neutral",
                    "message": f"About {multiple:.1f} times the usual number of shares traded "
                    "today. Lots of people care about today's price move.",
                }
            )

    return signals


def _number(value) -> float | None:
    """NaN out, null in.

    A bar before an indicator has warmed up carries NaN, and NaN is not valid JSON - it
    serialises to a bare `NaN` token that makes the browser's JSON.parse throw and the
    page die with nothing shown. Starting a run on a symbol's listing day is enough to
    hit this, so every number that leaves here goes through one guard.
    """
    return None if value is None or pd.isna(value) else float(value)


def latest_row_summary(df: pd.DataFrame) -> dict:
    if df.empty:
        return {}
    row = df.iloc[-1]

    def value(column: str) -> float | None:
        return _number(row.get(column))

    return {
        "date": row["ts"].isoformat(),
        "open": value("open"),
        "high": value("high"),
        "low": value("low"),
        "close": value("close"),
        "volume": _number(row["volume"]),
        "sma20": value("sma20"),
        "sma50": value("sma50"),
        "rsi14": value("rsi14"),
        "macd": value("macd"),
        "macd_signal": value("macd_signal"),
        "macd_hist": value("macd_hist"),
        "volume_avg20": value("volume_avg20"),
    }


def to_series(df: pd.DataFrame) -> list[dict]:
    if df.empty:
        return []
    return [
        {
            "date": row["ts"].isoformat(),
            "close": _number(row["close"]),
            "volume": _number(row["volume"]),
            "sma20": _number(row["sma20"]),
            "sma50": _number(row["sma50"]),
            "rsi14": _number(row["rsi14"]),
            "macd": _number(row["macd"]),
            "macd_signal": _number(row["macd_signal"]),
            "macd_hist": _number(row["macd_hist"]),
        }
        for _, row in df.iterrows()
    ]
