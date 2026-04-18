"""Kraken market data fetcher using ccxt."""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import ccxt
import pandas as pd

from ..config import settings, TRADEABLE_COINS, CoinInfo
from ..storage import db, MarketData

logger = logging.getLogger(__name__)


class MarketDataFetcher:
    """Fetches OHLCV market data from Kraken exchange."""

    # Timeframe mapping
    TIMEFRAMES = {
        "1m": "1m",
        "5m": "5m",
        "15m": "15m",
        "1h": "1h",
        "4h": "4h",
        "1d": "1d",
    }

    def __init__(self):
        """Initialize the Kraken connection."""
        self.exchange = ccxt.kraken({
            "apiKey": settings.kraken.api_key,
            "secret": settings.kraken.api_secret,
            "enableRateLimit": True,
            "options": {
                "defaultType": "spot"
            }
        })

        # Note: Kraken doesn't support sandbox mode in ccxt
        # Paper trading is handled at the application level instead

        self._markets_loaded = False

    def _ensure_markets_loaded(self):
        """Ensure markets are loaded."""
        if not self._markets_loaded:
            try:
                self.exchange.load_markets()
                self._markets_loaded = True
            except Exception as e:
                logger.error(f"Failed to load markets: {e}")
                raise

    def get_symbol_for_kraken(self, coin: CoinInfo) -> str:
        """Get the Kraken symbol for a coin."""
        return coin.kraken_pair

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1h",
        limit: int = 500,
        since: Optional[datetime] = None
    ) -> pd.DataFrame:
        """
        Fetch OHLCV data for a symbol.

        Args:
            symbol: Trading pair (e.g., "BTC/USD")
            timeframe: Candle timeframe
            limit: Maximum number of candles
            since: Start time for fetching data

        Returns:
            DataFrame with OHLCV data
        """
        self._ensure_markets_loaded()

        try:
            since_ts = int(since.timestamp() * 1000) if since else None

            ohlcv = self.exchange.fetch_ohlcv(
                symbol,
                timeframe=timeframe,
                since=since_ts,
                limit=limit
            )

            if not ohlcv:
                logger.warning(f"No data returned for {symbol}")
                return pd.DataFrame()

            df = pd.DataFrame(
                ohlcv,
                columns=["timestamp", "open", "high", "low", "close", "volume"]
            )

            # Convert timestamp to datetime
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")

            return df

        except ccxt.NetworkError as e:
            logger.error(f"Network error fetching {symbol}: {e}")
            raise
        except ccxt.ExchangeError as e:
            logger.error(f"Exchange error fetching {symbol}: {e}")
            raise

    def fetch_ticker(self, symbol: str) -> dict:
        """
        Fetch current ticker data for a symbol.

        Args:
            symbol: Trading pair (e.g., "BTC/USD")

        Returns:
            Dictionary with ticker data
        """
        self._ensure_markets_loaded()

        try:
            ticker = self.exchange.fetch_ticker(symbol)
            return {
                "symbol": symbol,
                "last": ticker.get("last"),
                "bid": ticker.get("bid"),
                "ask": ticker.get("ask"),
                "high": ticker.get("high"),
                "low": ticker.get("low"),
                "volume": ticker.get("baseVolume"),
                "timestamp": datetime.utcnow()
            }
        except Exception as e:
            logger.error(f"Error fetching ticker for {symbol}: {e}")
            raise

    def fetch_order_book(
        self,
        symbol: str,
        limit: int = 20
    ) -> dict:
        """
        Fetch order book for a symbol.

        Args:
            symbol: Trading pair
            limit: Depth of order book

        Returns:
            Dictionary with bids and asks
        """
        self._ensure_markets_loaded()

        try:
            order_book = self.exchange.fetch_order_book(symbol, limit=limit)
            return {
                "symbol": symbol,
                "bids": order_book.get("bids", []),
                "asks": order_book.get("asks", []),
                "timestamp": datetime.utcnow()
            }
        except Exception as e:
            logger.error(f"Error fetching order book for {symbol}: {e}")
            raise

    def fetch_all_coins_ohlcv(
        self,
        timeframe: str = "1h",
        limit: int = 500
    ) -> Dict[str, pd.DataFrame]:
        """
        Fetch OHLCV data for all tradeable coins.

        Args:
            timeframe: Candle timeframe
            limit: Maximum number of candles per coin

        Returns:
            Dictionary mapping symbol to DataFrame
        """
        results = {}

        for symbol, coin_info in TRADEABLE_COINS.items():
            try:
                df = self.fetch_ohlcv(
                    coin_info.kraken_pair,
                    timeframe=timeframe,
                    limit=limit
                )
                if not df.empty:
                    results[symbol] = df
                    logger.info(f"Fetched {len(df)} candles for {symbol}")
            except Exception as e:
                logger.error(f"Failed to fetch data for {symbol}: {e}")

        return results

    def fetch_and_store(
        self,
        symbol: str,
        timeframe: str = "1h",
        limit: int = 500
    ) -> int:
        """
        Fetch OHLCV data and store in database.

        Args:
            symbol: Coin symbol (e.g., "BTC")
            timeframe: Candle timeframe
            limit: Maximum number of candles

        Returns:
            Number of records stored
        """
        coin_info = TRADEABLE_COINS.get(symbol)
        if not coin_info:
            logger.error(f"Unknown symbol: {symbol}")
            return 0

        try:
            df = self.fetch_ohlcv(coin_info.kraken_pair, timeframe, limit)
            if df.empty:
                return 0

            # Convert to list of dicts
            data = df.to_dict("records")

            # Store in database
            db.save_market_data(data, symbol, timeframe)
            logger.info(f"Stored {len(data)} records for {symbol}")

            return len(data)

        except Exception as e:
            logger.error(f"Failed to fetch and store data for {symbol}: {e}")
            return 0

    def fetch_and_store_all(
        self,
        timeframe: str = "1h",
        limit: int = 500
    ) -> Dict[str, int]:
        """
        Fetch and store OHLCV data for all tradeable coins.

        Returns:
            Dictionary mapping symbol to number of records stored
        """
        results = {}

        for symbol in TRADEABLE_COINS:
            count = self.fetch_and_store(symbol, timeframe, limit)
            results[symbol] = count

        return results

    def get_current_prices(self) -> Dict[str, float]:
        """
        Get current prices for all tradeable coins.

        Returns:
            Dictionary mapping symbol to current price
        """
        prices = {}

        for symbol, coin_info in TRADEABLE_COINS.items():
            try:
                ticker = self.fetch_ticker(coin_info.kraken_pair)
                if ticker.get("last"):
                    prices[symbol] = ticker["last"]
            except Exception as e:
                logger.error(f"Failed to get price for {symbol}: {e}")

        return prices


# Global instance
market_data_fetcher = MarketDataFetcher()
