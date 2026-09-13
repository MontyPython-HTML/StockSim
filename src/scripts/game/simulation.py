"""The per-user "fake future" price series.

Real ingested history stops at the last downloaded trading day. When a player wants to
keep trading past that, this module rolls the last real close forward with a seeded
geometric-Brownian-motion walk and persists the result into `simulated_prices`, keyed
by session id. Nothing here is ever written back into `stock_prices`: the shared,
real dataset stays real for everybody, and each player's invented future is theirs
alone.

Determinism matters more than realism here. A session's future is a pure function of
(seed, anchor price, config), so the same bar is produced no matter when or how often
the generator runs - which is what makes it safe to regenerate and what lets a player
reload a session and see the same chart they left.
"""

import math
import random
import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

import config
from scripts.database import database
from scripts.game import indicators


TRADING_DAYS_PER_YEAR = 252
SEED_MAX = 2**63 - 1

# Volatility and drift are estimated from real history when we have it; these are the
# fallbacks for a series too short to measure, and the bounds that keep an outlier
# estimate from producing an absurd chart.
MIN_ESTIMATE_DAYS = 30
VOLATILITY_FLOOR = 0.006
VOLATILITY_CEILING = 0.09

# Drift is measured from the real series but capped in annual terms, expressed here as
# log drift per session. A stock that tripled last year should not be handed a future
# that triple again by construction - that is a lesson about mean reversion, not a
# free compounding machine.
ANNUAL_DRIFT_CEILING = 0.30
ANNUAL_DRIFT_FLOOR = -0.20
DRIFT_CEILING = math.log(1 + ANNUAL_DRIFT_CEILING) / TRADING_DAYS_PER_YEAR
DRIFT_FLOOR = math.log(1 + ANNUAL_DRIFT_FLOOR) / TRADING_DAYS_PER_YEAR

_EXTENSION_CHUNK = 252

_lock = threading.Lock()

# --- frame cache ----------------------------------------------------------
#
# Building a symbol's frame means reading its stored bars back out of the database,
# splicing them onto the real history and recomputing every indicator across the result.
# One clock tick used to do that four times over for each symbol - asking for the same
# numbers for the candles, the calendar and the chart - and against a database on the far
# side of the network the reads alone were most of a second per tick. A frame is a pure
# function of (session, ticker, that session's config), so it is built once and kept until
# something actually writes to the future: a shock, a longer horizon, or a new fork.
MAX_CACHED_FRAMES = 64

_bars_cache: "OrderedDict[tuple[str, str], pd.DataFrame]" = OrderedDict()
_future_cache: "OrderedDict[tuple[str, str], pd.DataFrame]" = OrderedDict()
_merged_cache: "OrderedDict[tuple[str, str], pd.DataFrame]" = OrderedDict()
_cache_lock = threading.Lock()


def _remember(cache: "OrderedDict", key: tuple, frame: pd.DataFrame) -> None:
    with _cache_lock:
        cache[key] = frame
        cache.move_to_end(key)
        while len(cache) > MAX_CACHED_FRAMES:
            cache.popitem(last=False)


def _recall(cache: "OrderedDict", key: tuple):
    with _cache_lock:
        frame = cache.get(key)
        if frame is not None:
            cache.move_to_end(key)
        return frame


def invalidate(session_id: str, ticker: str | None = None) -> None:
    """Drop cached frames after the future changed. Called from every write path."""
    wanted = None if ticker is None else (str(session_id), ticker.upper())
    with _cache_lock:
        for cache in (_bars_cache, _future_cache, _merged_cache):
            if wanted is not None:
                cache.pop(wanted, None)
                continue
            for key in [key for key in cache if key[0] == str(session_id)]:
                cache.pop(key, None)


@dataclass(frozen=True)
class SimulationConfig:
    ticker: str
    fork_date: date
    anchor_price: float
    horizon_days: int
    drift: float
    volatility: float
    mean_reversion: float
    seed: int
    generator: str = "gbm"

    @classmethod
    def from_row(cls, row: dict) -> "SimulationConfig":
        return cls(
            ticker=row["ticker"],
            fork_date=row["fork_date"],
            anchor_price=float(row["anchor_price"]),
            horizon_days=int(row["horizon_days"]),
            drift=float(row["drift"]),
            volatility=float(row["volatility"]),
            mean_reversion=float(row["mean_reversion"]),
            seed=int(row["seed"]),
            generator=row["generator"],
        )

    def persist_fields(self) -> dict:
        return {
            "ticker": self.ticker,
            "fork_date": self.fork_date,
            "anchor_price": self.anchor_price,
            "horizon_days": self.horizon_days,
            "drift": self.drift,
            "volatility": self.volatility,
            "mean_reversion": self.mean_reversion,
            "generator": self.generator,
            "seed": self.seed,
        }

    def describe(self) -> dict:
        """JSON-safe summary for the API/UI."""
        annual_drift = (1 + self.drift) ** TRADING_DAYS_PER_YEAR - 1
        return {
            "ticker": self.ticker,
            "fork_date": self.fork_date.isoformat(),
            "anchor_price": round(self.anchor_price, 4),
            "horizon_days": self.horizon_days,
            "drift": self.drift,
            "volatility": self.volatility,
            "mean_reversion": self.mean_reversion,
            "annualized_drift_pct": round(annual_drift * 100, 2),
            "annualized_volatility_pct": round(self.volatility * math.sqrt(TRADING_DAYS_PER_YEAR) * 100, 2),
            "seed": self.seed,
            "generator": self.generator,
        }


def estimate_from_history(frame: pd.DataFrame) -> tuple[float, float, float]:
    """(daily drift, daily volatility, base volume) measured from the tail of real data.

    Uses log returns over roughly the last year of sessions. Both numbers are clamped:
    a meme stock's 2021 run should not decide the shape of every simulated future.
    """
    default_volatility = config.SIMULATION_VOLATILITY
    if frame is None or len(frame) < MIN_ESTIMATE_DAYS:
        base_volume = 1_000_000.0
        if frame is not None and not frame.empty and "volume" in frame:
            base_volume = float(frame["volume"].tail(20).mean())
        return 0.0, default_volatility, base_volume

    recent = frame.tail(TRADING_DAYS_PER_YEAR)
    log_returns = (recent["close"].astype(float)).apply(math.log).diff().dropna()
    drift = float(log_returns.mean())
    volatility = float(log_returns.std(ddof=1))

    drift = max(DRIFT_FLOOR, min(DRIFT_CEILING, drift))
    volatility = max(VOLATILITY_FLOOR, min(VOLATILITY_CEILING, volatility))
    base_volume = float(recent["volume"].tail(60).mean())
    return drift, volatility, base_volume


def next_business_day(after: date) -> date:
    """Synthetic futures are weekdays only - we have no holiday calendar to consult."""
    candidate = after + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def _rng(config_: SimulationConfig, index: int) -> random.Random:
    """A fresh PRNG per bar index, so a bar's value never depends on iteration order."""
    return random.Random(f"{config_.seed}:{index}")


def generate_bars(config_: SimulationConfig, base_volume: float | None = None) -> list[tuple]:
    """The full deterministic future: one tuple per synthetic trading day.

    (ts, open, high, low, close, volume) - indicators are computed later over the
    concatenation of real and synthetic history, so they stay continuous across the fork.
    """
    if base_volume is None:
        base_volume = 1_000_000.0

    daily_sigma = config_.volatility
    daily_mu = config_.drift
    kappa = config_.mean_reversion
    log_anchor = math.log(config_.anchor_price)

    bars: list[tuple] = []
    ts = config_.fork_date
    previous_close = config_.anchor_price

    for index in range(config_.horizon_days):
        step = index + 1
        ts = next_business_day(ts)

        rng = _rng(config_, index)
        shock = rng.gauss(0.0, 1.0)
        # Momentum plus a pull back toward the drift trend line: pure GBM wanders far
        # enough over a year that charts stop looking like a stock.
        trend_level = log_anchor + daily_mu * step
        reversion = kappa * (trend_level - math.log(previous_close))
        log_return = (daily_mu - 0.5 * daily_sigma**2) + daily_sigma * shock + reversion

        close = previous_close * math.exp(log_return)

        gap = rng.gauss(0.0, daily_sigma / 3)
        open_ = previous_close * math.exp(gap)
        intraday = abs(rng.gauss(0.0, daily_sigma / 2))
        high = max(open_, close) * (1 + intraday)
        low = min(open_, close) * (1 - intraday)

        # Volume clusters with the size of the move, which is what makes spikes readable.
        volume_multiplier = math.exp(rng.gauss(0.0, 0.25) + 8 * abs(log_return))
        volume = max(1, int(base_volume * volume_multiplier))

        bars.append(
            (
                ts,
                round(open_, 4),
                round(high, 4),
                round(low, 4),
                round(close, 4),
                volume,
            )
        )
        previous_close = close

    return bars


def _stored_bars(session_id: str, config_: SimulationConfig) -> pd.DataFrame:
    key = (str(session_id), config_.ticker)
    cached = _recall(_bars_cache, key)
    if cached is not None:
        return cached
    frame = database.fetch_simulated_prices(
        session_id, config_.ticker, config_.fork_date, config_.fork_date + timedelta(days=40 * config_.horizon_days)
    )
    _remember(_bars_cache, key, frame)
    return frame


def ensure_future(session_id: str, config_: SimulationConfig) -> int:
    """Materialize any not-yet-written bars in the horizon. Returns rows written."""
    with _lock:
        existing = _stored_bars(session_id, config_)
        if len(existing) >= config_.horizon_days:
            return 0

        base_volume = 1_000_000.0
        real = database.fetch_price_history(
            config_.ticker, config_.fork_date - timedelta(days=400), config_.fork_date
        )
        if not real.empty:
            _, _, base_volume = estimate_from_history(real)

        bars = generate_bars(config_, base_volume)
        new_rows = [(*bar, None) for bar in bars[len(existing):]]
        written = database.upsert_simulated_prices(session_id, config_.ticker, new_rows)
        if bars:
            database.update_simulation(session_id, config_.ticker, last_generated=bars[-1][0])
        # New bars exist now, so every frame built from the old ones is stale.
        invalidate(session_id, config_.ticker)
        return written


def extend_horizon(session_id: str, config_: SimulationConfig) -> SimulationConfig:
    """Grow the horizon by a chunk when a player runs off the end of their future."""
    grown = replace_horizon(config_, config_.horizon_days + _EXTENSION_CHUNK)
    database.update_simulation(session_id, config_.ticker, horizon_days=grown.horizon_days)
    ensure_future(session_id, grown)
    return grown


def replace_horizon(config_: SimulationConfig, horizon_days: int) -> SimulationConfig:
    return SimulationConfig(
        ticker=config_.ticker,
        fork_date=config_.fork_date,
        anchor_price=config_.anchor_price,
        horizon_days=horizon_days,
        drift=config_.drift,
        volatility=config_.volatility,
        mean_reversion=config_.mean_reversion,
        seed=config_.seed,
        generator=config_.generator,
    )


def future_frame(session_id: str, config_: SimulationConfig) -> pd.DataFrame:
    """Persisted synthetic bars as a price frame, shaped like fetch_price_history.

    The horizon is only checked and topped up on a cache miss: on a hit the bars are
    already materialized, which is exactly the condition `ensure_future` re-derives with a
    query of its own.
    """
    key = (str(session_id), config_.ticker)
    cached = _recall(_future_cache, key)
    if cached is not None:
        return cached
    ensure_future(session_id, config_)
    frame = _stored_bars(session_id, config_)
    _remember(_future_cache, key, frame)
    return frame


def merged_frame(session_id: str, config_: SimulationConfig, real: pd.DataFrame) -> pd.DataFrame:
    """`merge_with_history` with the result kept.

    This is the expensive half: SMA/RSI/MACD are recomputed across the seam of real and
    generated bars, over the whole series, on every request that draws a chart.
    """
    key = (str(session_id), config_.ticker)
    cached = _recall(_merged_cache, key)
    if cached is not None:
        return cached
    frame = merge_with_history(real, future_frame(session_id, config_))
    _remember(_merged_cache, key, frame)
    return frame


def apply_shock(
    session_id: str,
    config_: SimulationConfig,
    from_date: date,
    sentiment: float,
    magnitude: float,
    decay_days: int = 10,
    event_id: int | None = None,
) -> int:
    """Bend the synthetic future so a headline actually shows up in the candles.

    The event does not land as one enormous gap. A fraction of it is priced in the day
    the story breaks, and the rest bleeds in as extra drift over the following sessions,
    which is both closer to how news actually reprices a stock and much less likely to
    produce a cartoonish 30% single candle. Total repricing converges on sentiment x
    magnitude x SHOCK_IMPACT_SCALE.
    """
    frame = _stored_bars(session_id, config_)
    if frame.empty:
        return 0

    affected = frame[frame["ts"] >= from_date]
    if affected.empty:
        return 0

    impact = max(-0.9, min(0.9, float(sentiment) * float(magnitude) * config.SHOCK_IMPACT_SCALE))
    if abs(impact) < 1e-6:
        return 0
    # Every cached frame for this symbol came from the pre-shock candles.
    invalidate(session_id, config_.ticker)
    decay_days = max(1, int(decay_days))

    updates = []
    cumulative = 0.0
    for position, (_, row) in enumerate(affected.iterrows()):
        if position == 0:
            step = impact * config.SHOCK_IMMEDIATE_SHARE
        else:
            step = (
                impact
                * (1 - config.SHOCK_IMMEDIATE_SHARE)
                / decay_days
                * math.exp(-(position - 1) / decay_days)
            )
        cumulative += step
        multiplier = 1 + cumulative

        # The day the story breaks trades heavy; the extra volume fades with the news.
        volume_multiplier = 1 + (1.2 if position == 0 else 0.5 * math.exp(-position / decay_days)) * abs(impact) * 3
        updates.append(
            (
                round(float(row["open"]) * multiplier, 4),
                round(float(row["high"]) * multiplier, 4),
                round(float(row["low"]) * multiplier, 4),
                round(float(row["close"]) * multiplier, 4),
                int(float(row["volume"]) * volume_multiplier),
                event_id,
                row["ts"],
            )
        )

    written = database.rescale_simulated_prices(session_id, config_.ticker, updates)
    # Again on the way out: a rebuild that slipped in between the two invalidations would
    # have cached the pre-shock candles.
    invalidate(session_id, config_.ticker)
    return written


def merge_with_history(real: pd.DataFrame, future: pd.DataFrame) -> pd.DataFrame:
    """Real history and the simulated future as one frame, with indicators recomputed
    across the seam so SMA/RSI/MACD do not restart at the fork."""
    if future is None or future.empty:
        return indicators.compute_all(real)
    combined = pd.concat([real, future], ignore_index=True)
    combined = combined.sort_values("ts").drop_duplicates(subset="ts", keep="last")
    return indicators.compute_all(combined.reset_index(drop=True))


def build_config(
    ticker: str,
    fork_date: date,
    anchor_price: float,
    frame: pd.DataFrame | None = None,
    horizon_days: int | None = None,
    drift: float | None = None,
    volatility: float | None = None,
    mean_reversion: float | None = None,
    seed: int | None = None,
) -> SimulationConfig:
    """Fill in whatever the caller left out from the ticker's own history.

    Volatility always comes from the real series when it is measurable, so a sleepy
    name and a meme name do not get handed the same chart; drift is only estimated if
    the caller did not ask for a specific one.
    """
    estimated_drift, estimated_volatility, _ = estimate_from_history(frame)
    return SimulationConfig(
        ticker=ticker.upper(),
        fork_date=fork_date,
        anchor_price=anchor_price,
        horizon_days=horizon_days or config.SIMULATION_HORIZON_DAYS,
        drift=estimated_drift if drift is None else drift,
        volatility=estimated_volatility if volatility is None else volatility,
        mean_reversion=config.SIMULATION_MEAN_REVERSION if mean_reversion is None else mean_reversion,
        seed=seed if seed is not None else random.SystemRandom().randrange(SEED_MAX),
    )
