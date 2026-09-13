"""The pattern curriculum the AI coach teaches from.

A signal firing is an event; a *lesson* is what turns it into knowledge. This module owns
two things and nothing else:

* What each pattern actually is, in the coach's own words. This text is the offline
  lesson *and* the reference handed to Gemini, so a lesson reads the same whether the API
  answered or not, and the model explains the pattern that is genuinely on the chart
  rather than inventing a syllabus of its own.
* Which pattern to teach next. This is the part that makes the coach a curriculum instead
  of a firehose: a pattern the player has never been shown is always worth a lesson, and
  one they have already met is only worth revisiting once enough sessions have passed.

The catalogs in `indicators.detect_signals` and `PATTERNS` are deliberately the same
strings. A signal with no lesson here is a bug in this file, not something to paper over,
so `lesson_for()` refusing to guess is the point.
"""

import logging
from dataclasses import dataclass
from datetime import date

import config
from scripts.database import database
from scripts.game import indicators

log = logging.getLogger(__name__)

DEFAULT_REPEAT_GAP_DAYS = 20


@dataclass(frozen=True)
class Pattern:
    """One chart pattern, with the lesson text the coach teaches it from."""

    slug: str
    name: str
    family: str
    tension: str
    what_it_is: str
    how_to_spot: tuple[str, ...]
    why_it_matters: str
    common_mistake: str
    watch_next: str

    def describe(self) -> dict:
        return {
            "slug": self.slug,
            "name": self.name,
            "family": self.family,
            "tension": self.tension,
        }

    def lesson(self) -> dict:
        """The lesson body, with no reference to a specific chart."""
        return {
            "title": self.name,
            "what_it_is": self.what_it_is,
            "how_to_spot": list(self.how_to_spot),
            "why_it_matters": self.why_it_matters,
            "common_mistake": self.common_mistake,
            "watch_next": self.watch_next,
        }


PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        slug="golden_cross",
        name="Golden cross",
        family="trend",
        tension="Is the stock starting to go up for real?",
        what_it_is=(
            "The price chart has two average lines: the average price over the last 20 days and "
            "over the last 50 days. A golden cross is when the 20-day line moves above the "
            "50-day line. It means recent prices are higher than older ones."
        ),
        how_to_spot=(
            "Find the two average lines on the price chart.",
            "Watch for the yellow 20-day line to cross above the blue 50-day line.",
            "It only counts on the day it happens. A cross from weeks ago is old news.",
        ),
        why_it_matters=(
            "It is a slow but steady sign that the stock has turned upward."
        ),
        common_mistake=(
            "Buying just because of the cross. The lines are built from old prices, so the "
            "stock may have already gone up a lot."
        ),
        watch_next=(
            "See if the price stays above the 20-day line for the next few days. If it drops "
            "below both lines, the cross was a false alarm."
        ),
    ),
    Pattern(
        slug="death_cross",
        name="Death cross",
        family="trend",
        tension="Is the stock starting to go down for real?",
        what_it_is=(
            "The opposite of a golden cross. The 20-day average line drops below the 50-day "
            "line. It means recent prices are lower than older ones."
        ),
        how_to_spot=(
            "Find the two average lines on the price chart.",
            "Watch for the yellow 20-day line to cross below the blue 50-day line.",
            "Both lines often flatten out or point down just before it happens.",
        ),
        why_it_matters=(
            "It warns that the stock has turned downward. Careful traders often own less of it."
        ),
        common_mistake=(
            "Selling in a panic that day. These crosses can flip back quickly, and much of the "
            "drop has usually already happened."
        ),
        watch_next=(
            "See if the price climbs back above the 50-day line in the next few days. If it "
            "stays below, the drop may keep going."
        ),
    ),
    Pattern(
        slug="macd_bullish",
        name="MACD bullish crossover",
        family="momentum",
        tension="Is the stock starting to speed up?",
        what_it_is=(
            "MACD shows whether a stock is speeding up or slowing down. When its blue line "
            "crosses above its yellow line, the stock is picking up speed upward."
        ),
        how_to_spot=(
            "Look at the MACD chart under the price chart.",
            "Find where the blue line crosses above the yellow line.",
            "The bars turn from red to green at the same time.",
        ),
        why_it_matters=(
            "It is one of the earliest signs that a stock may rise. Early signs are handy, but "
            "they are wrong more often."
        ),
        common_mistake=(
            "Trusting it when the price is just moving sideways. Then the lines cross back and "
            "forth and don't mean much."
        ),
        watch_next=(
            "See if the price keeps climbing over the next few days. If it doesn't, the cross "
            "will probably fade."
        ),
    ),
    Pattern(
        slug="macd_bearish",
        name="MACD bearish crossover",
        family="momentum",
        tension="Is the stock running out of steam?",
        what_it_is=(
            "The blue MACD line crosses below the yellow line. The stock is slowing down, often "
            "before its price starts to drop."
        ),
        how_to_spot=(
            "Look at the MACD chart under the price chart.",
            "Find where the blue line crosses below the yellow line.",
            "The bars shrink and turn from green to red.",
        ),
        why_it_matters=(
            "It is a warning sign. Many traders sell part of what they own, not all of it."
        ),
        common_mistake=(
            "Thinking every cross down means the top. When a stock is rising strongly, it is "
            "often just a short pause."
        ),
        watch_next=(
            "See if the price holds up. If it keeps falling, the warning was right."
        ),
    ),
    Pattern(
        slug="rsi_overbought",
        name="RSI overbought",
        family="momentum",
        tension="Has the stock gone up too fast?",
        what_it_is=(
            "RSI is a number from 0 to 100 that shows how fast a stock has moved over the last "
            "two weeks. Above 70 means it went up a lot, very fast."
        ),
        how_to_spot=(
            "Look at the RSI chart under the price chart.",
            "Watch the line cross above the dashed line at 70.",
            "The price chart should show a strong climb at the same time.",
        ),
        why_it_matters=(
            "The easy gains are often over. It doesn't mean the price will fall, just that the "
            "stock is running hot."
        ),
        common_mistake=(
            "Selling every time it happens. Strong stocks can stay above 70 for weeks and keep "
            "climbing."
        ),
        watch_next=(
            "See if RSI cools off toward 50 while the price holds steady (fine), or if the price "
            "falls too (trouble)."
        ),
    ),
    Pattern(
        slug="rsi_oversold",
        name="RSI oversold",
        family="momentum",
        tension="Is this a bargain or a trap?",
        what_it_is=(
            "RSI dropped below 30. That means the stock went down a lot, very fast, over the "
            "last two weeks."
        ),
        how_to_spot=(
            "Look at the RSI chart under the price chart.",
            "Watch the line cross below the dashed line at 30.",
            "Check if this is the first big drop or one of many. Drop after drop is a bad sign.",
        ),
        why_it_matters=(
            "People are selling in a hurry. Sometimes that makes a bargain, and sometimes the "
            "stock just keeps falling."
        ),
        common_mistake=(
            "Buying only because RSI is below 30. A falling stock can stay low for a long time."
        ),
        watch_next=(
            "Wait for the price to stop falling and for RSI to climb back above 30."
        ),
    ),
    Pattern(
        slug="volume_spike",
        name="Volume spike",
        family="participation",
        tension="Do lots of people agree with today's move?",
        what_it_is=(
            "Volume is how many shares were bought and sold in a day. A spike is a day with more "
            "than twice the usual amount."
        ),
        how_to_spot=(
            "Look at the grey bars along the bottom of the price chart.",
            "Find a bar about twice as tall as the ones around it.",
            "Check whether the price went up or down that same day.",
        ),
        why_it_matters=(
            "Lots of trading means lots of people care. A price move with big volume is more "
            "likely to last."
        ),
        common_mistake=(
            "Ignoring it because the price barely moved. Big volume can be an early clue."
        ),
        watch_next=(
            "See if the price holds the next day. If it slips back on quiet trading, the spike "
            "was a one-off."
        ),
    ),
)

_BY_NAME = {pattern.name: pattern for pattern in PATTERNS}
_BY_SLUG = {pattern.slug: pattern for pattern in PATTERNS}
_ORDER = {pattern.slug: index for index, pattern in enumerate(PATTERNS)}

LESSON_EVENT_TYPE = "PATTERN_LESSON"


def lesson_for(name: str) -> Pattern | None:
    """The pattern a detected signal belongs to, or None if the catalog is incomplete."""
    return _BY_NAME.get(name)


def by_slug(slug: str) -> Pattern | None:
    return _BY_SLUG.get(slug)


def catalog() -> list[dict]:
    return [pattern.describe() for pattern in PATTERNS]


def taught(event_log: list[dict]) -> set[str]:
    """Slugs already taught in this session, from its own event log."""
    return {
        str(entry["payload"].get("pattern"))
        for entry in event_log
        if entry.get("event_type") == LESSON_EVENT_TYPE and entry.get("payload")
    }


def taught_counts(event_log: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in event_log:
        if entry.get("event_type") != LESSON_EVENT_TYPE or not entry.get("payload"):
            continue
        slug = str(entry["payload"].get("pattern"))
        counts[slug] = counts.get(slug, 0) + 1
    return counts


def choose_new(signals: list[dict], event_log: list[dict]) -> dict | None:
    """The next signal worth teaching because the player has never seen its pattern.

    Only signals that actually fired are considered: the lesson has to point at something
    on the player's own chart, which is the difference between teaching and a glossary.
    """
    known = taught(event_log)
    fresh = [
        signal
        for signal in signals
        if lesson_for(signal["name"]) is not None and pattern_slug(signal) not in known
    ]
    if not fresh:
        return None
    return min(fresh, key=lambda s: _ORDER[pattern_slug(s)])


def choose_repeat(signals: list[dict], event_log: list[dict]) -> dict | None:
    """A signal worth revisiting: the pattern the session has taught least often."""
    counts = taught_counts(event_log)
    candidates = [signal for signal in signals if lesson_for(signal["name"])]
    if not candidates:
        return None
    return min(candidates, key=lambda s: (counts.get(pattern_slug(s), 0), _ORDER[pattern_slug(s)]))


def pattern_slug(signal: dict) -> str:
    pattern = lesson_for(signal.get("name", ""))
    if pattern is None:
        raise KeyError(f"no lesson for signal {signal.get('name')!r}")
    return pattern.slug


def progress(event_log: list[dict]) -> list[dict]:
    """The whole syllabus with what this session has covered so far, for the UI."""
    counts = taught_counts(event_log)
    first_seen: dict[str, tuple[date, str]] = {}
    for entry in event_log:
        if entry.get("event_type") != LESSON_EVENT_TYPE or not entry.get("payload"):
            continue
        slug = str(entry["payload"].get("pattern"))
        day = entry["sim_date"]
        day = day if isinstance(day, date) else date.fromisoformat(str(day))
        if slug not in first_seen or day < first_seen[slug][0]:
            first_seen[slug] = (day, str(entry.get("ticker") or ""))
    rows = []
    for pattern in PATTERNS:
        seen = first_seen.get(pattern.slug)
        rows.append(
            {
                **pattern.describe(),
                "taught": counts.get(pattern.slug, 0) > 0,
                "lessons": counts.get(pattern.slug, 0),
                "first_taught": seen[0].isoformat() if seen else None,
                "first_ticker": seen[1] if seen else None,
            }
        )
    return rows


def offline_lesson(pattern: Pattern, ticker: str, sim_date: date, context: dict | None = None) -> dict:
    """A lesson with no model behind it, so a keyless demo still teaches."""
    return {
        "pattern": pattern.slug,
        "name": pattern.name,
        "family": pattern.family,
        **pattern.lesson(),
        "ticker": ticker,
        "as_of": sim_date.isoformat(),
        "context": context or {},
        "source": "offline",
        "fictional": False,
    }


def repeat_gap_days() -> int:
    return int(getattr(config, "PATTERN_REPEAT_GAP_DAYS", DEFAULT_REPEAT_GAP_DAYS))


def teach(
    session_id: str,
    ticker: str,
    sim_date: date,
    signal: dict,
    frame,
) -> dict | None:
    """Explain the signal this chart just produced, and log the lesson.

    Mirrors `events.run`: the model when it answers, the local syllabus when it does not,
    so a room with no API key still gets taught. `frame` is the symbol's indicator frame
    through `sim_date`, so the lesson can quote the numbers actually on the player's
    chart instead of describing the pattern in the abstract.
    """
    pattern = lesson_for(signal.get("name", ""))
    if pattern is None:
        log.warning("signal %r has no lesson in the catalog", signal.get("name"))
        return None

    context = indicators.latest_row_summary(frame) if frame is not None else {}
    payload = None
    try:
        from scripts.api import gemini_mcp_client

        payload = gemini_mcp_client.pattern_lesson(
            session_id,
            ticker,
            sim_date,
            pattern.name,
            signal=signal.get("name", ""),
            context=context,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("Gemini pattern lesson failed: %s: %s", type(exc).__name__, exc)
        payload = None

    if not payload or payload.get("error"):
        payload = offline_lesson(pattern, ticker, sim_date, context)

    stored = {
        **payload,
        "pattern": pattern.slug,
        "name": pattern.name,
        "family": pattern.family,
        "tension": pattern.tension,
        "signal": signal.get("name"),
        "signal_message": signal.get("message", ""),
        "ticker": ticker.upper(),
        "as_of": sim_date.isoformat(),
        "context": context,
    }
    event_id = database.insert_mcp_event(
        session_id=session_id,
        ticker=ticker,
        sim_date=sim_date,
        event_type=LESSON_EVENT_TYPE,
        payload=stored,
    )
    stored["event_id"] = event_id
    return stored
