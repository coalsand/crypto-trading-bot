"""Supported cryptocurrencies configuration."""

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class CoinInfo:
    """Information about a supported cryptocurrency."""
    symbol: str                # Trading symbol (e.g., BTC)
    name: str                  # Full name
    kraken_pair: str           # Kraken trading pair (e.g., BTC/USD)
    subreddits: List[str]      # Related subreddits (for Reddit sentiment)
    stocktwits_symbol: str = ""  # StockTwits symbol (e.g., BTC.X). Defaults to f"{symbol}.X".

    def __post_init__(self):
        if not self.stocktwits_symbol:
            self.stocktwits_symbol = f"{self.symbol}.X"


# Top 10 cryptocurrencies by market cap (as of implementation)
SUPPORTED_COINS: Dict[str, CoinInfo] = {
    "BTC":  CoinInfo("BTC",  "Bitcoin",    "BTC/USD",  ["bitcoin", "btc"]),
    "ETH":  CoinInfo("ETH",  "Ethereum",   "ETH/USD",  ["ethereum", "ethtrader", "ethfinance"]),
    "USDT": CoinInfo("USDT", "Tether",     "USDT/USD", ["tether"]),
    "BNB":  CoinInfo("BNB",  "BNB",        "BNB/USD",  ["binance", "bnbchainofficial"]),
    "SOL":  CoinInfo("SOL",  "Solana",     "SOL/USD",  ["solana"]),
    "XRP":  CoinInfo("XRP",  "XRP",        "XRP/USD",  ["ripple", "xrp"]),
    "USDC": CoinInfo("USDC", "USD Coin",   "USDC/USD", ["coinbase"]),
    "ADA":  CoinInfo("ADA",  "Cardano",    "ADA/USD",  ["cardano"]),
    "DOGE": CoinInfo("DOGE", "Dogecoin",   "DOGE/USD", ["dogecoin"]),
    "AVAX": CoinInfo("AVAX", "Avalanche",  "AVAX/USD", ["avax", "avalancheavax"]),
}

# Tradeable coins (excluding stablecoins for trading signals)
TRADEABLE_COINS = {k: v for k, v in SUPPORTED_COINS.items() if k not in ("USDT", "USDC")}


def get_all_subreddits() -> List[str]:
    """Get all unique subreddits across all coins."""
    subs = set()
    for coin in SUPPORTED_COINS.values():
        subs.update(coin.subreddits)
    return list(subs)


def get_coin_by_symbol(symbol: str) -> CoinInfo | None:
    """Get coin info by symbol (case-insensitive)."""
    return SUPPORTED_COINS.get(symbol.upper())
