"""A practice newswire for the stocks in a session.

A few stories matter: the days a stock moved hard or traded far above its normal volume
(reported from the chart itself, never with an invented cause) and the headlines the AI has
already staged. They are buried in routine company noise - conference talks, office moves,
filings - and nothing marks which is which. Sorting them out is the exercise.

Every story is a pure function of the session, the symbol and the day, so it does not change
between refreshes and never appears before its date.
"""

import hashlib
import logging
import math
import random
from datetime import date, timedelta

from scripts.database import database

log = logging.getLogger(__name__)

WINDOW_ROWS = 15
AI_LOOKBACK_DAYS = 30
LIMIT = 40
FILLER_CHANCE = 0.4
BIG_MOVE_PCT = 4.0
VOLUME_SPIKE = 2.5
AI_EVENT_TYPES = ("NEWS_EVENT", "MARKET_SHOCK")

PEOPLE = ["Dana Whitfield", "Ravi Mehta", "Colleen Park", "Tomas Ruiz", "Imani Brooks", "Greg Olsen", "Lena Hoffman"]
CITIES = ["Omaha", "Raleigh", "Tampa", "Boise", "Madison", "Tucson", "Richmond", "Spokane"]
CONFERENCES = ["Midwest Growth", "Pacific Technology", "Global Consumer", "Northeast Industrials", "Healthcare Leaders"]
BANKS = ["Harlow & Finch", "Brightwater Capital", "Kessler Securities", "Redwood Research", "Alder Partners"]
DEPARTMENTS = ["facilities", "internal communications", "procurement", "regional sales", "corporate travel"]

FILLER = [
    "{name} to present at the {conference} investor conference next month",
    "{name} names {person} vice president of {department}",
    "{name} renews the lease on its {city} regional office",
    "{bank} reiterates hold rating on {ticker}",
    "{name} publishes its annual sustainability report",
    "{name} confirms the date of its annual shareholder meeting",
    "{name} opens a customer support center in {city}",
    "{name} updates its employee travel policy",
    "{ticker} added to {bank}'s list of stocks to watch this quarter",
    "{name} sponsors the {city} half marathon",
    "{name} files routine paperwork with regulators",
    "{name} completes its previously announced office move in {city}",
    "{name} launches a refreshed company website",
    "{bank} trims its {ticker} price target by ${cut}",
    "{name} extends its office supply contract with a long-time vendor",
    "{name} hosts an employee volunteer day in {city}",
]
UP = ["{ticker} surges {pct:.1f}% in heavy trading", "{name} shares jump {pct:.1f}%", "{ticker} rallies {pct:.1f}%"]
DOWN = ["{ticker} tumbles {pct:.1f}% in heavy selling", "{name} shares slide {pct:.1f}%", "{ticker} drops {pct:.1f}%"]
VOLUME = ["Unusual activity in {ticker}: volume hits {ratio:.1f}x its daily average", "{name} trades at {ratio:.1f}x its normal volume"]

_names: dict[str, str] = {}


def _rng(*parts) -> random.Random:
    return random.Random(hashlib.sha256(":".join(str(part) for part in parts).encode()).digest())


def _company_names(tickers: list[str]) -> dict[str, str]:
    missing = [ticker for ticker in tickers if ticker not in _names]
    if missing:
        try:
            found = database.company_names(missing)
        except Exception as exc:  # noqa: BLE001 - a missing catalog just means ticker-only headlines
            log.warning("company names unavailable: %s", exc)
            found = {}
        for ticker in missing:
            _names[ticker] = found.get(ticker) or ticker
    return {ticker: _names[ticker] for ticker in tickers}


def _chart_stories(session_id: str, ticker: str, name: str, frame) -> list[dict]:
    recent = frame.tail(WINDOW_ROWS + 1)
    days = recent["ts"].tolist()
    closes = [float(value) for value in recent["close"].tolist()]
    volumes = recent["volume"].tolist()
    averages = recent["volume_avg20"].tolist() if "volume_avg20" in recent else [None] * len(recent)

    stories: list[dict] = []
    for i in range(1, len(recent)):
        day = days[i].isoformat()
        rng = _rng(session_id, ticker, day)
        fields = {"name": name, "ticker": ticker}

        change = (closes[i] - closes[i - 1]) / closes[i - 1] * 100 if closes[i - 1] else 0.0
        average = averages[i]
        ratio = volumes[i] / average if average and math.isfinite(average) else 0.0
        if abs(change) >= BIG_MOVE_PCT:
            headline = rng.choice(UP if change > 0 else DOWN).format(pct=abs(change), **fields)
            stories.append({"date": day, "ticker": ticker, "headline": headline, "major": True})
        elif ratio >= VOLUME_SPIKE:
            headline = rng.choice(VOLUME).format(ratio=ratio, **fields)
            stories.append({"date": day, "ticker": ticker, "headline": headline, "major": True})

        if rng.random() < FILLER_CHANCE:
            headline = rng.choice(FILLER).format(
                person=rng.choice(PEOPLE),
                city=rng.choice(CITIES),
                conference=rng.choice(CONFERENCES),
                bank=rng.choice(BANKS),
                department=rng.choice(DEPARTMENTS),
                cut=rng.randint(1, 3),
                **fields,
            )
            stories.append({"date": day, "ticker": ticker, "headline": headline, "major": False})
    return stories


def _ai_stories(events: list[dict], since: date) -> list[dict]:
    stories = []
    for event in events:
        headline = (event.get("payload") or {}).get("headline")
        if event["event_type"] not in AI_EVENT_TYPES or not headline:
            continue
        day = event["sim_date"] if isinstance(event["sim_date"], date) else date.fromisoformat(str(event["sim_date"]))
        if day >= since:
            stories.append({"date": day.isoformat(), "ticker": event["ticker"], "headline": headline, "major": True})
    return stories


def headlines(session: dict, sim_date: date, events: list[dict], sources: dict) -> list[dict]:
    """Newest first. Stories from the same day are shuffled so the important one is not always on top."""
    session_id = str(session["id"])
    names = _company_names(list(sources))
    stories: list[dict] = []
    for ticker, source in sources.items():
        frame = source.history_through(sim_date)
        if len(frame) > 1:
            stories.extend(_chart_stories(session_id, ticker, names[ticker], frame))
    stories.extend(_ai_stories(events, sim_date - timedelta(days=AI_LOOKBACK_DAYS)))

    stories.sort(key=lambda story: (story["date"], _rng(session_id, story["headline"]).random()), reverse=True)
    return [
        {"id": hashlib.sha1(f"{story['date']}|{story['ticker']}|{story['headline']}".encode()).hexdigest()[:12], **story}
        for story in stories[:LIMIT]
    ]
