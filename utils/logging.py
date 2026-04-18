"""Logging configuration for the trading bot."""

import logging
import os
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from ..config import settings


def setup_logging(
    log_level: str = None,
    log_file: str = None,
    console_output: bool = True
) -> logging.Logger:
    """
    Configure logging for the trading bot.

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
        log_file: Path to log file
        console_output: Whether to output to console

    Returns:
        Configured logger
    """
    log_level = log_level or settings.log_level
    log_file = log_file or settings.log_file

    # Create logs directory
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Get root logger
    logger = logging.getLogger("crypto_trading_bot")
    logger.setLevel(getattr(logging, log_level.upper()))

    # Clear existing handlers
    logger.handlers = []

    # Create formatters
    detailed_formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    console_formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S"
    )

    # File handler with rotation
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(detailed_formatter)
    logger.addHandler(file_handler)

    # Console handler
    if console_output:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(getattr(logging, log_level.upper()))
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)

    # Set logging level for third-party libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("ccxt").setLevel(logging.WARNING)
    logging.getLogger("praw").setLevel(logging.WARNING)
    logging.getLogger("tweepy").setLevel(logging.WARNING)
    logging.getLogger("transformers").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)

    logger.info(f"Logging initialized - Level: {log_level}, File: {log_file}")

    return logger


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger for a specific module.

    Args:
        name: Module name

    Returns:
        Logger instance
    """
    return logging.getLogger(f"crypto_trading_bot.{name}")


class TradeLogger:
    """Specialized logger for trade events."""

    def __init__(self):
        self.logger = get_logger("trades")
        self.trade_log_file = Path(settings.log_file).parent / "trades.log"

        # Create trade-specific file handler
        handler = RotatingFileHandler(
            self.trade_log_file,
            maxBytes=5 * 1024 * 1024,
            backupCount=10,
            encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter(
            "%(asctime)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        ))
        self.logger.addHandler(handler)

    def log_signal(
        self,
        symbol: str,
        signal_type: str,
        technical_score: float,
        sentiment_score: float,
        combined_score: float
    ):
        """Log a trading signal."""
        self.logger.info(
            f"SIGNAL | {symbol} | {signal_type.upper()} | "
            f"Tech: {technical_score:.3f} | Sent: {sentiment_score:.3f} | "
            f"Combined: {combined_score:.3f}"
        )

    def log_trade_open(
        self,
        symbol: str,
        trade_type: str,
        quantity: float,
        price: float,
        stop_loss: float,
        take_profit: float,
        is_paper: bool
    ):
        """Log trade opening."""
        mode = "PAPER" if is_paper else "LIVE"
        self.logger.info(
            f"OPEN | {mode} | {symbol} | {trade_type.upper()} | "
            f"Qty: {quantity:.6f} | Price: ${price:.2f} | "
            f"SL: ${stop_loss:.2f} | TP: ${take_profit:.2f}"
        )

    def log_trade_close(
        self,
        symbol: str,
        exit_price: float,
        pnl: float,
        pnl_pct: float,
        reason: str,
        is_paper: bool
    ):
        """Log trade closing."""
        mode = "PAPER" if is_paper else "LIVE"
        pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
        self.logger.info(
            f"CLOSE | {mode} | {symbol} | Exit: ${exit_price:.2f} | "
            f"P&L: {pnl_str} ({pnl_pct:+.2f}%) | Reason: {reason}"
        )

    def log_portfolio_update(
        self,
        total_value: float,
        cash: float,
        positions_value: float,
        unrealized_pnl: float,
        is_paper: bool
    ):
        """Log portfolio state update."""
        mode = "PAPER" if is_paper else "LIVE"
        self.logger.info(
            f"PORTFOLIO | {mode} | Total: ${total_value:.2f} | "
            f"Cash: ${cash:.2f} | Positions: ${positions_value:.2f} | "
            f"Unrealized: ${unrealized_pnl:.2f}"
        )


# Global trade logger
trade_logger = TradeLogger()
