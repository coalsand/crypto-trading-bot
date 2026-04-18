"""Stock market data fetching via yfinance.

Mirrors the minimal interface the rest of the bot expects from market_data:
    - fetch_ohlcv(symbol, ...) -> DataFrame with lowercase OHLCV cols
    - fetch_all_ohlcv(symbols, ...) -> Dict[str, DataFrame]
    - get_current_prices(symbols) -> Dict[str, float]
"""

from typing import Dict, Iterable, List

import pandas as pd
import yfinance as yf

from ..config.stocks import YF_PERIOD, YF_INTERVAL
from ..utils import get_logger

logger = get_logger("stock_data")


def fetch_ohlcv(
    symbol: str,
    period: str = YF_PERIOD,
    interval: str = YF_INTERVAL,
) -> pd.DataFrame:
    """Fetch OHLCV bars for a single ticker. Empty DataFrame on failure."""
    try:
        df = yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=True)
        if df.empty:
            return df
        df = df.rename(columns=str.lower)
        return df[["open", "high", "low", "close", "volume"]].copy()
    except Exception as e:
        logger.error(f"stock_data: fetch_ohlcv({symbol}) failed: {e}")
        return pd.DataFrame()


def fetch_all_ohlcv(
    symbols: Iterable[str],
    period: str = YF_PERIOD,
    interval: str = YF_INTERVAL,
) -> Dict[str, pd.DataFrame]:
    """Fetch OHLCV bars for a list of tickers in one yfinance call."""
    tickers = list(symbols)
    if not tickers:
        return {}
    try:
        raw = yf.download(
            tickers=" ".join(tickers),
            period=period,
            interval=interval,
            group_by="ticker",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
    except Exception as e:
        logger.error(f"stock_data: fetch_all_ohlcv bulk download failed: {e}")
        return {}

    result: Dict[str, pd.DataFrame] = {}
    for ticker in tickers:
        try:
            if len(tickers) == 1:
                df = raw
            elif isinstance(raw.columns, pd.MultiIndex) and ticker in raw.columns.levels[0]:
                df = raw[ticker]
            else:
                continue
            df = df.dropna(how="all").rename(columns=str.lower)
            if df.empty:
                continue
            result[ticker] = df[["open", "high", "low", "close", "volume"]].copy()
        except Exception as e:
            logger.warning(f"stock_data: skipped {ticker}: {e}")
    return result


def get_current_prices(symbols: Iterable[str]) -> Dict[str, float]:
    """Last known price for each ticker (uses the final close from a 1d/1m snapshot)."""
    tickers = list(symbols)
    if not tickers:
        return {}
    prices: Dict[str, float] = {}
    try:
        raw = yf.download(
            tickers=" ".join(tickers),
            period="1d",
            interval="1m",
            group_by="ticker",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
    except Exception as e:
        logger.error(f"stock_data: get_current_prices bulk fetch failed: {e}")
        return {}

    for ticker in tickers:
        try:
            if len(tickers) == 1:
                df = raw
            elif isinstance(raw.columns, pd.MultiIndex) and ticker in raw.columns.levels[0]:
                df = raw[ticker]
            else:
                continue
            close = df["Close"].dropna() if "Close" in df.columns else df["close"].dropna()
            if not close.empty:
                prices[ticker] = float(close.iloc[-1])
        except Exception as e:
            logger.warning(f"stock_data: no price for {ticker}: {e}")
    return prices
