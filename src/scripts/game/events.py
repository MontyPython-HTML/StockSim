"""Random market events that actually move the simulated chart.

A headline on its own is just text. What makes the simulated future feel like a market
is that a news event bends the forward curve: Gemini invents a plausible, entirely
fictional story, scores its sentiment and magnitude, and that score is written into the
synthetic bars as a decaying shock.

A story has a *scope*, and that is what makes a basket behave like a basket rather than
ten unrelated charts:

* `ticker` - company news, and only that company moves.
* `sector` - a sector story, so every watched name in that sector reprices together.
* `market` - a macro story, so the whole session moves at once.

Real newswires work this way, and it is the lesson the app is trying to teach: a
portfolio of five semiconductor names is not five bets, it is one bet sized five times.

Two paths produce an event:

* `run()` asks Gemini (through the MCP server) and falls back to a locally generated
  headline when the API is unavailable, so a room with no key still sees events fire.
* `apply()` takes numbers straight from the caller, which is what the API endpoint uses
  to inject a specific event during a demo.

Both end up in `mcp_events` as `MARKET_SHOCK` rows whose id is stamped onto every bar
the shock touched.
"""

import hashlib
import logging
import random
from datetime import date

import config
from scripts.database import database
from scripts.game import simulation

log = logging.getLogger(__name__)

SCOPE_TICKER = "ticker"
SCOPE_SECTOR = "sector"
SCOPE_MARKET = "market"
SCOPES = (SCOPE_TICKER, SCOPE_SECTOR, SCOPE_MARKET)

_COMPANY_HEADLINES: list[tuple[str, str, float]] = [
    ("{ticker} beats on earnings and raises full-year guidance", "Revenue and margin both came in ahead of consensus.", 0.6),
    ("{ticker} unveils next-generation AI accelerator", "The company says the new part ships to cloud partners next quarter.", 0.55),
    ("{ticker} announces a $10B share buyback", "The board authorised the largest repurchase in company history.", 0.45),
    ("{ticker} signs a multi-year government cloud contract", "The deal is reported to be worth several billion dollars.", 0.4),
    ("Regulators open a probe into {ticker}'s cloud practices", "Antitrust staff are reviewing bundling complaints from rivals.", -0.5),
    ("A key {ticker} supplier reports shipment delays", "Lead times on a critical component have slipped by weeks.", -0.45),
    ("Analysts downgrade {ticker} on margin concerns", "Two sell-side desks cut targets, citing slowing enterprise spend.", -0.4),
    ("{ticker} CFO departs abruptly", "The company gave no reason and named an interim finance chief.", -0.35),
    ("Insider selling at {ticker} draws scrutiny", "Several executives exercised and sold into the recent rally.", -0.3),
]

_SECTOR_HEADLINES: list[tuple[str, str, float]] = [
    ("Chip demand cools as cloud capex guidance is trimmed", "Three hyperscalers signalled slower server orders for the next two quarters.", -0.55),
    ("Enterprise software budgets come in ahead of plan", "IT spending surveys show the strongest renewal rates in two years.", 0.5),
    ("Antitrust push widens to platform practices across the sector", "Regulators in two jurisdictions opened parallel reviews into bundling.", -0.5),
    ("A key supplier outage ripples through the whole sector", "Lead times doubled overnight after a fire at a shared fabrication site.", -0.45),
    ("Sector rotation drags high-multiple names lower", "Funds rotated into defensives, punishing anything trading above 25x earnings.", -0.4),
    ("Export rules tighten on advanced components", "New licensing requirements add friction to the sector's largest market.", -0.45),
    ("Data-centre buildout is running ahead of schedule", "Power and cooling procurement points to faster capacity growth.", 0.45),
    ("Wage inflation squeezes engineering margins industry-wide", "Layoff announcements broadened across the sector this week.", -0.3),
]

_MARKET_HEADLINES: list[tuple[str, str, float]] = [
    ("A hot inflation print resets rate expectations", "Futures repriced a later first cut, and high-multiple names led the move down.", -0.6),
    ("Central bank signals a faster path of rate cuts", "The statement was read as dovish, and risk assets gapped higher.", 0.55),
    ("Global growth forecasts revised down across the board", "The IMF cut next year's outlook for every major economy.", -0.5),
    ("A surprise liquidity crunch hits risk assets everywhere", "Funding spreads blew out and dealers pulled balance sheet.", -0.65),
    ("Bond yields ease and equities rally broadly", "The ten-year fell through a level traders had been watching all quarter.", 0.45),
]


def _pick_scope(rng: random.Random) -> str:
    """Which kind of story this will be, weighted toward company news."""
    roll = rng.random()
    if roll < config.SHOCK_MARKET_PROBABILITY:
        return SCOPE_MARKET
    if roll < config.SHOCK_MARKET_PROBABILITY + config.SHOCK_SECTOR_PROBABILITY:
        return SCOPE_SECTOR
    return SCOPE_TICKER


def _offline_event(
    ticker: str, session_id: str, sim_date: date, scope: str, sector: str | None
) -> dict:
    """A deterministic-per-session headline, used when Gemini cannot be reached.

    Seeded so the same session and day always produces the same story: a reload must not
    quietly redraw a headline the player already traded on.
    """
    rng = random.Random(f"offline:{session_id}:{sim_date.isoformat()}:{ticker}:{scope}")
    if scope == SCOPE_MARKET:
        template, summary, sentiment = rng.choice(_MARKET_HEADLINES)
        headline = template
        magnitude = round(rng.uniform(0.45, 0.85), 2)
        lesson = (
            "When the whole market moves together, everything you own moves with it. "
            "Sizing down before macro days is a real decision, not a cop-out."
        )
    elif scope == SCOPE_SECTOR:
        template, summary, sentiment = rng.choice(_SECTOR_HEADLINES)
        headline = template
        magnitude = round(rng.uniform(0.4, 0.8), 2)
        lesson = (
            "A sector story hits every name you hold in that sector at once. Check how "
            "much of your book is really the same bet before you call it diversified."
        )
    else:
        template, summary, sentiment = rng.choice(_COMPANY_HEADLINES)
        headline = template.format(ticker=ticker)
        magnitude = round(rng.uniform(0.35, 0.75), 2)
        lesson = (
            "A headline is information, not an instruction. Check whether the chart was "
            "already leaning this way before you size a trade on it."
        )
    return {
        "ticker": ticker,
        "scope": scope,
        "sector": sector,
        "as_of": sim_date.isoformat(),
        "headline": headline,
        "summary": summary,
        "sentiment": round(sentiment * rng.uniform(0.7, 1.3), 3),
        "magnitude": magnitude,
        "decay_days": config.SHOCK_DECAY_DAYS,
        "lesson": lesson,
        "fictional": True,
        "source": "offline",
    }


def _from_gemini(
    payload: dict | None,
    ticker: str,
    session_id: str,
    sim_date: date,
    scope: str,
    sector: str | None,
) -> dict:
    if not payload or payload.get("error"):
        reason = (payload or {}).get("error", "Gemini unavailable")
        log.info("market event falling back to offline generator: %s", reason)
        return _offline_event(ticker, session_id, sim_date, scope, sector)
    decay = payload.get("decay_days")
    return {
        "ticker": ticker,
        "scope": scope,
        "sector": payload.get("sector") or sector,
        "as_of": sim_date.isoformat(),
        "headline": payload.get("headline", ""),
        "summary": payload.get("summary", ""),
        "sentiment": float(payload.get("sentiment", 0.0)),
        "magnitude": float(payload.get("magnitude", 0.3)),
        "decay_days": int(decay) if isinstance(decay, (int, float)) else config.SHOCK_DECAY_DAYS,
        "lesson": payload.get("lesson", ""),
        "fictional": True,
        "source": "gemini",
    }


def resolve_targets(sources: dict, event: dict, sectors: dict | None = None) -> list[str]:
    """Which symbols a story lands on.

    A ticker story names itself; a sector story takes every watched name filed under that
    sector; a macro story takes the whole session. Others fall back to the primary symbol
    so an unlabelled event still moves something.
    """
    scope = str(event.get("scope") or SCOPE_TICKER).lower()
    primary = str(event.get("ticker") or "").upper()
    sectors = sectors or {}

    if scope == SCOPE_MARKET:
        return list(sources)
    if scope == SCOPE_SECTOR:
        wanted = str(event.get("sector") or "").strip().lower()
        picked = [
            ticker
            for ticker in sources
            if wanted and str(sectors.get(ticker, {}).get("sector", "")).lower() == wanted
        ]
        if picked:
            return picked
        return [primary] if primary in sources else []
    return [primary] if primary in sources else []


def _peer_scale(session_id: str, event: dict, ticker: str) -> float:
    """A stable per-symbol multiplier for a shared story.

    Deterministic from the story and the symbol, so the same headline always bends the
    same chart by the same amount, but different enough between names that a sector move
    does not look like one line copy-pasted five times.
    """
    seed = f"{session_id}:{event.get('headline', '')}:{ticker}"
    unit = int.from_bytes(hashlib.sha256(seed.encode()).digest()[:4], "big") / 0xFFFFFFFF
    return 1.0 + config.SHOCK_PEER_SPREAD * (2 * unit - 1)


def should_fire(event_log: list[dict], ticker: str, source, sim_date: date) -> bool:
    """Spacing and dice roll, same shape as the prediction/news cadence in engine.py.

    Spacing is measured per symbol and counts shocks it was merely *caught up in*: a
    sector story that moved six names should space out all six, or a basket gets hit
    repeatedly by the same cadence.
    """
    shocks = [
        event
        for event in event_log
        if event["event_type"] == "MARKET_SHOCK"
        and event["payload"].get("applied")
        and (event["ticker"] == ticker or ticker in (event["payload"].get("affected_tickers") or []))
    ]
    if shocks:
        last = max(event["sim_date"] for event in shocks)
        if source.trading_days_between(last, sim_date) < config.SHOCK_MIN_DAYS_BETWEEN:
            return False
    return random.random() < config.SHOCK_PROBABILITY


def apply(
    session_id: str,
    sources: dict,
    sim_date: date,
    event: dict,
    sectors: dict | None = None,
    event_id: int | None = None,
) -> dict:
    """Bend the simulated future and log the headline. Returns the stored payload.

    `sources` is the whole session keyed by symbol; the event's scope decides which of
    them the story lands on, and one row is logged for the story no matter how many
    charts it bends.
    """
    primary = str(event.get("ticker") or "").upper()
    targets = [ticker for ticker in resolve_targets(sources, event, sectors) if ticker in sources]

    if event_id is None:
        event_id = database.insert_mcp_event(
            session_id=session_id,
            ticker=primary or (targets[0] if targets else "MARKET"),
            sim_date=sim_date,
            event_type="MARKET_SHOCK",
            payload=event,
        )

    affected: list[dict] = []
    total = 0
    for ticker in targets:
        source = sources[ticker]
        if getattr(source, "kind", None) != "simulated":
            continue
        scale = _peer_scale(session_id, event, ticker)
        touched = simulation.apply_shock(
            session_id=session_id,
            config_=source.config,
            from_date=sim_date,
            sentiment=event["sentiment"],
            magnitude=max(0.0, min(1.0, float(event["magnitude"]) * scale)),
            decay_days=event.get("decay_days", config.SHOCK_DECAY_DAYS),
            event_id=event_id,
        )
        source.refresh()
        affected.append(
            {"ticker": ticker, "bars_affected": touched, "magnitude": round(float(event["magnitude"]) * scale, 4)}
        )
        total += touched

    stored = {
        **event,
        "applied": total > 0,
        "bars_affected": total,
        "affected": affected,
        "affected_tickers": [entry["ticker"] for entry in affected],
        "event_id": event_id,
    }
    database.update_mcp_event_payload(event_id, stored)
    return stored


def run(
    session_id: str,
    sources: dict,
    sim_date: date,
    ticker: str,
    sectors: dict | None = None,
) -> dict | None:
    """Background entry point: ask Gemini for a headline, then apply it.

    This runs off the request path, so by the time Gemini answers the player may have
    advanced past the day the event was queued for. The shock is clamped to the current
    sim_date so it can never rewrite candles the player has already traded through.
    """
    sectors = sectors or {}
    phase = random.Random(f"{session_id}:{ticker}:{sim_date.isoformat()}")
    scope = _pick_scope(phase)
    sector = sectors.get(ticker, {}).get("sector") or None

    try:
        from scripts.api import gemini_mcp_client

        payload = gemini_mcp_client.market_shock(session_id, ticker, sim_date, scope=scope)
    except Exception as exc:  # noqa: BLE001
        log.warning("Gemini market shock failed: %s: %s", type(exc).__name__, exc)
        payload = None

    session = database.get_session(session_id)
    if not session:
        return None
    effective = max(sim_date, session["sim_date"])

    event = _from_gemini(payload, ticker, session_id, effective, scope, sector)
    if not config.SIMULATION_OFFLINE_EVENTS and event["source"] == "offline":
        return None
    return apply(session_id, sources, effective, event, sectors)
