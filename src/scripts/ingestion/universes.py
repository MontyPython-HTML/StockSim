"""Static ticker universes used to seed the `tickers` table.

This is deliberately plain data rather than something fetched at runtime: index
membership drifts a few times a year, and a hackathon demo should not depend on a
third party being reachable. Edit the list when the composition changes, then run:

    uv run python -m scripts.ingestion.sync_universe

`is_tech` is what powers "show me the tech names"; it marks the companies whose
business is actually software/hardware/semis, not every company that happens to be
listed on a tech-heavy index.
"""

NASDAQ100 = "nasdaq100"

_NASDAQ100: list[tuple[str, str, str, bool]] = [
    ("AAPL", "Apple", "Technology", True),
    ("ABNB", "Airbnb", "Consumer Discretionary", True),
    ("ADBE", "Adobe", "Technology", True),
    ("ADI", "Analog Devices", "Technology", True),
    ("ADP", "Automatic Data Processing", "Technology", True),
    ("ADSK", "Autodesk", "Technology", True),
    ("AEP", "American Electric Power", "Utilities", False),
    ("AMAT", "Applied Materials", "Technology", True),
    ("AMD", "Advanced Micro Devices", "Technology", True),
    ("AMGN", "Amgen", "Health Care", False),
    ("AMZN", "Amazon", "Consumer Discretionary", True),
    ("APP", "AppLovin", "Technology", True),
    ("ARM", "Arm Holdings", "Technology", True),
    ("ASML", "ASML Holding", "Technology", True),
    ("AVGO", "Broadcom", "Technology", True),
    ("AXON", "Axon Enterprise", "Industrials", True),
    ("BIIB", "Biogen", "Health Care", False),
    ("BKNG", "Booking Holdings", "Consumer Discretionary", False),
    ("BKR", "Baker Hughes", "Energy", False),
    ("CCEP", "Coca-Cola Europacific Partners", "Consumer Staples", False),
    ("CDNS", "Cadence Design Systems", "Technology", True),
    ("CDW", "CDW", "Technology", True),
    ("CEG", "Constellation Energy", "Utilities", False),
    ("CHTR", "Charter Communications", "Communication Services", False),
    ("CMCSA", "Comcast", "Communication Services", False),
    ("COIN", "Coinbase Global", "Financials", True),
    ("COST", "Costco Wholesale", "Consumer Staples", False),
    ("CPRT", "Copart", "Industrials", False),
    ("CRWD", "CrowdStrike", "Technology", True),
    ("CSCO", "Cisco Systems", "Technology", True),
    ("CSGP", "CoStar Group", "Real Estate", False),
    ("CSX", "CSX", "Industrials", False),
    ("CTAS", "Cintas", "Industrials", False),
    ("CTSH", "Cognizant Technology Solutions", "Technology", True),
    ("DASH", "DoorDash", "Consumer Discretionary", True),
    ("DDOG", "Datadog", "Technology", True),
    ("DXCM", "DexCom", "Health Care", False),
    ("EA", "Electronic Arts", "Communication Services", False),
    ("EXC", "Exelon", "Utilities", False),
    ("FANG", "Diamondback Energy", "Energy", False),
    ("FAST", "Fastenal", "Industrials", False),
    ("FTNT", "Fortinet", "Technology", True),
    ("GEHC", "GE HealthCare Technologies", "Health Care", False),
    ("GFS", "GlobalFoundries", "Technology", True),
    ("GILD", "Gilead Sciences", "Health Care", False),
    ("GOOG", "Alphabet (Class C)", "Communication Services", True),
    ("GOOGL", "Alphabet (Class A)", "Communication Services", True),
    ("HON", "Honeywell International", "Industrials", False),
    ("IDXX", "IDEXX Laboratories", "Health Care", False),
    ("INSM", "Insmed", "Health Care", False),
    ("INTC", "Intel", "Technology", True),
    ("INTU", "Intuit", "Technology", True),
    ("ISRG", "Intuitive Surgical", "Health Care", True),
    ("KDP", "Keurig Dr Pepper", "Consumer Staples", False),
    ("KHC", "Kraft Heinz", "Consumer Staples", False),
    ("KLAC", "KLA", "Technology", True),
    ("LIN", "Linde", "Materials", False),
    ("LRCX", "Lam Research", "Technology", True),
    ("MAR", "Marriott International", "Consumer Discretionary", False),
    ("MCHP", "Microchip Technology", "Technology", True),
    ("MDLZ", "Mondelez International", "Consumer Staples", False),
    ("MELI", "MercadoLibre", "Consumer Discretionary", True),
    ("META", "Meta Platforms", "Communication Services", True),
    ("MNST", "Monster Beverage", "Consumer Staples", False),
    ("MRVL", "Marvell Technology", "Technology", True),
    ("MSFT", "Microsoft", "Technology", True),
    ("MSTR", "Strategy (MicroStrategy)", "Technology", True),
    ("MU", "Micron Technology", "Technology", True),
    ("NFLX", "Netflix", "Communication Services", True),
    ("NOW", "ServiceNow", "Technology", True),
    ("NVDA", "NVIDIA", "Technology", True),
    ("NXPI", "NXP Semiconductors", "Technology", True),
    ("ODFL", "Old Dominion Freight Line", "Industrials", False),
    ("ON", "ON Semiconductor", "Technology", True),
    ("ORLY", "O'Reilly Automotive", "Consumer Discretionary", False),
    ("PANW", "Palo Alto Networks", "Technology", True),
    ("PAYX", "Paychex", "Industrials", True),
    ("PCAR", "PACCAR", "Industrials", False),
    ("PDD", "PDD Holdings", "Consumer Discretionary", True),
    ("PEP", "PepsiCo", "Consumer Staples", False),
    ("PLTR", "Palantir Technologies", "Technology", True),
    ("PYPL", "PayPal Holdings", "Technology", True),
    ("QCOM", "Qualcomm", "Technology", True),
    ("REGN", "Regeneron Pharmaceuticals", "Health Care", False),
    ("ROP", "Roper Technologies", "Technology", True),
    ("ROST", "Ross Stores", "Consumer Discretionary", False),
    ("SBUX", "Starbucks", "Consumer Discretionary", False),
    ("SHOP", "Shopify", "Technology", True),
    ("SNPS", "Synopsys", "Technology", True),
    ("STX", "Seagate Technology", "Technology", True),
    ("TEAM", "Atlassian", "Technology", True),
    ("TMUS", "T-Mobile US", "Communication Services", False),
    ("TSLA", "Tesla", "Consumer Discretionary", True),
    ("TTD", "The Trade Desk", "Technology", True),
    ("TTWO", "Take-Two Interactive", "Communication Services", False),
    ("TXN", "Texas Instruments", "Technology", True),
    ("VRSK", "Verisk Analytics", "Industrials", True),
    ("VRTX", "Vertex Pharmaceuticals", "Health Care", False),
    ("WBD", "Warner Bros. Discovery", "Communication Services", False),
    ("WDAY", "Workday", "Technology", True),
    ("XEL", "Xcel Energy", "Utilities", False),
    ("ZS", "Zscaler", "Technology", True),
]

UNIVERSES: dict[str, list[tuple[str, str, str, bool]]] = {
    NASDAQ100: _NASDAQ100,
}

US_TECH_EXTRA = "us_tech_extra"

_EXTRA_TECH: list[tuple[str, str, str, bool]] = [
    ("ORCL", "Oracle", "Technology", True),
    ("IBM", "IBM", "Technology", True),
    ("CRM", "Salesforce", "Technology", True),
    ("UBER", "Uber Technologies", "Industrials", True),
    ("SNOW", "Snowflake", "Technology", True),
    ("SMCI", "Super Micro Computer", "Technology", True),
]

SEED: list[tuple[str, str, str, str, bool]] = [
    (ticker, name, sector, NASDAQ100, is_tech) for ticker, name, sector, is_tech in _NASDAQ100
] + [
    (ticker, name, sector, US_TECH_EXTRA, is_tech) for ticker, name, sector, is_tech in _EXTRA_TECH
]

STARTER_TICKERS: list[str] = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "GOOG", "META", "AVGO", "TSLA", "NFLX",
    "AMD", "ADBE", "INTC", "QCOM", "TXN", "MU", "AMAT", "LRCX", "KLAC", "MRVL",
    "ADI", "ON", "MCHP", "NXPI", "ASML", "ARM", "CDNS", "SNPS", "NOW", "INTU",
    "PANW", "CRWD", "FTNT", "DDOG", "ZS", "WDAY", "SHOP", "PLTR", "COIN", "MSTR",
    "CSCO", "ORCL", "IBM", "CRM", "UBER", "SNOW", "PYPL", "TEAM", "APP", "SMCI",
]


def rows(universe: str | None = None) -> list[tuple[str, str, str, str, bool]]:
    """Seed rows shaped for database.upsert_tickers(). None means every universe."""
    if universe is None:
        return list(SEED)
    known = set(UNIVERSES) | {US_TECH_EXTRA}
    if universe not in known:
        raise KeyError(f"unknown universe {universe!r}; known: {sorted(known)}")
    return [row for row in SEED if row[3] == universe]


def tickers(universe: str | None = None, tech_only: bool = False) -> list[str]:
    return [ticker for ticker, _, _, _, is_tech in rows(universe) if is_tech or not tech_only]


def universe_names() -> list[str]:
    return sorted({row[3] for row in SEED})


def describe(ticker: str) -> tuple[str, str] | None:
    """(company_name, universe) for a symbol, if we know it."""
    for symbol, name, _, universe, _ in SEED:
        if symbol == ticker.upper():
            return name, universe
    return None
