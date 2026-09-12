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

# Retaught material is spaced out by session count, per symbol, so a pattern that keeps
# firing does not turn the feed into the same card over and over.
DEFAULT_REPEAT_GAP_DAYS = 20


@dataclass(frozen=True)
class Pattern:
    """One chart pattern, with the lesson text the coach teaches it from."""

    slug: str
    name: str  # must equal the `name` indicators.detect_signals emits
    family: str
    tension: str  # the question this pattern makes the player ask
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


# Ordered as a syllabus, not alphabetically: first the two moving-average crosses that
# define the trend, then the momentum oscillator, then participation. `choose` reads this
# order, so a session with several patterns on screen at once teaches them in this
# sequence rather than in whatever order the watchlist happens to be in.
PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        slug="golden_cross",
        name="Golden cross",
        family="trend",
        tension="Has the trend actually turned up, or is this a bounce?",
        what_it_is=(
            "The 20-day average price crossing above the 50-day average. A moving average "
            "is just the average close over the last N sessions, so a short one reacts "
            "first: when the fast line climbs through the slow line, recent prices have "
            "started to beat the older ones."
        ),
        how_to_spot=(
            "Two average lines are drawn over the price; find the faster one (SMA 20).",
            "Look for the point where the fast line crosses from below to above the slow one.",
            "It happens once, not continuously - a cross that happened weeks ago is history, not news.",
        ),
        why_it_matters=(
            "It is a slow, hard-to-fake confirmation that buyers have taken control of the "
            "trend. Trend followers enter on it and use the same averages as a place to exit."
        ),
        common_mistake=(
            "Treating it as a buy signal on its own. It is built from past prices, so it "
            "arrives after the move; if price is already far above both lines you are late."
        ),
        watch_next=(
            "Price holding above the 20-day average on the next few closes. A fast drop back "
            "below both averages marks the cross as a head-fake."
        ),
    ),
    Pattern(
        slug="death_cross",
        name="Death cross",
        family="trend",
        tension="Is the uptrend finished, or is this a pullback in disguise?",
        what_it_is=(
            "The 20-day average crossing below the 50-day average - the mirror image of the "
            "golden cross. Recent closes have stopped keeping up with the average of the "
            "last two months."
        ),
        how_to_spot=(
            "Find the two average lines over the price chart.",
            "Watch for the fast line (SMA 20) crossing the slow one (SMA 50) downward.",
            "Confirm the slope: both lines usually flatten or turn down just before it.",
        ),
        why_it_matters=(
            "It marks the moment the prevailing trend has changed character. Risk managers "
            "read it as a signal to cut position size rather than to short the stock."
        ),
        common_mistake=(
            "Panic-selling the same day. Crosses whipsaw in choppy markets, and the average "
            "already reflects weeks of falling prices - the damage is often done before you see it."
        ),
        watch_next=(
            "Whether price reclaims the 50-day average within a few sessions. Stalling below "
            "it keeps the downtrend intact."
        ),
    ),
    Pattern(
        slug="macd_bullish",
        name="MACD bullish crossover",
        family="momentum",
        tension="Is momentum turning up before price does?",
        what_it_is=(
            "The MACD line crossing above its own signal line. MACD measures how far the "
            "12-day average sits from the 26-day average; the signal line is a 9-day average "
            "of MACD itself. MACD crossing up through it means the gap is widening in the "
            "bullish direction."
        ),
        how_to_spot=(
            "Find the MACD panel under the price chart - two lines and a histogram.",
            "Watch where the faster MACD line crosses the signal line.",
            "The histogram flipping from red to green is the same event drawn as bars.",
        ),
        why_it_matters=(
            "It is the earliest of the common signals, which is why traders like it and why "
            "it lies to them: it turns before the trend is confirmed."
        ),
        common_mistake=(
            "Acting on the cross alone in a sideways market. MACD is built from averages of "
            "averages, so it lags and chops whenever price is going nowhere."
        ),
        watch_next=(
            "Volume on the follow-through day, and whether price takes out the last swing "
            "high. Without either, the cross tends to fade."
        ),
    ),
    Pattern(
        slug="macd_bearish",
        name="MACD bearish crossover",
        family="momentum",
        tension="Is the rally running out of push?",
        what_it_is=(
            "The MACD line crossing below its signal line. The upward gap between the 12-day "
            "and 26-day averages has stopped widening, which is what momentum stalling looks "
            "like before price admits it."
        ),
        how_to_spot=(
            "In the MACD panel, find the point where the MACD line crosses down through the "
            "signal line.",
            "The histogram bars shrink toward zero and then flip negative.",
            "Occurs many times within a longer uptrend, not just at tops.",
        ),
        why_it_matters=(
            "It is the standard warning to tighten up: take profits on part of a position or "
            "trail a stop, without assuming the whole trend is over."
        ),
        common_mistake=(
            "Reading every bearish cross as a top. In a strong uptrend a MACD cross down "
            "usually marks a pause, and selling everything on it is how people miss the rest "
            "of the move."
        ),
        watch_next=(
            "Whether the 50-day average holds. A cross down that never breaks that line is "
            "usually just consolidation."
        ),
    ),
    Pattern(
        slug="rsi_overbought",
        name="RSI overbought",
        family="momentum",
        tension="How much of the good news is already in the price?",
        what_it_is=(
            "The 14-day Relative Strength Index climbing above 70. RSI compares the size of "
            "recent gains with recent losses on a 0-100 scale, so above 70 means gains have "
            "dominated the last two weeks."
        ),
        how_to_spot=(
            "In the RSI panel, watch it cross up through the dashed 70 line.",
            "Note the slope: a fast run from below 50 to above 70 is the strong version.",
            "Check the price chart for a matching run - RSI spikes without a price move are rare.",
        ),
        why_it_matters=(
            "It is a measure of crowding, not of direction. Strong stocks can stay above 70 "
            "for weeks, but the easy part of the move is usually done."
        ),
        common_mistake=(
            "Shorting anything that goes overbought. In a genuine uptrend RSI 70 signals "
            "strength, and fading it repeatedly is how a winning stock is sold too early."
        ),
        watch_next=(
            "Whether RSI resets toward 50 on a shallow pullback (healthy) or price breaks "
            "down with it (the trend is failing)."
        ),
    ),
    Pattern(
        slug="rsi_oversold",
        name="RSI oversold",
        family="momentum",
        tension="Is this a discount or a trap?",
        what_it_is=(
            "RSI dropping below 30 - losses have dominated the last two weeks. It says the "
            "selling has been heavy and persistent, not that it is finished."
        ),
        how_to_spot=(
            "Watch RSI cross down through the dashed 30 line in its own panel.",
            "Compare with the price chart: a steep fall into the low 30s and below.",
            "Note whether it is the first dip or the third one - repeated oversold readings in "
            "a downtrend are a warning, not a gift.",
        ),
        why_it_matters=(
            "It is one of the few moments where the crowd is selling in a hurry, which is "
            "where both the bargains and the falling knives live."
        ),
        common_mistake=(
            "Buying purely because a number went below 30. Falling stocks can stay oversold "
            "for months; the indicator measures speed of decline, not value."
        ),
        watch_next=(
            "A higher low in price and RSI climbing back over 30. Without a turn in price, "
            "oversold is only oversold."
        ),
    ),
    Pattern(
        slug="volume_spike",
        name="Volume spike",
        family="participation",
        tension="Does anyone else believe this move?",
        what_it_is=(
            "A session with more than twice the 20-day average number of shares traded. "
            "Volume is the count of transactions, so a spike means an unusual number of "
            "buyers and sellers agreed on a price today."
        ),
        how_to_spot=(
            "The volume bars sit under the price chart - one bar per session.",
            "Look for a bar roughly double the height of the recent cluster around it.",
            "Read the direction of that same session's price candle, not the spike alone.",
        ),
        why_it_matters=(
            "Big volume means real conviction. Breakouts on thin volume are the classic "
            "failed breakout; the same breakout on heavy volume is far more likely to hold."
        ),
        common_mistake=(
            "Ignoring it because the price move looks small. Volume is the one indicator "
            "that is not derived from price, so it can disagree with the chart - and when it "
            "does, it is usually right."
        ),
        watch_next=(
            "Whether the next session holds the price the spike created. Giving it all back "
            "on light volume marks the spike as a one-off."
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
    # Syllabus order, so two patterns on the same bar are taught in a fixed sequence
    # rather than whichever order the watchlist happened to produce them in.
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
        # Unreachable for signals this codebase produces; a clear failure beats a KeyError
        # buried in a worker thread.
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
                # Which of the player's own stocks this pattern turned up in. That link is
                # the point: the lesson is about their chart, not a textbook example.
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
        # Imported here, like events.run, so the MCP subprocess stays optional and this
        # module never becomes an import-time dependency of the web app's boot path.
        from scripts.api import gemini_mcp_client

        payload = gemini_mcp_client.pattern_lesson(
            session_id,
            ticker,
            sim_date,
            pattern.name,
            signal=signal.get("name", ""),
            context=context,
        )
    except Exception as exc:  # noqa: BLE001 - any failure must still teach the lesson
        log.warning("Gemini pattern lesson failed: %s: %s", type(exc).__name__, exc)
        payload = None

    if not payload or payload.get("error"):
        payload = offline_lesson(pattern, ticker, sim_date, context)

    stored = {
        **payload,
        # Identity and provenance are the caller's to fix, not the model's to invent: a
        # hallucinated slug would silently break the progress panel that keys off it.
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
