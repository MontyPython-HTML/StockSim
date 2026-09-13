"""Training levels: short runs on real history that hand the player one tool at a time.

Each level picks a stretch of history where the tool it teaches actually fires, switches off
every panel it is not teaching, and grades each trade against a simple rule for that tool.
"""

import random
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import numpy as np
import pandas as pd

from scripts.database import database
from scripts.game import patterns, price_cache

STARTING_CASH = Decimal("10000")
WARMUP_BARS = 60
EARLIEST_START = date(2010, 1, 1)
ACT_BUFFER = 10
WINDOW_TRIES = 300

RSI_BUY, RSI_SELL = 35, 65
MACD_FRESH_BARS = 3
COMBO_FRESH_BARS = 5
COMBO_LOOKBACK = 10
COMBO_RSI_BUY, COMBO_RSI_SELL = 40, 60

SINGLE_POOL = ["AAPL", "MSFT", "NVDA", "AMZN", "COST", "INTC", "CSCO", "ORCL", "ADBE", "QCOM", "AMD", "SBUX", "PEP", "AMGN", "TXN", "MU"]
BASKET_POOL = ["AAPL", "MSFT", "NVDA", "AMZN", "INTC", "COST", "PEP", "SBUX", "ROST", "AMGN", "GILD", "HON", "CSX", "ADP", "AEP", "XEL"]

ALL_PATTERNS = tuple(pattern.slug for pattern in patterns.PATTERNS)


@dataclass(frozen=True)
class Level:
    number: int
    slug: str
    title: str
    tagline: str
    tools: tuple[str, ...]
    panels: frozenset[str]
    patterns: tuple[str, ...]
    rules: tuple[str, ...]
    basket_size: int
    trading_days: int
    follow_target: int
    brief: tuple[str, ...]
    buy_rule: str
    sell_rule: str
    needs: tuple[tuple[str, int], ...] = ()
    beat_basket: bool = False

    def goal_labels(self) -> list[str]:
        return [
            f"Play all {self.trading_days} days",
            _follow_label(self),
            BEAT_LABEL if self.beat_basket else PROFIT_LABEL,
        ]

    def describe(self) -> dict:
        return {
            "number": self.number,
            "count": len(LEVELS),
            "slug": self.slug,
            "title": self.title,
            "tagline": self.tagline,
            "tools": list(self.tools),
            "panels": sorted(self.panels),
            "patterns": list(self.patterns),
            "brief": list(self.brief),
            "buy_rule": self.buy_rule,
            "sell_rule": self.sell_rule,
            "trading_days": self.trading_days,
            "basket_size": self.basket_size,
            "goal_labels": self.goal_labels(),
        }


PROFIT_LABEL = "End with more money than you started with"
BEAT_LABEL = "Do better than buying every stock on day one and holding"

LEVELS: tuple[Level, ...] = (
    Level(
        number=1,
        slug="rsi",
        title="RSI only",
        tagline="Learn to read one gauge: RSI.",
        tools=("RSI",),
        panels=frozenset({"rsi", "signals", "patterns"}),
        patterns=("rsi_overbought", "rsi_oversold"),
        rules=("rsi",),
        basket_size=1,
        trading_days=90,
        follow_target=2,
        brief=(
            "A stock is a small piece of a company. Its price goes up and down every day. You make "
            "money when you buy a stock and later sell it for more than you paid.",
            "RSI is a number from 0 to 100. It shows how fast a stock has gone up or down over "
            "the last two weeks.",
            "Below 30 means the stock dropped a lot, fast, so it might bounce back up. Above 70 "
            "means it climbed a lot, fast, so it might dip.",
            "In this level you only see the price and RSI. Buy when RSI is low. Sell when RSI is high.",
        ),
        buy_rule=f"Buy when RSI is {RSI_BUY} or lower.",
        sell_rule=f"Sell when RSI is {RSI_SELL} or higher.",
        needs=(("rsi_buy", 3), ("rsi_sell", 3)),
    ),
    Level(
        number=2,
        slug="macd",
        title="MACD only",
        tagline="Learn to spot when a stock speeds up or slows down.",
        tools=("MACD",),
        panels=frozenset({"macd", "signals", "patterns"}),
        patterns=("macd_bullish", "macd_bearish"),
        rules=("macd",),
        basket_size=1,
        trading_days=90,
        follow_target=2,
        brief=(
            "MACD shows whether a stock is speeding up or slowing down. Its chart has a blue "
            "line and a yellow line.",
            "When the blue line crosses above the yellow line, the bars turn green and the stock "
            "is picking up speed. When it crosses below, the bars turn red and the stock is slowing down.",
            "Timing matters. A cross is most useful in the first few days after it happens.",
        ),
        buy_rule=f"Buy within {MACD_FRESH_BARS} days after the blue line crosses above the yellow line.",
        sell_rule=f"Sell within {MACD_FRESH_BARS} days after the blue line crosses below the yellow line.",
        needs=(("macd_buy", 3), ("macd_sell", 3)),
    ),
    Level(
        number=3,
        slug="basket",
        title="The basket",
        tagline="Don't put all your money in one stock.",
        tools=("Basket chart", "Account value"),
        panels=frozenset({"basket"}),
        patterns=(),
        rules=("diversify",),
        basket_size=5,
        trading_days=120,
        follow_target=1,
        brief=(
            "The basket chart starts every stock at 100 so you can compare them fairly. A line at "
            "110 means that stock is up 10%.",
            "Companies in the same industry often move together. Owning different kinds of "
            "companies helps when one of them has a bad day.",
            "There's no RSI or MACD here. Spread your money out and watch your account value.",
        ),
        buy_rule="Own at least 3 stocks from 2 or more different industries at the same time.",
        sell_rule="If one stock grows into most of your money, sell some of it.",
    ),
    Level(
        number=4,
        slug="rsi_macd",
        title="RSI + MACD",
        tagline="Only act when both gauges agree.",
        tools=("RSI", "MACD"),
        panels=frozenset({"rsi", "macd", "signals", "patterns"}),
        patterns=("macd_bullish", "macd_bearish", "rsi_overbought", "rsi_oversold"),
        rules=("combo",),
        basket_size=1,
        trading_days=120,
        follow_target=2,
        brief=(
            "RSI and MACD each get it wrong sometimes. Using both together cuts down on bad calls.",
            f"To buy, you want RSI to have been low ({COMBO_RSI_BUY} or less) in the last "
            f"{COMBO_LOOKBACK} days, and the blue MACD line to have just crossed above the yellow line.",
            f"To sell, you want RSI to have been high ({COMBO_RSI_SELL} or more) in the last "
            f"{COMBO_LOOKBACK} days, and the blue MACD line to have just crossed below the yellow line.",
        ),
        buy_rule=(
            f"Buy within {COMBO_FRESH_BARS} days of MACD crossing up, if RSI was "
            f"{COMBO_RSI_BUY} or lower in the last {COMBO_LOOKBACK} days."
        ),
        sell_rule=(
            f"Sell within {COMBO_FRESH_BARS} days of MACD crossing down, if RSI was "
            f"{COMBO_RSI_SELL} or higher in the last {COMBO_LOOKBACK} days."
        ),
        needs=(("combo_buy", 1), ("combo_sell", 1)),
    ),
    Level(
        number=5,
        slug="everything",
        title="Everything",
        tagline="Use everything you've learned.",
        tools=("RSI", "MACD", "Basket", "Average price lines", "Volume", "Daily Ledger", "AI coach"),
        panels=frozenset({"rsi", "macd", "basket", "trend", "volume", "news", "ai", "signals", "patterns"}),
        patterns=ALL_PATTERNS,
        rules=("rsi", "macd", "combo"),
        basket_size=4,
        trading_days=150,
        follow_target=3,
        brief=(
            "Now you get every tool: RSI, MACD, the basket chart, average price lines, volume, "
            "the news and the AI coach.",
            "A trade counts if it follows any rule from the earlier levels.",
            "For the last star, you have to do better than someone who bought all four stocks on "
            "day one and never sold.",
        ),
        buy_rule=f"Buy when RSI is {RSI_BUY} or lower, when MACD crosses up, or both.",
        sell_rule=f"Sell when RSI is {RSI_SELL} or higher, when MACD crosses down, or both.",
        needs=(("rsi_buy", 2), ("rsi_sell", 2)),
        beat_basket=True,
    ),
)

_BY_NUMBER = {level.number: level for level in LEVELS}
_sectors: dict[str, str] = {}


def _follow_label(level: Level) -> str:
    if "diversify" in level.rules:
        return "Own 3 stocks from 2 different industries at once"
    return f"Make {level.follow_target} trades that follow the rule"


def _plural(count: int, word: str, many: str | None = None) -> str:
    return f"{count} {word if count == 1 else (many or word + 's')}"


def get(number) -> Level | None:
    try:
        return _BY_NUMBER.get(int(number)) if number is not None else None
    except (TypeError, ValueError):
        return None


def catalog() -> list[dict]:
    return [level.describe() for level in LEVELS]


def shows(level: Level | None, panel: str) -> bool:
    return level is None or panel in level.panels


def allows_signal(level: Level, signal: dict) -> bool:
    pattern = patterns.lesson_for(signal.get("name", ""))
    return pattern is not None and pattern.slug in level.patterns


def filter_patterns(level: Level | None, rows: list[dict]) -> list[dict]:
    return rows if level is None else [row for row in rows if row["slug"] in level.patterns]



def setups(frame: pd.DataFrame) -> pd.DataFrame:
    """Per bar, whether each rule would call it a buy or a sell."""
    rsi = frame["rsi14"]
    hist = frame["macd_hist"]

    def crossed_up(bars: int) -> pd.Series:
        return (hist > 0) & (hist.shift(1).rolling(bars, min_periods=1).min() <= 0)

    def crossed_down(bars: int) -> pd.Series:
        return (hist < 0) & (hist.shift(1).rolling(bars, min_periods=1).max() >= 0)

    lowest = rsi.rolling(COMBO_LOOKBACK, min_periods=1).min()
    highest = rsi.rolling(COMBO_LOOKBACK, min_periods=1).max()
    return pd.DataFrame(
        {
            "rsi_buy": rsi <= RSI_BUY,
            "rsi_sell": rsi >= RSI_SELL,
            "macd_buy": crossed_up(MACD_FRESH_BARS),
            "macd_sell": crossed_down(MACD_FRESH_BARS),
            "combo_buy": crossed_up(COMBO_FRESH_BARS) & (lowest <= COMBO_RSI_BUY),
            "combo_sell": crossed_down(COMBO_FRESH_BARS) & (highest >= COMBO_RSI_SELL),
        },
        index=frame.index,
    )


def _bars_since_cross(hist: pd.Series) -> int | None:
    signs = (hist > 0).tolist()
    values = hist.tolist()
    for back in range(len(values) - 1, 0, -1):
        if pd.isna(values[back - 1]):
            return None
        if signs[back] != signs[back - 1]:
            return len(values) - 1 - back
    return None


def _when(bars: int) -> str:
    return "the same day" if bars == 0 else f"{_plural(bars, 'day')} before"


def _review(level: Level, frame: pd.DataFrame, side: str) -> tuple[bool, str]:
    tail = frame.tail(COMBO_LOOKBACK + COMBO_FRESH_BARS + 30)
    flags = setups(tail).iloc[-1]
    buying = side == "BUY"
    key = "buy" if buying else "sell"
    matched = [rule for rule in level.rules if bool(flags[f"{rule}_{key}"])]

    rsi = tail["rsi14"].iloc[-1]
    hist = tail["macd_hist"]
    since = _bars_since_cross(hist)
    above = bool(hist.iloc[-1] > 0)
    if since is None:
        macd_text = f"The MACD blue line was {'above' if above else 'below'} the yellow line"
    else:
        macd_text = f"MACD crossed {'up' if above else 'down'} {_when(since)}"
    rsi_text = "RSI wasn't ready yet" if pd.isna(rsi) else f"RSI was {rsi:.0f}"
    direction = "up" if buying else "down"

    if level.rules == ("rsi",):
        zone = f"{RSI_BUY} or lower" if buying else f"{RSI_SELL} or higher"
        why = f"{rsi_text}. The rule says {zone}."
    elif level.rules == ("macd",):
        why = (
            f"{macd_text}. Good timing."
            if matched
            else f"{macd_text}. The rule says within {MACD_FRESH_BARS} days of a cross {direction}."
        )
    elif level.rules == ("combo",):
        window = tail["rsi14"].tail(COMBO_LOOKBACK)
        extreme = window.min() if buying else window.max()
        word = "Lowest" if buying else "Highest"
        extreme_text = "not ready yet" if pd.isna(extreme) else f"{extreme:.0f}"
        why = f"{macd_text}. {word} RSI in the last {COMBO_LOOKBACK} days: {extreme_text}."
    else:
        names = {"rsi": "the RSI rule", "macd": "the MACD rule", "combo": "the RSI + MACD rule"}
        why = f"{rsi_text}. {macd_text}."
        if matched:
            why += f" You followed {' and '.join(names[rule] for rule in matched)}."
    return bool(matched), why



def _sector_of(tickers: list[str]) -> dict[str, str]:
    missing = [ticker for ticker in tickers if ticker not in _sectors]
    if missing:
        try:
            found = database.ticker_sectors(missing)
        except Exception:  # noqa: BLE001
            found = {}
        for ticker in missing:
            _sectors[ticker] = (found.get(ticker) or {}).get("sector") or "Other"
    return {ticker: _sectors[ticker] for ticker in tickers}


def _ordered(level: Level, flags: pd.DataFrame, start: int, stop: int) -> bool:
    """A buy setup shows up before the last sell setup, so the player can do both in order."""
    if not level.needs:
        return True
    buy_column, sell_column = level.needs[0][0], level.needs[1][0]
    buys = np.flatnonzero(flags[buy_column].to_numpy()[start:stop])
    sells = np.flatnonzero(flags[sell_column].to_numpy()[start:stop])
    return len(buys) > 0 and len(sells) > 0 and buys[0] < sells[-1]


def _plan_single(level: Level, rng: random.Random) -> tuple[list[str], date, date]:
    pool = SINGLE_POOL[:]
    rng.shuffle(pool)
    fallback = None
    for ticker in pool[:6]:
        frame = price_cache.load(ticker)
        length = level.trading_days
        if len(frame) < WARMUP_BARS + length + 1:
            continue
        stamps = frame["ts"]
        first = max(WARMUP_BARS, int(stamps.searchsorted(EARLIEST_START)))
        last = len(frame) - length - 1
        if first > last:
            continue
        fallback = fallback or (ticker, stamps.iloc[last], stamps.iloc[last + length])

        flags = setups(frame)
        starts = np.arange(first, last + 1)
        valid = np.ones(len(starts), dtype=bool)
        for column, count in level.needs:
            totals = np.concatenate([[0], np.cumsum(flags[column].to_numpy(dtype=int))])
            valid &= totals[starts + length - ACT_BUFFER] - totals[starts] >= count
        candidates = starts[valid].tolist()
        rng.shuffle(candidates)
        for start in candidates[:WINDOW_TRIES]:
            if _ordered(level, flags, start, start + length - ACT_BUFFER):
                return [ticker], stamps.iloc[start], stamps.iloc[start + length]
    if fallback is None:
        raise ValueError("No stock has enough history for this level. Ingest the starter set first.")
    return [fallback[0]], fallback[1], fallback[2]


def _pick_basket(level: Level, rng: random.Random) -> list[str]:
    sectors = _sector_of(BASKET_POOL)
    pool = BASKET_POOL[:]
    rng.shuffle(pool)
    chosen: list[str] = []
    seen: set[str] = set()
    for ticker in pool:
        if sectors[ticker] not in seen:
            chosen.append(ticker)
            seen.add(sectors[ticker])
        if len(chosen) == level.basket_size:
            return chosen
    for ticker in pool:
        if ticker not in chosen:
            chosen.append(ticker)
        if len(chosen) == level.basket_size:
            break
    return chosen


def _plan_basket(level: Level, rng: random.Random) -> tuple[list[str], date, date]:
    tickers = _pick_basket(level, rng)
    price_cache.prefetch(tickers)
    frames = {ticker: price_cache.load(ticker) for ticker in tickers}
    frames = {ticker: frame for ticker, frame in frames.items() if len(frame) > WARMUP_BARS + level.trading_days}
    if len(frames) < 2:
        raise ValueError("Not enough stocks with history for the basket levels. Ingest the starter set first.")
    tickers = list(frames)

    lead = frames[tickers[0]]
    stamps = lead["ts"]
    earliest = max(max(frame["ts"].iloc[WARMUP_BARS] for frame in frames.values()), EARLIEST_START)
    latest = min(frame["ts"].iloc[-1] for frame in frames.values())
    first = int(stamps.searchsorted(earliest))
    last = int(stamps.searchsorted(latest, side="right")) - level.trading_days - 1
    if first > last:
        raise ValueError("These stocks do not share enough history for this level.")

    flags = {ticker: setups(frame) for ticker, frame in frames.items()}
    starts = list(range(first, last + 1))
    rng.shuffle(starts)
    for start in starts[:WINDOW_TRIES]:
        begin = stamps.iloc[start]
        cutoff = stamps.iloc[start + level.trading_days - ACT_BUFFER]
        met = True
        for column, count in level.needs:
            total = 0
            for ticker, frame in frames.items():
                low = int(frame["ts"].searchsorted(begin))
                high = int(frame["ts"].searchsorted(cutoff))
                total += int(flags[ticker][column].iloc[low:high].sum())
            met = met and total >= count
        if met:
            return tickers, begin, stamps.iloc[start + level.trading_days]
    start = starts[0]
    return tickers, stamps.iloc[start], stamps.iloc[start + level.trading_days]


def plan(number: int, rng: random.Random) -> tuple[list[str], date, date]:
    level = get(number)
    if level is None:
        raise ValueError(f"There is no level {number}. Pick 1 to {len(LEVELS)}.")
    if level.basket_size == 1:
        return _plan_single(level, rng)
    return _plan_basket(level, rng)



def _as_date(value) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def _basket_return(sources: dict, start: date, through: date) -> float:
    changes = []
    for source in sources.values():
        frame = source.history_through(through)
        if frame.empty:
            continue
        position = int(frame["ts"].searchsorted(start))
        if position >= len(frame):
            continue
        base = float(frame["close"].iloc[position])
        if base:
            changes.append(float(frame["close"].iloc[-1]) / base - 1)
    return sum(changes) / len(changes) * 100 if changes else 0.0


def _diversity_reviews(trades: list[dict], sectors: dict[str, str]) -> tuple[list[dict], bool, int, int]:
    held: dict[str, float] = {}
    diversified = False
    most = (0, 0)
    reviews = []
    for trade in trades:
        shares = float(trade["shares"])
        held[trade["ticker"]] = held.get(trade["ticker"], 0.0) + (shares if trade["side"] == "BUY" else -shares)
        owned = [ticker for ticker, count in held.items() if count > 1e-9]
        spread = len({sectors.get(ticker, "Other") for ticker in owned})
        spread_out = len(owned) >= 3 and spread >= 2
        diversified = diversified or spread_out
        most = max(most, (len(owned), spread))
        reviews.append(
            {
                "followed": spread_out,
                "why": f"After this trade you owned {_plural(len(owned), 'stock')} in {_plural(spread, 'industry', 'industries')}.",
            }
        )
    return reviews, diversified, most[0], most[1]


def state(level: Level, session: dict, bundle: dict, portfolio: dict, sources: dict) -> dict:
    start = _as_date(session["start_date"])
    end = _as_date(session["end_date"])
    sim_date = _as_date(session["sim_date"])
    finished = session["status"] == "finished"
    watchlist = list(sources)
    clock = sources[watchlist[0]]
    total = max(1, clock.trading_days_between(start, end))
    played = min(total, clock.trading_days_between(start, sim_date))

    trades = [trade for trade in bundle["trades"] if not trade.get("forced")]
    reviews: list[dict] = []
    if "diversify" in level.rules:
        verdicts, follow_met, most, spread = _diversity_reviews(trades, _sector_of(watchlist))
        follow_progress = f"Most at once: {_plural(most, 'stock')} in {_plural(spread, 'industry', 'industries')}"
    else:
        verdicts = []
        for trade in trades:
            frame = sources[trade["ticker"]].history_through(_as_date(trade["trade_date"]))
            followed, why = _review(level, frame, trade["side"]) if len(frame) > 1 else (False, "No chart yet.")
            verdicts.append({"followed": followed, "why": why})
        count = sum(1 for verdict in verdicts if verdict["followed"])
        follow_met = count >= level.follow_target
        follow_progress = f"{min(count, level.follow_target)} of {level.follow_target} so far"

    for trade, verdict in zip(trades, verdicts):
        reviews.append(
            {
                "date": _as_date(trade["trade_date"]).isoformat(),
                "ticker": trade["ticker"],
                "side": trade["side"],
                "shares": float(trade["shares"]),
                "price": float(trade["price"]),
                **verdict,
            }
        )

    result = portfolio["trading_return_pct"]
    if level.beat_basket:
        benchmark = _basket_return(sources, start, sim_date)
        profit = {
            "id": "profit",
            "label": BEAT_LABEL,
            "met": finished and result > benchmark,
            "progress": f"You: {result:+.2f}% · Buy and hold: {benchmark:+.2f}%",
        }
    else:
        benchmark = None
        profit = {
            "id": "profit",
            "label": PROFIT_LABEL,
            "met": finished and result > 0,
            "progress": f"{result:+.2f}% so far",
        }

    goals = [
        {"id": "finish", "label": f"Play all {total} days", "met": finished, "progress": f"{played} of {total} days"},
        {"id": "follow", "label": _follow_label(level), "met": follow_met, "progress": follow_progress},
        profit,
    ]
    passed = finished and follow_met
    return {
        **level.describe(),
        "trading_days": total,
        "days_played": played,
        "finished": finished,
        "goals": goals,
        "stars": sum(1 for goal in goals if goal["met"]) if finished else 0,
        "passed": passed,
        "benchmark_pct": benchmark,
        "trades": reviews,
        "next_level": level.number + 1 if level.number < len(LEVELS) else None,
    }
