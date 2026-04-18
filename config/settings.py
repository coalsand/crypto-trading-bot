"""Configuration management for the crypto trading bot."""

import os
from dataclasses import dataclass, field
from typing import Optional
from dotenv import load_dotenv

load_dotenv()


@dataclass
class KrakenConfig:
    """Kraken API configuration."""
    api_key: str = field(default_factory=lambda: os.getenv("KRAKEN_API_KEY", ""))
    api_secret: str = field(default_factory=lambda: os.getenv("KRAKEN_API_SECRET", ""))
    sandbox: bool = True  # Use sandbox/paper trading by default


@dataclass
class RedditConfig:
    """Reddit API configuration."""
    client_id: str = field(default_factory=lambda: os.getenv("REDDIT_CLIENT_ID", ""))
    client_secret: str = field(default_factory=lambda: os.getenv("REDDIT_CLIENT_SECRET", ""))
    user_agent: str = "CryptoTradingBot/1.0"
    subreddits: list = field(default_factory=lambda: [
        "cryptocurrency", "bitcoin", "ethereum", "CryptoMarkets",
        "altcoin", "defi", "solana", "cardano"
    ])


@dataclass
class TwitterConfig:
    """Twitter/X API configuration."""
    bearer_token: str = field(default_factory=lambda: os.getenv("TWITTER_BEARER_TOKEN", ""))
    api_key: str = field(default_factory=lambda: os.getenv("TWITTER_API_KEY", ""))
    api_secret: str = field(default_factory=lambda: os.getenv("TWITTER_API_SECRET", ""))
    access_token: str = field(default_factory=lambda: os.getenv("TWITTER_ACCESS_TOKEN", ""))
    access_token_secret: str = field(default_factory=lambda: os.getenv("TWITTER_ACCESS_TOKEN_SECRET", ""))


@dataclass
class TradingConfig:
    """Trading strategy configuration."""
    # Position sizing
    max_position_size_pct: float = 0.05  # 5% max per trade
    min_position_size_pct: float = 0.03  # 3% min per trade
    max_open_positions: int = 5

    # Risk management
    stop_loss_atr_multiplier: float = 2.0  # Stop-loss at 2x ATR
    take_profit_ratio: float = 3.0  # 3:1 risk/reward ratio
    daily_loss_limit_pct: float = 0.10  # 10% daily loss limit

    # Signal weights
    technical_weight: float = 0.60
    sentiment_weight: float = 0.40

    # Signal thresholds
    buy_signal_threshold: float = 0.5
    sell_signal_threshold: float = -0.5
    sentiment_threshold: float = 0.3

    # Technical indicator settings
    rsi_period: int = 14
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    bb_period: int = 20
    bb_std: float = 2.0
    ema_short: int = 20
    ema_medium: int = 50
    ema_long: int = 200
    atr_period: int = 14


@dataclass
class SchedulerConfig:
    """Scheduler configuration."""
    signal_check_interval_minutes: int = 15
    sentiment_update_interval_minutes: int = 30
    market_data_interval_minutes: int = 5


@dataclass
class DatabaseConfig:
    """Database configuration."""
    database_url: str = field(
        default_factory=lambda: os.getenv(
            "DATABASE_URL",
            "sqlite:///crypto_trading_bot/data/trading_bot.db"
        )
    )


@dataclass
class Settings:
    """Main settings container."""
    kraken: KrakenConfig = field(default_factory=KrakenConfig)
    reddit: RedditConfig = field(default_factory=RedditConfig)
    twitter: TwitterConfig = field(default_factory=TwitterConfig)
    trading: TradingConfig = field(default_factory=TradingConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)

    # Paper trading mode (default: True for safety)
    paper_trading: bool = True

    # Logging
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    log_file: str = field(default_factory=lambda: os.getenv("LOG_FILE", "crypto_trading_bot/logs/trading_bot.log"))


# Global settings instance
settings = Settings()
