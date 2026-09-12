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
    #Dates are ISO (YYYY-MM-DD)
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
def log_ai_event(
    session_id: str, ticker: str, sim_date: str, event_type: str, payload: dict
) -> dict:
    #Persist a prediction or news event into TigerData mcp_events log
    #event_type must be PREDICTION or NEWS_EVENT
    
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
