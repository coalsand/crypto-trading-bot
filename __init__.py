"""
Crypto Trading Bot

An autonomous cryptocurrency trading bot using technical analysis
and multi-source sentiment analysis.

Features:
- Technical analysis (RSI, MACD, Bollinger Bands, EMA)
- Sentiment analysis from Reddit, Twitter, and News
- Risk management with position sizing and stop-losses
- Paper trading for strategy testing
- Live trading via Kraken API

Usage:
    python -m crypto_trading_bot.main --paper  # Paper trading
    python -m crypto_trading_bot.main --live   # Live trading (CAUTION)

For more information, see the README.
"""

__version__ = "1.0.0"
__author__ = "Crypto Trading Bot"

from .config import settings
from .storage import db

__all__ = ["settings", "db", "__version__"]
