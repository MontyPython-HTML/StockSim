import hashlib
import json
import threading
import time
from collections import OrderedDict
from datetime import date
from functools import lru_cache

import pandas as pd
from google import genai
from google.genai import types

import config

CONTEXT_ROWS = 30
REQUEST_TIMEOUT_MS = 10000
MAX_ATTEMPTS = 4
FALLBACK_MODELS = ["gemini-flash-latest", "gemini-2.5-flash"]

# Every response here is a small JSON object. Without a ceiling the model is free to pad
# the prose fields, which costs output tokens and latency on every single call.
MAX_OUTPUT_TOKENS = 900

# A model/key pair that just failed is very unlikely to work again a second later, and
# retrying it costs a full REQUEST_TIMEOUT_MS before the chain moves on. Remembering the
# failure for a short while turns a dead key from a per-call tax into a one-off.
FAILURE_COOLDOWN_SECONDS = 90

# Identical prompt means identical question: the same pattern, on the same symbol, on the
# same bar. Teaching answers are reused rather than re-bought, which is what makes
# replaying a stretch - or two players trading the same week - cost one call instead of N.
CACHE_TTL_SECONDS = 6 * 60 * 60
CACHE_MAX_ENTRIES = 256

DISCLAIMER = "Simulated teaching output. Not financial advice."

_cooldowns: dict[tuple[str, str], float] = {}
_cache: "OrderedDict[str, tuple[float, dict]]" = OrderedDict()
_state_lock = threading.Lock()


def _cache_get(key: str) -> dict | None:
    with _state_lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        stored_at, payload = entry
        if time.monotonic() - stored_at > CACHE_TTL_SECONDS:
            _cache.pop(key, None)
            return None
        _cache.move_to_end(key)
    # Copied on the way out so a caller stamping session_id onto the result cannot
    # write that into every future reader's copy.
    return json.loads(json.dumps(payload))


def _cache_put(key: str, payload: dict) -> None:
    with _state_lock:
        _cache[key] = (time.monotonic(), payload)
        _cache.move_to_end(key)
        while len(_cache) > CACHE_MAX_ENTRIES:
            _cache.popitem(last=False)


def _cooling_down(pair: tuple[str, str]) -> bool:
    with _state_lock:
        until = _cooldowns.get(pair)
        if until is None:
            return False
        if time.monotonic() >= until:
            _cooldowns.pop(pair, None)
            return False
        return True


def _mark_failed(pair: tuple[str, str]) -> None:
    with _state_lock:
        _cooldowns[pair] = time.monotonic() + FAILURE_COOLDOWN_SECONDS


def _mark_ok(pair: tuple[str, str]) -> None:
    with _state_lock:
        _cooldowns.pop(pair, None)


def cache_stats() -> dict:
    with _state_lock:
        return {
            "cached_answers": len(_cache),
            "cooling_down": [f"{model}" for model, _ in _cooldowns],
        }


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

    Pairs that failed within the last FAILURE_COOLDOWN_SECONDS are moved to the back
    rather than dropped: skipping them is the point, but a blip that knocks out every
    pair must not leave the coach with nothing to try.
    """
    models = [config.GEMINI_MODEL] + [m for m in FALLBACK_MODELS if m != config.GEMINI_MODEL]
    pairs = [(model, key) for model in models for key in config.GEMINI_API_KEYS]
    fresh = [pair for pair in pairs if not _cooling_down(pair)]
    resting = [pair for pair in pairs if _cooling_down(pair)]
    return (fresh + resting)[:MAX_ATTEMPTS]


MISSING_KEY_MESSAGE = (
    "No GEMINI_API_KEY configured. Add GEMINI_API_KEY=... to hackrice/.env and restart "
    "the app; the calendar, trading and price simulation all work without it."
)


def generate_json(prompt: str, temperature: float = 0.9, cache: bool = True) -> dict:
    """One JSON answer from whichever model/key pair answers first.

    `cache=False` is for the deliberately creative calls - invented headlines - where two
    sessions asking the same question are supposed to get different stories. Teaching
    answers cache: the same pattern on the same bar has one correct explanation.
    """
    if not config.GEMINI_API_KEYS:
        raise RuntimeError(MISSING_KEY_MESSAGE)

    key = None
    if cache:
        key = hashlib.sha256(f"{temperature}::{prompt}".encode()).hexdigest()
        hit = _cache_get(key)
        if hit is not None:
            return hit

    errors: list[str] = []
    for pair in _candidates():
        model, api_key = pair
        try:
            response = _client_for(api_key).models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=temperature,
                    max_output_tokens=MAX_OUTPUT_TOKENS,
                ),
            )
            data = json.loads(response.text)
        except Exception as exc:
            _mark_failed(pair)
            errors.append(f"{model}: {type(exc).__name__} {str(exc)[:80]}")
            continue
        _mark_ok(pair)
        if key is not None:
            _cache_put(key, data)
        return data
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
    data = generate_json(prompt, temperature=1.0, cache=False)

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


def generate_pattern_lesson(
    ticker: str,
    as_of: date,
    df: pd.DataFrame,
    pattern: dict,
    signal: str,
    context: dict | None = None,
) -> dict:
    """Teach one chart pattern, using the player's own chart as the example.

    `pattern` is the reference entry from the local syllabus rather than a name the model
    is asked to interpret, and it is included in the prompt on purpose: the coach has to
    explain the pattern the detector actually found, at the depth the syllabus sets. Left
    to itself the model drifts into a generic encyclopedia entry, and the player ends up
    reading about a pattern that is not on their screen.

    `context` is the indicator reading on the day the signal fired, so the lesson can say
    "RSI is 78" instead of "RSI above 70".
    """
    readings = context or {}
    lines = [
        f"- close: {readings.get('close')}",
        f"- RSI(14): {readings.get('rsi14')}",
        f"- SMA20: {readings.get('sma20')}",
        f"- SMA50: {readings.get('sma50')}",
        f"- MACD: {readings.get('macd')} (signal {readings.get('macd_signal')}, histogram {readings.get('macd_hist')})",
        f"- volume: {readings.get('volume')} against a 20-day average of {readings.get('volume_avg20')}",
    ]
    spots = "\n".join(f"  {index}. {step}" for index, step in enumerate(pattern["how_to_spot"], start=1))
    prompt = f"""You are a trading coach in a teaching simulator. Today is {as_of} and the student
is watching {ticker}. A pattern detector just fired on their chart:

  pattern: {pattern['name']} ({pattern['family']})
  detected: {signal}
  the question it raises: {pattern['tension']}

The chart on their screen, as of today:
{chr(10).join(lines)}

Recent price and indicator history:
{format_context(df)}

Explain this pattern to a beginner who is looking at the numbers above right now. Ground every
claim in the values listed - name the actual RSI reading, the actual averages - so the lesson
points at their chart and not at a textbook.

The reference explanation this curriculum is built on (stay consistent with it, deepen it, do
not contradict it):
  what it is: {pattern['what_it_is']}
  how to spot it:
{spots}
  why it matters: {pattern['why_it_matters']}
  common mistake: {pattern['common_mistake']}
  watch next: {pattern['watch_next']}

Reply as JSON with exactly these keys:
- "what_it_is": 2 sentences in plain English, referring to this chart's numbers
- "how_to_spot": array of 2-4 short strings, each a concrete check the student can make on
  this chart (e.g. "SMA20 at 182.40 is only just above SMA50 at 181.05")
- "why_it_matters": 1 sentence on what a disciplined trader does about it
- "common_mistake": 1 sentence on how a beginner misreads this exact situation
- "watch_next": 1 sentence naming the level or signal that would confirm or invalidate it
- "confidence": number 0-1, how clear-cut this example is on the chart above
- "lesson": one sentence summarising the takeaway
"""
    data = generate_json(prompt)

    def bound(value, fallback: float) -> float:
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return fallback

    def text(key: str, fallback: str) -> str:
        value = data.get(key)
        return str(value).strip() if value else fallback

    steps = data.get("how_to_spot")
    if not isinstance(steps, list) or not steps:
        steps = list(pattern["how_to_spot"])
    return {
        "pattern": pattern["slug"],
        "name": pattern["name"],
        "family": pattern["family"],
        "what_it_is": text("what_it_is", pattern["what_it_is"]),
        "how_to_spot": [str(step) for step in steps],
        "why_it_matters": text("why_it_matters", pattern["why_it_matters"]),
        "common_mistake": text("common_mistake", pattern["common_mistake"]),
        "watch_next": text("watch_next", pattern["watch_next"]),
        "confidence": bound(data.get("confidence"), 0.5),
        "lesson": data.get("lesson", ""),
        "ticker": ticker,
        "as_of": as_of.isoformat(),
        "source": "gemini",
        "fictional": False,
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
    data = generate_json(prompt, temperature=1.0, cache=False)
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
