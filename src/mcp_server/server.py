import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp.server.mcpserver import MCPServer

from mcp_server import gemini_tools
from scripts.database import database
from scripts.game import indicators

GROUNDING_LOOKBACK_DAYS = 200

mcp = MCPServer("stock-teacher")


def _parse(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _grounding_frame(ticker: str, as_of: date):
    start = as_of - timedelta(days=GROUNDING_LOOKBACK_DAYS)
    return indicators.compute_all(database.fetch_price_history(ticker, start, as_of))


@mcp.tool()
def get_price_history(ticker: str, start_date: str, end_date: str) -> dict:
    frame = indicators.compute_all(
        database.fetch_price_history(ticker, _parse(start_date), _parse(end_date))
    )
    return {
        "ticker": ticker.upper(),
        "count": len(frame),
        "rows": indicators.to_series(frame),
    }


@mcp.tool()
def predict_next_move(session_id: str, ticker: str, as_of_date: str) -> dict:
    as_of = _parse(as_of_date)
    frame = _grounding_frame(ticker, as_of)
    if frame.empty:
        return {"error": f"no stored history for {ticker} through {as_of_date}"}
    try:
        prediction = gemini_tools.generate_prediction(ticker.upper(), as_of, frame)
    except Exception as exc:
        return {"error": str(exc)}
    prediction["session_id"] = session_id
    return prediction


@mcp.tool()
def generate_market_event(session_id: str, ticker: str, as_of_date: str) -> dict:
    as_of = _parse(as_of_date)
    frame = _grounding_frame(ticker, as_of)
    if frame.empty:
        return {"error": f"no stored history for {ticker} through {as_of_date}"}
    try:
        event = gemini_tools.generate_news_event(ticker.upper(), as_of, frame)
    except Exception as exc:
        return {"error": str(exc)}
    event["session_id"] = session_id
    return event


@mcp.tool()
def generate_market_shock(
    session_id: str, ticker: str, as_of_date: str, scope: str = "ticker"
) -> dict:
    """A fictional, sized market event for the player's simulated future.

    Unlike generate_market_event, the result carries decay_days so the caller can apply
    the shock to the generated price series rather than just display the headline.

    `scope` is "ticker", "sector" or "market", and decides how the prompt is framed: a
    sector story has to be written to move every name the student holds in that sector,
    which the tool works out from the session's own watchlist and the ticker catalog.
    """
    as_of = _parse(as_of_date)
    ticker = ticker.upper()
    scope = (scope or "ticker").strip().lower()
    if scope not in ("ticker", "sector", "market"):
        return {"error": f"unknown scope {scope!r}; expected ticker, sector or market"}

    frame = _grounding_frame(ticker, as_of)
    if frame.empty:
        return {"error": f"no stored history for {ticker} through {as_of_date}"}

    watchlist = database.session_tickers(session_id) or [ticker]
    sectors = database.ticker_sectors(watchlist)
    sector = sectors.get(ticker, {}).get("sector") or None
    try:
        shock = gemini_tools.generate_market_shock(
            ticker, as_of, frame, scope=scope, sector=sector, peers=watchlist
        )
    except Exception as exc:
        return {"error": str(exc)}
    shock["session_id"] = session_id
    return shock


@mcp.tool()
def explain_pattern(
    session_id: str,
    ticker: str,
    as_of_date: str,
    pattern: str,
    signal: str = "",
) -> dict:
    """Teach the chart pattern behind a signal that just fired on the player's chart.

    `pattern` is a pattern name from the local syllabus (see scripts/game/patterns.py), not
    free text: the curriculum decides *what* gets taught and in what order, and this tool
    only decides how well it is explained. An unrecognised name is refused rather than
    passed to the model, which would otherwise happily invent a lesson for it.
    """
    from scripts.game import patterns

    entry = patterns.lesson_for(pattern) or patterns.by_slug(pattern)
    if entry is None:
        known = ", ".join(candidate.name for candidate in patterns.PATTERNS)
        return {"error": f"unknown pattern {pattern!r}; expected one of: {known}"}

    as_of = _parse(as_of_date)
    frame = _grounding_frame(ticker.upper(), as_of)
    if frame.empty:
        return {"error": f"no stored history for {ticker} through {as_of_date}"}

    context = indicators.latest_row_summary(frame)
    reference = {
        "slug": entry.slug,
        "name": entry.name,
        "family": entry.family,
        "tension": entry.tension,
        **entry.lesson(),
    }
    try:
        lesson = gemini_tools.generate_pattern_lesson(
            ticker.upper(), as_of, frame, reference, signal or entry.name, context
        )
    except Exception as exc:
        return {"error": str(exc)}
    lesson["session_id"] = session_id
    lesson["context"] = context
    return lesson


@mcp.tool()
def log_ai_event(
    session_id: str, ticker: str, sim_date: str, event_type: str, payload: dict
) -> dict:
    
    event_id = database.insert_mcp_event(
        session_id=session_id,
        ticker=ticker,
        sim_date=_parse(sim_date),
        event_type=event_type,
        payload=payload,
    )
    return {"id": event_id, "logged": True}


if __name__ == "__main__":
    mcp.run("stdio")
