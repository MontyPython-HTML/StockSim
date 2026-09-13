"""The Daily Ledger: a practice newspaper for the stocks in a session.

The stories that matter are facts read off the chart - big moves, volume spikes, 52-week highs
and lows, long streaks - plus the headlines the AI has already staged. They are printed next to
routine company news, clickbait columns and local business filler, and nothing marks which is
which. Every story is a pure function of the session, the symbol and the day, so an edition
never changes between reads and never appears before its date.
"""

import hashlib
import logging
import math
import random
from datetime import date, timedelta

from scripts.database import database

log = logging.getLogger(__name__)

WINDOW_ROWS = 15
ARCHIVE_DAYS = 20
AI_LOOKBACK_DAYS = 30
LIMIT = 40
YEAR_BARS = 252
BIG_MOVE_PCT = 4.0
VOLUME_SPIKE = 2.5
STREAK_DAYS = {5: "fifth", 8: "eighth"}
RECORD_GAP_BARS = 10
FILLER_CHANCES = (0.6, 0.3)
LOCAL_CHANCES = (1.0, 0.6, 0.25)
AI_EVENT_TYPES = ("NEWS_EVENT", "MARKET_SHOCK")

DESKS = {
    "markets": ("Markets", "Ledger Markets Desk"),
    "corporate": ("Corporate", "Ledger Business Wire"),
    "analysts": ("Analysts", "Street Brief"),
    "opinion": ("Opinion", "The Ticker Tape Blog"),
    "local": ("Around town", "Metro Business Journal"),
}

PEOPLE = ["Dana Whitfield", "Ravi Mehta", "Colleen Park", "Tomas Ruiz", "Imani Brooks", "Greg Olsen", "Lena Hoffman"]
CITIES = ["Omaha", "Raleigh", "Tampa", "Boise", "Madison", "Tucson", "Richmond", "Spokane"]
CONFERENCES = ["Midwest Growth", "Pacific Technology", "Global Consumer", "Northeast Industrials", "Healthcare Leaders"]
BANKS = ["Harlow & Finch", "Brightwater Capital", "Kessler Securities", "Redwood Research", "Alder Partners"]
DEPARTMENTS = ["facilities", "internal communications", "procurement", "regional sales", "corporate travel"]

# (desk, headline, body, loud)
FILLER = [
    ("corporate", "{name} to present at the {conference} investor conference next month",
     "Executives will give a scheduled overview of the business. The company said it does not plan to update its outlook at the event.", False),
    ("corporate", "{name} names {person} vice president of {department}",
     "{person} joins from a similar role elsewhere and will report to the chief operating officer. The company called the hire routine succession planning.", False),
    ("corporate", "{name} renews the lease on its {city} regional office",
     "The new lease runs for seven years on roughly the same floor space. Terms were not disclosed.", False),
    ("analysts", "{bank} reiterates hold rating on {ticker}",
     "The note left its estimates unchanged, saying the firm sees \"no reason to change our view this quarter.\"", False),
    ("corporate", "{name} publishes its annual sustainability report",
     "The report updates figures on energy use, recycling and supplier audits. It contains no financial guidance.", False),
    ("corporate", "{name} confirms the date of its annual shareholder meeting",
     "The meeting will be held online. The agenda covers the routine re-election of directors and ratification of the auditor.", False),
    ("corporate", "{name} opens a customer support center in {city}",
     "The site will employ about {staff} people when fully staffed, the company said.", False),
    ("corporate", "{name} updates its employee travel policy",
     "An internal memo seen by the Ledger asks staff to book economy fares for flights under six hours.", False),
    ("analysts", "{ticker} added to {bank}'s list of stocks to watch this quarter",
     "The list runs to more than {listed} names and is refreshed every quarter.", False),
    ("local", "{name} sponsors the {city} half marathon",
     "The company's logo will appear on race bibs and at the finish line. Around {runners} runners are expected.", False),
    ("corporate", "{name} files routine paperwork with regulators",
     "The filing covers standard disclosures the company makes every year. Nothing in it was new.", False),
    ("corporate", "{name} completes its previously announced office move in {city}",
     "The move was announced last year and finished on schedule.", False),
    ("corporate", "{name} launches a refreshed company website",
     "The redesign adds a new careers page and a simpler investor relations section.", False),
    ("analysts", "{bank} trims its {ticker} price target by ${cut}",
     "The firm kept its rating unchanged. The new target is still close to where the shares already trade.", False),
    ("corporate", "{name} extends its office supply contract with a long-time vendor",
     "The agreement covers paper, printer toner and breakroom supplies for three more years.", False),
    ("local", "{name} hosts an employee volunteer day in {city}",
     "Staff spent the afternoon at a food bank and a park cleanup.", False),
    ("analysts", "{bank} starts covering {ticker} with a neutral rating",
     "The analyst called the company \"well run\" and \"fairly valued\", with a price target a few percent from the current price.", False),
    ("opinion", "3 reasons {ticker} could double - and 3 reasons it won't",
     "Our columnist lays out the bull case and the bear case, then declines to pick one.", True),
    ("opinion", "Is {name} the next big thing? Readers weigh in",
     "We asked readers what they think of the stock. Answers ranged from \"love it\" to \"never heard of it\".", True),
    ("opinion", "Why everyone is suddenly talking about {ticker} again",
     "Mentions of the ticker on message boards ticked up this week. Nothing about the company has changed.", True),
    ("opinion", "The one chart every {name} shareholder needs to see",
     "The chart in question is the share price over the last ten years.", True),
    ("opinion", "Should you buy {ticker} before it's too late?",
     "The column never says what \"too late\" means, and concludes that it depends on your goals.", True),
    ("opinion", "{name} CEO spotted at a {city} coffee shop, fans say",
     "Posts online show someone who may be the chief executive ordering a latte. The company did not comment.", True),
]

LOCAL = [
    ("local", "Downtown {city} parking garage reopens after renovation",
     "The garage adds 40 spaces and new lighting. Monthly rates are unchanged.", False),
    ("local", "{city} chamber of commerce names new board members",
     "The board meets quarterly and focuses on small-business outreach.", False),
    ("local", "Small-business expo draws crowds in {city}",
     "Organizers counted more than {runners} visitors across two days of booths and panels.", False),
    ("local", "{city} brewery opens a second taproom",
     "The owners say the new location will focus on food and live music.", False),
    ("local", "Office furniture maker reports steady orders",
     "A spokesperson said demand was \"about the same as last year\".", False),
    ("local", "Trade group publishes its annual member directory",
     "This year's edition lists {listed} member firms, up slightly from last year.", False),
    ("local", "{city} airport adds a nonstop route to {city2}",
     "Service starts next quarter with four flights a week.", False),
    ("local", "Regional bank opens a branch in {city}",
     "The branch will offer extended Saturday hours.", False),
]

UP = ["{ticker} surges {pct:.1f}% in heavy trading", "{name} shares jump {pct:.1f}%", "{ticker} rallies {pct:.1f}%"]
DOWN = ["{ticker} tumbles {pct:.1f}% in heavy selling", "{name} shares slide {pct:.1f}%", "{ticker} drops {pct:.1f}%"]
MOVE_BODY = (
    "{name} closed at ${close:,.2f}, {direction} {pct:.1f}% on the day, on {volume_text}. "
    "The Ledger could not tie the move to any single announcement."
)
VOLUME = ["Unusual activity in {ticker}: volume hits {ratio:.1f}x its daily average", "{name} trades at {ratio:.1f}x its normal volume"]
VOLUME_BODY = (
    "Shares closed at ${close:,.2f} ({change:+.1f}%) as {ratio:.1f} times the usual number of shares "
    "changed hands. No company announcement came with the trading."
)
HIGH = ["{ticker} closes at a 52-week high", "{name} finishes at its highest close in a year"]
HIGH_BODY = "At ${close:,.2f}, the shares closed above every finish of the past twelve months."
LOW = ["{ticker} sinks to a 52-week low", "{name} closes at its lowest level in a year"]
LOW_BODY = "At ${close:,.2f}, the shares closed below every finish of the past twelve months."
STREAK_UP = "{name} rises for a {word} straight session"
STREAK_DOWN = "{name} falls for a {word} straight session"
STREAK_BODY = "The shares have closed {direction} {count} days in a row, {verb} {pct:.1f}% over the run to ${close:,.2f}."

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


def _story(day: str, ticker: str | None, company: str | None, desk: str, headline: str, body: str, major: bool, loud: bool) -> dict:
    section, source = DESKS[desk]
    return {
        "id": hashlib.sha1(f"{day}|{ticker}|{headline}".encode()).hexdigest()[:12],
        "date": day,
        "ticker": ticker,
        "company": company,
        "section": section,
        "source": source,
        "headline": headline,
        "body": body,
        "major": major,
        "loud": loud,
    }


def _public(story: dict) -> dict:
    return {key: value for key, value in story.items() if key != "loud"}


def _details(rng: random.Random) -> dict:
    first, second = rng.sample(CITIES, 2)
    return {
        "person": rng.choice(PEOPLE),
        "city": first,
        "city2": second,
        "conference": rng.choice(CONFERENCES),
        "bank": rng.choice(BANKS),
        "department": rng.choice(DEPARTMENTS),
        "cut": rng.randint(1, 3),
        "staff": rng.randrange(40, 400, 10),
        "listed": rng.randrange(30, 120),
        "runners": rng.randrange(800, 6000, 100),
    }


def _series(frame) -> dict:
    tail = frame.tail(ARCHIVE_DAYS + YEAR_BARS + 2)
    averages = tail["volume_avg20"].tolist() if "volume_avg20" in tail else [None] * len(tail)
    return {
        "dates": [day.isoformat() for day in tail["ts"]],
        "closes": [float(value) for value in tail["close"]],
        "volumes": [float(value or 0) for value in tail["volume"]],
        "averages": averages,
    }


def _volume_ratio(series: dict, i: int) -> float:
    average = series["averages"][i]
    if not average or not math.isfinite(average):
        return 0.0
    return series["volumes"][i] / average


def _ticker_stories(session_id: str, ticker: str, name: str, series: dict, i: int) -> list[dict]:
    day = series["dates"][i]
    closes = series["closes"]
    rng = _rng(session_id, ticker, day)
    close = closes[i]
    change = (close - closes[i - 1]) / closes[i - 1] * 100 if closes[i - 1] else 0.0
    ratio = _volume_ratio(series, i)
    fields = {"name": name, "ticker": ticker, "close": close}
    stories: list[dict] = []

    if abs(change) >= BIG_MOVE_PCT:
        volume_text = f"volume about {ratio:.1f} times its 20-day average" if ratio >= 1.5 else "roughly normal volume"
        headline = rng.choice(UP if change > 0 else DOWN).format(pct=abs(change), **fields)
        body = MOVE_BODY.format(direction="up" if change > 0 else "down", pct=abs(change), volume_text=volume_text, **fields)
        stories.append(_story(day, ticker, name, "markets", headline, body, True, True))
    elif ratio >= VOLUME_SPIKE:
        headline = rng.choice(VOLUME).format(ratio=ratio, **fields)
        stories.append(_story(day, ticker, name, "markets", headline, VOLUME_BODY.format(ratio=ratio, change=change, **fields), True, True))

    if i >= YEAR_BARS - 1:
        prior = closes[i - YEAR_BARS + 1:i]
        for best, templates, body in ((max(prior), HIGH, HIGH_BODY), (min(prior), LOW, LOW_BODY)):
            record = close > best if templates is HIGH else close < best
            last_seen = i - 1 - prior[::-1].index(best)
            if record and i - last_seen > RECORD_GAP_BARS:
                stories.append(_story(day, ticker, name, "markets", rng.choice(templates).format(**fields), body.format(**fields), True, True))

    rising = change > 0
    count = 0
    while i - count >= 1 and closes[i - count] != closes[i - count - 1] and (closes[i - count] > closes[i - count - 1]) == rising:
        count += 1
    if count in STREAK_DAYS:
        pct = abs(close / closes[i - count] - 1) * 100
        headline = (STREAK_UP if rising else STREAK_DOWN).format(word=STREAK_DAYS[count], **fields)
        body = STREAK_BODY.format(direction="higher" if rising else "lower", verb="gaining" if rising else "losing", count=count, pct=pct, **fields)
        stories.append(_story(day, ticker, name, "markets", headline, body, True, False))

    templates = rng.sample(FILLER, 2)
    for chance, (desk, headline, body, loud) in zip(FILLER_CHANCES, templates):
        if rng.random() < chance:
            details = _details(rng)
            stories.append(_story(day, ticker, name, desk, headline.format(**fields, **details), body.format(**fields, **details), False, loud))
    return stories


def _local_stories(session_id: str, day: str) -> list[dict]:
    rng = _rng(session_id, "local", day)
    stories = []
    for chance, (desk, headline, body, loud) in zip(LOCAL_CHANCES, rng.sample(LOCAL, len(LOCAL_CHANCES))):
        if rng.random() < chance:
            details = _details(rng)
            stories.append(_story(day, None, None, desk, headline.format(**details), body.format(**details), False, loud))
    return stories


def _ai_stories(events: list[dict], names: dict[str, str], since: date, through: date) -> list[dict]:
    stories = []
    for event in events:
        payload = event.get("payload") or {}
        headline = payload.get("headline")
        if event["event_type"] not in AI_EVENT_TYPES or not headline:
            continue
        day = event["sim_date"] if isinstance(event["sim_date"], date) else date.fromisoformat(str(event["sim_date"])[:10])
        if since <= day <= through:
            ticker = event["ticker"]
            stories.append(_story(day.isoformat(), ticker, names.get(ticker, ticker), "corporate", headline, payload.get("summary") or "", True, True))
    return stories


def _all_series(sources: dict, sim_date: date) -> dict[str, dict]:
    series = {}
    for ticker, source in sources.items():
        frame = source.history_through(sim_date)
        if len(frame) > 1:
            series[ticker] = _series(frame)
    return series


def headlines(session: dict, sim_date: date, events: list[dict], sources: dict) -> list[dict]:
    """Newest first. Stories from the same day are shuffled so the important one is not always on top."""
    session_id = str(session["id"])
    names = _company_names(list(sources))
    stories: list[dict] = []
    days: set[str] = set()
    for ticker, series in _all_series(sources, sim_date).items():
        count = len(series["dates"])
        for i in range(max(1, count - WINDOW_ROWS), count):
            stories.extend(_ticker_stories(session_id, ticker, names[ticker], series, i))
            days.add(series["dates"][i])
    for day in days:
        stories.extend(_local_stories(session_id, day))
    stories.extend(_ai_stories(events, names, sim_date - timedelta(days=AI_LOOKBACK_DAYS), sim_date))

    stories.sort(key=lambda story: (story["date"], _rng(session_id, story["id"]).random()), reverse=True)
    return [_public(story) for story in stories[:LIMIT]]


def edition(session: dict, sim_date: date, day: date | None, events: list[dict], sources: dict) -> dict:
    """One day's paper, snapped to the latest trading day on or before `day` that has been played."""
    session_id = str(session["id"])
    names = _company_names(list(sources))
    all_series = _all_series(sources, sim_date)
    dates = sorted({stamp for series in all_series.values() for stamp in series["dates"][-ARCHIVE_DAYS:]}, reverse=True)
    if not dates:
        return {"date": None, "sim_date": sim_date.isoformat(), "dates": [], "previous": None, "next": None,
                "lead": None, "stories": [], "closing_bell": []}

    wanted = day.isoformat() if day else dates[0]
    chosen = next((stamp for stamp in dates if stamp <= wanted), dates[-1])
    stories: list[dict] = []
    bell: list[dict] = []
    for ticker, series in all_series.items():
        if chosen not in series["dates"]:
            continue
        i = series["dates"].index(chosen)
        if i < 1:
            continue
        stories.extend(_ticker_stories(session_id, ticker, names[ticker], series, i))
        previous_close = series["closes"][i - 1]
        bell.append(
            {
                "ticker": ticker,
                "company": names[ticker],
                "close": series["closes"][i],
                "change_pct": (series["closes"][i] / previous_close - 1) * 100 if previous_close else None,
                "volume_ratio": round(_volume_ratio(series, i), 2) or None,
            }
        )
    stories.extend(_local_stories(session_id, chosen))
    chosen_day = date.fromisoformat(chosen)
    stories.extend(_ai_stories(events, names, chosen_day, chosen_day))

    unique = list({story["id"]: story for story in stories}.values())
    unique.sort(key=lambda story: _rng(session_id, "order", story["id"]).random())
    loud = [story for story in unique if story["loud"]]
    lead = (loud or unique)[0] if unique else None
    position = dates.index(chosen)
    return {
        "date": chosen,
        "sim_date": sim_date.isoformat(),
        "is_latest": position == 0,
        "dates": dates,
        "previous": dates[position + 1] if position + 1 < len(dates) else None,
        "next": dates[position - 1] if position > 0 else None,
        "lead": _public(lead) if lead else None,
        "stories": [_public(story) for story in unique if story is not lead],
        "closing_bell": bell,
    }
