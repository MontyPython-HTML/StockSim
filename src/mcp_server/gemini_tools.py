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
    """Model/key pairs to try in order. Gemini 503s and per-key model access both vary
    minute to minute, so one dead combination must not sink the call. Grouped by key so a
    dead key is exhausted last, and capped so the whole chain stays under the MCP call
    timeout in gemini_mcp_client.
    """
    models = [config.GEMINI_MODEL] + [m for m in FALLBACK_MODELS if m != config.GEMINI_MODEL]
    pairs = [(model, key) for key in config.GEMINI_API_KEYS for model in models]
    return pairs[:MAX_ATTEMPTS]


def generate_json(prompt: str, temperature: float = 0.9) -> dict:
    if not config.GEMINI_API_KEYS:
        raise RuntimeError("No GEMINI_API_KEY configured")
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
