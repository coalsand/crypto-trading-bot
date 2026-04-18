"""Supported cryptocurrencies configuration."""

from dataclasses import dataclass
from typing import Dict, List


@dataclass
class CoinInfo:
    """Information about a supported cryptocurrency."""
    symbol: str  # Trading symbol (e.g., BTC)
    name: str  # Full name
    kraken_pair: str  # Kraken trading pair (e.g., BTC/USD)
    subreddits: List[str]  # Related subreddits
    twitter_hashtags: List[str]  # Related Twitter hashtags
    twitter_accounts: List[str]  # Influential accounts to track


# Top 10 cryptocurrencies by market cap (as of implementation)
SUPPORTED_COINS: Dict[str, CoinInfo] = {
    "BTC": CoinInfo(
        symbol="BTC",
        name="Bitcoin",
        kraken_pair="BTC/USD",
        subreddits=["bitcoin", "btc"],
        twitter_hashtags=["#Bitcoin", "#BTC"],
        twitter_accounts=["bitcoin", "saborskis"]
    ),
    "ETH": CoinInfo(
        symbol="ETH",
        name="Ethereum",
        kraken_pair="ETH/USD",
        subreddits=["ethereum", "ethtrader", "ethfinance"],
        twitter_hashtags=["#Ethereum", "#ETH"],
        twitter_accounts=["ethereum", "VitalikButerin"]
    ),
    "USDT": CoinInfo(
        symbol="USDT",
        name="Tether",
        kraken_pair="USDT/USD",
        subreddits=["tether"],
        twitter_hashtags=["#USDT", "#Tether"],
        twitter_accounts=["Tether_to"]
    ),
    "BNB": CoinInfo(
        symbol="BNB",
        name="BNB",
        kraken_pair="BNB/USD",
        subreddits=["binance", "bnbchainofficial"],
        twitter_hashtags=["#BNB", "#Binance"],
        twitter_accounts=["binance", "caborskisz"]
    ),
    "SOL": CoinInfo(
        symbol="SOL",
        name="Solana",
        kraken_pair="SOL/USD",
        subreddits=["solana"],
        twitter_hashtags=["#Solana", "#SOL"],
        twitter_accounts=["solana", "aaborskismal"]
    ),
    "XRP": CoinInfo(
        symbol="XRP",
        name="XRP",
        kraken_pair="XRP/USD",
        subreddits=["ripple", "xrp"],
        twitter_hashtags=["#XRP", "#Ripple"],
        twitter_accounts=["Ripple", "baborskisarner"]
    ),
    "USDC": CoinInfo(
        symbol="USDC",
        name="USD Coin",
        kraken_pair="USDC/USD",
        subreddits=["coinbase"],
        twitter_hashtags=["#USDC"],
        twitter_accounts=["circle", "coinbase"]
    ),
    "ADA": CoinInfo(
        symbol="ADA",
        name="Cardano",
        kraken_pair="ADA/USD",
        subreddits=["cardano"],
        twitter_hashtags=["#Cardano", "#ADA"],
        twitter_accounts=["Cardano", "IOHK_Charles"]
    ),
    "DOGE": CoinInfo(
        symbol="DOGE",
        name="Dogecoin",
        kraken_pair="DOGE/USD",
        subreddits=["dogecoin"],
        twitter_hashtags=["#Dogecoin", "#DOGE"],
        twitter_accounts=["dogecoin", "elonmusk"]
    ),
    "AVAX": CoinInfo(
        symbol="AVAX",
        name="Avalanche",
        kraken_pair="AVAX/USD",
        subreddits=["avax", "avalancheavax"],
        twitter_hashtags=["#Avalanche", "#AVAX"],
        twitter_accounts=["avaborskislabs"]
    ),
}

# Tradeable coins (excluding stablecoins for trading signals)
TRADEABLE_COINS = {k: v for k, v in SUPPORTED_COINS.items() if k not in ["USDT", "USDC"]}


def get_all_subreddits() -> List[str]:
    """Get all unique subreddits for all coins."""
    subreddits = set()
    for coin in SUPPORTED_COINS.values():
        subreddits.update(coin.subreddits)
    return list(subreddits)


def get_all_hashtags() -> List[str]:
    """Get all unique hashtags for all coins."""
    hashtags = set()
    for coin in SUPPORTED_COINS.values():
        hashtags.update(coin.twitter_hashtags)
    return list(hashtags)


def get_coin_by_symbol(symbol: str) -> CoinInfo | None:
    """Get coin info by symbol."""
    return SUPPORTED_COINS.get(symbol.upper())
