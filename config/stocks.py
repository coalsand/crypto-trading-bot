"""Stock trading configuration and market-hours helpers."""

from datetime import datetime, time
from typing import Optional

import pytz

# Stock screener settings
NASDAQ100_URL = "https://en.wikipedia.org/wiki/Nasdaq-100"
MIN_AVG_VOLUME = 1_000_000   # 20-bar average, in shares
MIN_PRICE = 5.0              # avoid penny stocks
MIN_HISTORY_BARS = 100       # need enough bars for indicators
TOP_N_STOCKS = 15            # active list size after ranking

# Data-fetch settings (yfinance)
YF_PERIOD = "60d"
YF_INTERVAL = "1h"

# Market hours (US Eastern)
_ET = pytz.timezone("US/Eastern")
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)
NO_OPEN_AFTER = time(15, 30)  # no new positions in last 30min


def _now_et(now: Optional[datetime] = None) -> datetime:
    now = now or datetime.now(tz=pytz.UTC)
    if now.tzinfo is None:
        now = pytz.UTC.localize(now)
    return now.astimezone(_ET)


def is_market_open(now: Optional[datetime] = None) -> bool:
    """True if US equities market is currently open (weekdays 9:30–16:00 ET)."""
    et = _now_et(now)
    if et.weekday() >= 5:
        return False
    return MARKET_OPEN <= et.time() <= MARKET_CLOSE


def can_open_new_stock_position(now: Optional[datetime] = None) -> bool:
    """True if it's OK to open a new stock position (no opens after 15:30 ET)."""
    et = _now_et(now)
    if et.weekday() >= 5:
        return False
    return MARKET_OPEN <= et.time() <= NO_OPEN_AFTER
