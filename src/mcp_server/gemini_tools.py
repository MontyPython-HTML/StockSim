import json
from datetime import date
from functools import lru_cache

import pandas as pd
from google import genai
from google.genai import types

import config

CONTEXT_ROWS = 30
REQUEST_TIMEOUT_MS = 10000
MAX_ATTEMPTS = 3
FALLBACK_MODELS = ["gemini-2.5-flash", "gemini-flash-latest"]

DISCLAIMER = "Simulated teaching output. Not financial advice."


@lru_cache(maxsize=4)
def _client_for(api_key: str) -> genai.Client:
    # Cached: a garbage-collected genai.Client closes the httpx transport its siblings share.
    return genai.Client(
        api_key=api_key,
        # Bounded so a Gemini 503 falls through to the next key instead of retrying for a minute.
        http_options=types.HttpOptions(
            timeout=REQUEST_TIMEOUT_MS,
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )


def _candidates() -> list[tuple[str, str]]:
    """Model/key pairs to try in order.

    Gemini 503s and per-model access both vary minute to minute, so one dead combination
    must not sink the call. Ordered model-major deliberately: when a model is overloaded
    or not enabled for a key, the cheapest recovery is the SAME model on the next key.
    Grouping by key first burned every attempt on one dead key and never reached the
    second. Capped so the chain stays under the MCP call timeout in gemini_mcp_client.
    """
    models = [config.GEMINI_MODEL] + [m for m in FALLBACK_MODELS if m != config.GEMINI_MODEL]
    pairs = [(model, key) for model in models for key in config.GEMINI_API_KEYS]
    return pairs[:MAX_ATTEMPTS]


MISSING_KEY_MESSAGE = (
    "No GEMINI_API_KEY configured. Add GEMINI_API_KEY=... to hackrice/.env and restart "
    "the app; the calendar, trading and price simulation all work without it."
)


def generate_json(prompt: str, temperature: float = 0.9) -> dict:
    if not config.GEMINI_API_KEYS:
        raise RuntimeError(MISSING_KEY_MESSAGE)
    errors: list[str] = []
    for model, api_key in _candidates():
        try:
            response = _client_for(api_key).models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=temperature,
                ),
            )
            return json.loads(response.text)
        except Exception as exc:
            errors.append(f"{model}: {type(exc).__name__} {str(exc)[:80]}")
    raise RuntimeError("all Gemini attempts failed -> " + " | ".join(errors))


def format_context(df: pd.DataFrame) -> str:
    recent = df.tail(CONTEXT_ROWS)
    lines = []
    for _, row in recent.iterrows():
        def fmt(column: str, digits: int = 2) -> str:
            value = row.get(column)
            return "n/a" if value is None or pd.isna(value) else f"{float(value):.{digits}f}"

        lines.append(
            f"{row['ts']}: close={fmt('close')} vol={int(row['volume'])} "
            f"sma20={fmt('sma20')} sma50={fmt('sma50')} rsi={fmt('rsi14', 1)} "
            f"macd={fmt('macd', 3)} signal={fmt('macd_signal', 3)}"
        )
    return "\n".join(lines)


def generate_prediction(ticker: str, as_of: date, df: pd.DataFrame) -> dict:
    prompt = f"""You are a trading coach in a teaching simulator. Today is {as_of} and the
student is trading {ticker}. Below is the real price and indicator history available to them.
You cannot see beyond {as_of}.

{format_context(df)}

Give your read on the next few sessions and, more importantly, teach the student which signals
you are reading and why. Reply as JSON with exactly these keys:
- "direction": one of "up", "down", "flat"
- "confidence": number between 0 and 1
- "rationale": 2 sentences, plain English, naming the specific indicators you used
- "referenced_indicators": array of short strings, e.g. ["SMA20 above SMA50", "RSI 68"]
- "what_to_watch": one sentence naming the level or signal that would prove you wrong
"""
    data = generate_json(prompt)
    return {
        "ticker": ticker,
        "as_of": as_of.isoformat(),
        "direction": str(data.get("direction", "flat")).lower(),
        "confidence": float(data.get("confidence", 0.5)),
        "rationale": data.get("rationale", ""),
        "referenced_indicators": data.get("referenced_indicators", []),
        "what_to_watch": data.get("what_to_watch", ""),
        "disclaimer": DISCLAIMER,
    }


def generate_market_shock(
    ticker: str,
    as_of: date,
    df: pd.DataFrame,
    scope: str = "ticker",
    sector: str | None = None,
    peers: list[str] | None = None,
) -> dict:
    """A fictional headline plus the numbers needed to bend the simulated chart.

    This is the version that drives the user's generated future: on top of the usual
    newswire staging it has to commit to how big the move is and how long it lingers,
    because those two numbers are applied to the forward price curve.

    `scope` decides how wide the story is. The caller picks it (see events._pick_scope) so
    a session's basket gets a realistic mix of company, sector and macro news rather than
    ten unrelated single-stock stories.
    """
    recent_close = float(df["close"].iloc[-1]) if not df.empty else 0.0
    trend = "rising"
    if len(df) >= 50:
        sma20 = df["sma20"].iloc[-1] if "sma20" in df else None
        sma50 = df["sma50"].iloc[-1] if "sma50" in df else None
        if sma20 is not None and sma50 is not None and not pd.isna(sma20) and not pd.isna(sma50):
            trend = "rising" if sma20 >= sma50 else "falling"

    watchlist = ", ".join(peers or [ticker])
    if scope == "market":
        subject = (
            "Invent ONE fictional macro event - a rates, inflation, growth or liquidity "
            "story - that would move the entire market at once, not one company. It must "
            "not name a single company as its cause."
        )
        lesson_hint = (
            "tell the student what a broad market move means for a portfolio whose names "
            "are all correlated"
        )
    elif scope == "sector":
        subject = (
            f"Invent ONE fictional sector-wide event for the {sector or 'technology'} sector. "
            f"It must plausibly move EVERY name in the sector, which for this student means "
            f"{watchlist}. Do not make it good news for one of them as a direct consequence "
            f"of another's loss."
        )
        lesson_hint = (
            "tell the student why a sector headline hits several of their holdings at once, "
            "and what that says about how diversified they actually are"
        )
    else:
        subject = (
            f"Invent ONE fictional event for this company. It must be plausible for a "
            f"company of this size but entirely made up, and it must be the kind of thing "
            f"that moves a stock for days rather than one tick."
        )
        lesson_hint = (
            "tell the student how a disciplined trader should react to this kind of "
            "headline given the trend above"
        )

    prompt = f"""You are the market-event generator inside a teaching simulator. The stock
is {ticker}, the in-game date is {as_of}, it last closed at {recent_close:.2f}, and its
20/50-day trend is {trend}. The student currently holds or watches: {watchlist}.

Context (the only data you may reference):
{format_context(df)}

{subject} Reply as JSON with exactly these keys:
- "headline": under 90 characters, written like a newswire headline
- "summary": one sentence of detail
- "sentiment": number from -1 (very bearish) to 1 (very bullish)
- "magnitude": number from 0 (barely noticed) to 1 (market-moving)
- "decay_days": integer 1-30, how many sessions the effect takes to fade
- "sector": the affected sector name in upper case (e.g. "TECHNOLOGY"), or null if the
  story is company-specific
- "lesson": one sentence that should {lesson_hint}
"""
    data = generate_json(prompt, temperature=1.0)

    def bound(value, low: float, high: float, fallback: float) -> float:
        try:
            return max(low, min(high, float(value)))
        except (TypeError, ValueError):
            return fallback

    reported_sector = data.get("sector")
    return {
        "ticker": ticker,
        "scope": scope,
        "sector": sector if scope == "market" else (reported_sector or sector),
        "as_of": as_of.isoformat(),
        "headline": data.get("headline", ""),
        "summary": data.get("summary", ""),
        "sentiment": bound(data.get("sentiment"), -1.0, 1.0, 0.0),
        "magnitude": bound(data.get("magnitude"), 0.0, 1.0, 0.3),
        "decay_days": int(bound(data.get("decay_days"), 1, 30, 10)),
        "lesson": data.get("lesson", ""),
        "fictional": True,
        "disclaimer": DISCLAIMER,
    }


def generate_news_event(ticker: str, as_of: date, df: pd.DataFrame) -> dict:
    recent_close = float(df["close"].iloc[-1]) if not df.empty else 0.0
    prompt = f"""Invent one fictional market news headline for a trading simulator. The stock is
{ticker}, the in-game date is {as_of}, and it last closed at {recent_close:.2f}.

The headline must be plausible for a company like this but entirely made up. Reply as JSON with
exactly these keys:
- "headline": under 90 characters, written like a newswire headline
- "summary": one sentence of detail
- "sentiment": number from -1 (very bearish) to 1 (very bullish)
- "magnitude": number from 0 (noise) to 1 (major)
- "lesson": one sentence telling the student how a disciplined trader should react to this kind
  of headline, given what the chart is doing
"""
    data = generate_json(prompt, temperature=1.0)
    return {
        "ticker": ticker,
        "as_of": as_of.isoformat(),
        "headline": data.get("headline", ""),
        "summary": data.get("summary", ""),
        "sentiment": float(data.get("sentiment", 0.0)),
        "magnitude": float(data.get("magnitude", 0.3)),
        "lesson": data.get("lesson", ""),
        "fictional": True,
    }
