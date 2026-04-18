"""Stock screener — discovers tradeable tickers from the NASDAQ-100 universe.

Pipeline:
    1. Scrape NASDAQ-100 constituents from Wikipedia.
    2. Pre-filter by price, volume, and history length.
    3. Rank by absolute technical score (bullish OR bearish setups) and take top N.
"""

from datetime import datetime, timedelta
from io import StringIO
from typing import Dict, List, Tuple

import pandas as pd
import requests

from ..analysis import technical_analyzer
from ..config.stocks import (
    MIN_AVG_VOLUME,
    MIN_HISTORY_BARS,
    MIN_PRICE,
    NASDAQ100_URL,
    TOP_N_STOCKS,
)
from ..utils import get_logger
from . import stock_data

logger = get_logger("stock_screener")

_WIKI_UA = "Mozilla/5.0 (compatible; crypto-trading-bot/1.0)"
_NAME_CACHE: Dict[str, str] = {}
_NAME_CACHE_TS: datetime = datetime.min
_NAME_CACHE_TTL = timedelta(hours=24)


def fetch_nasdaq100_constituents() -> Dict[str, str]:
    """Return a {ticker: company_name} map scraped from Wikipedia."""
    resp = requests.get(NASDAQ100_URL, headers={"User-Agent": _WIKI_UA}, timeout=15)
    resp.raise_for_status()
    tables = pd.read_html(StringIO(resp.text))
    for t in tables:
        ticker_col = next((c for c in ("Ticker", "Symbol") if c in t.columns), None)
        if ticker_col is None:
            continue
        name_col = next((c for c in ("Company", "Name", "Security") if c in t.columns), None)
        df = t[[ticker_col] + ([name_col] if name_col else [])].dropna(subset=[ticker_col])
        result: Dict[str, str] = {}
        for _, row in df.iterrows():
            # Yahoo uses '-' where Wikipedia uses '.' for share classes (e.g. BRK.B -> BRK-B)
            ticker = str(row[ticker_col]).strip().replace(".", "-")
            if not ticker:
                continue
            result[ticker] = str(row[name_col]).strip() if name_col else ticker
        if result:
            return result
    raise RuntimeError("Could not locate NASDAQ-100 constituents table on Wikipedia")


def get_name_map() -> Dict[str, str]:
    """Cached {ticker: name} map; refreshes from Wikipedia every 24h."""
    global _NAME_CACHE, _NAME_CACHE_TS
    if not _NAME_CACHE or (datetime.utcnow() - _NAME_CACHE_TS) > _NAME_CACHE_TTL:
        try:
            _NAME_CACHE = fetch_nasdaq100_constituents()
            _NAME_CACHE_TS = datetime.utcnow()
        except Exception as e:
            logger.warning(f"stock_screener: name-map refresh failed, using last known ({len(_NAME_CACHE)}): {e}")
    return _NAME_CACHE


def fetch_nasdaq100_tickers() -> List[str]:
    """Return the NASDAQ-100 ticker list (derived from the constituents map)."""
    return list(get_name_map().keys())


def screen(top_n: int = TOP_N_STOCKS) -> List[str]:
    """Return the top N tickers ranked by |technical score|, after pre-filters."""
    try:
        universe = fetch_nasdaq100_tickers()
    except Exception as e:
        logger.error(f"stock_screener: could not fetch universe: {e}")
        return []

    logger.info(f"stock_screener: fetched {len(universe)} NASDAQ-100 tickers; scanning...")
    bars = stock_data.fetch_all_ohlcv(universe)
    if not bars:
        logger.warning("stock_screener: no OHLCV data returned for universe")
        return []

    scored: List[Tuple[str, float]] = []
    for ticker, df in bars.items():
        if df is None or df.empty or len(df) < MIN_HISTORY_BARS:
            continue
        try:
            if float(df["close"].iloc[-1]) < MIN_PRICE:
                continue
            if float(df["volume"].tail(20).mean()) < MIN_AVG_VOLUME:
                continue
            signals = technical_analyzer.analyze(df)
            scored.append((ticker, float(signals.overall_score)))
        except Exception as e:
            logger.debug(f"stock_screener: skipping {ticker}: {e}")

    scored.sort(key=lambda x: abs(x[1]), reverse=True)
    picked = [t for t, _ in scored[:top_n]]
    logger.info(f"stock_screener: selected {len(picked)} tickers: {picked}")
    return picked
