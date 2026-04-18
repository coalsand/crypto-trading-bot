#!/usr/bin/env python3
"""
Crypto Trading Bot - Main Entry Point

An autonomous cryptocurrency trading bot using technical analysis
and multi-source sentiment analysis.

Usage:
    python -m crypto_trading_bot.main [options]

Options:
    --paper         Run in paper trading mode (default)
    --live          Run in live trading mode (USE WITH CAUTION)
    --once          Run once and exit (no scheduler)
    --debug         Enable debug logging
"""

import argparse
import signal
import sys
import time
from datetime import datetime
from typing import Dict

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from .config import settings, TRADEABLE_COINS
from .config.stocks import is_market_open
from .storage import db
from .data import (
    market_data_fetcher, reddit_collector, twitter_collector, news_collector,
    stock_data, stock_screener,
)
from .analysis import technical_analyzer, sentiment_analyzer
from .strategy import signal_generator, portfolio_tracker
from .execution import paper_trader, live_trader, paper_order_manager
from .utils import setup_logging, get_logger, trade_logger

# Initialize logging
logger = get_logger("main")


class TradingBot:
    """Main trading bot orchestrator."""

    def __init__(self, paper_mode: bool = True):
        """
        Initialize the trading bot.

        Args:
            paper_mode: Whether to run in paper trading mode
        """
        self.paper_mode = paper_mode
        self.trader = paper_trader if paper_mode else live_trader
        self.order_manager = paper_order_manager

        self.scheduler = None
        self.running = False

        # Active stock list — populated by the daily screener
        self.active_stocks: list = []

        # Initialize database
        db.create_tables()

        logger.info(f"Trading bot initialized - Mode: {'PAPER' if paper_mode else 'LIVE'}")

    def fetch_market_data(self) -> Dict:
        """Fetch market data for all tradeable coins."""
        logger.info("Fetching market data...")

        try:
            data = market_data_fetcher.fetch_all_coins_ohlcv(
                timeframe="1h",
                limit=500
            )
            logger.info(f"Fetched data for {len(data)} coins")
            return data

        except Exception as e:
            logger.error(f"Error fetching market data: {e}")
            return {}

    def fetch_sentiment_data(self):
        """Fetch sentiment data from all sources."""
        logger.info("Fetching sentiment data...")

        try:
            # Reddit
            reddit_results = reddit_collector.fetch_all_coins(
                sentiment_analyzer,
                limit_per_coin=50
            )
            logger.info(f"Reddit sentiment: {sum(reddit_results.values())} records")

        except Exception as e:
            logger.error(f"Error fetching Reddit data: {e}")

        try:
            # Twitter
            twitter_results = twitter_collector.fetch_all_coins(
                sentiment_analyzer,
                max_results_per_coin=50
            )
            logger.info(f"Twitter sentiment: {sum(twitter_results.values())} records")

        except Exception as e:
            logger.error(f"Error fetching Twitter data: {e}")

        try:
            # News
            news_results = news_collector.fetch_all_coins(
                sentiment_analyzer,
                hours=24
            )
            logger.info(f"News sentiment: {sum(news_results.values())} records")

        except Exception as e:
            logger.error(f"Error fetching news data: {e}")

    def get_current_prices(self) -> Dict[str, float]:
        """Get current prices for all coins."""
        try:
            return market_data_fetcher.get_current_prices()
        except Exception as e:
            logger.error(f"Error getting current prices: {e}")
            return {}

    def run_stock_screen(self):
        """Refresh the active stock list via the screener."""
        if not settings.enable_stocks:
            return
        try:
            logger.info("Running stock screener (NASDAQ-100 → top candidates)...")
            self.active_stocks = stock_screener.screen()
            logger.info(f"Active stock list: {self.active_stocks}")
        except Exception as e:
            logger.exception(f"Stock screener failed: {e}")

    def run_stock_cycle(self, current_prices: dict):
        """Run signal generation + execution for the active stock list."""
        if not settings.enable_stocks:
            return
        if not self.active_stocks:
            logger.debug("Stock cycle: no active stocks; run the screener first")
            return
        if not is_market_open():
            logger.debug("Stock cycle skipped: US equities market is closed")
            return

        try:
            bars = stock_data.fetch_all_ohlcv(self.active_stocks)
            if not bars:
                logger.warning("Stock cycle: no OHLCV for active list")
                return

            stock_prices = stock_data.get_current_prices(self.active_stocks)
            combined_prices = {**current_prices, **stock_prices}
            self.trader.update_portfolio_prices(combined_prices)
            self.trader.check_and_close_positions(combined_prices)

            signals = signal_generator.generate_all_signals(
                bars,
                symbols=list(bars.keys()),
                asset_type="stock",
            )
            signal_generator.save_signals(signals)

            actionable = signal_generator.get_actionable_signals(signals)
            logger.info(f"Stock cycle: {len(actionable)} actionable stock signals")
            for sig in actionable:
                trade = self.trader.execute_signal(sig, combined_prices)
                if trade:
                    trade_logger.log_trade_open(
                        trade.symbol, trade.trade_type.value, trade.quantity,
                        trade.entry_price, trade.stop_loss, trade.take_profit,
                        self.paper_mode,
                    )
        except Exception as e:
            logger.exception(f"Error in stock cycle: {e}")

    def run_trading_cycle(self):
        """Run a complete trading cycle."""
        logger.info("=" * 50)
        logger.info(f"Starting trading cycle at {datetime.utcnow()}")
        logger.info("=" * 50)

        try:
            # 1. Fetch market data
            market_data = self.fetch_market_data()
            if not market_data:
                logger.warning("No market data available, skipping cycle")
                return

            # 2. Get current prices
            current_prices = self.get_current_prices()
            if not current_prices:
                logger.warning("No current prices available, skipping cycle")
                return

            # 3. Update portfolio with current prices
            self.trader.update_portfolio_prices(current_prices)

            # 4. Check and close positions that hit SL/TP
            closed_trades = self.trader.check_and_close_positions(current_prices)
            if closed_trades:
                logger.info(f"Closed {len(closed_trades)} positions")
                for trade in closed_trades:
                    trade_logger.log_trade_close(
                        trade.symbol,
                        trade.exit_price,
                        trade.realized_pnl,
                        trade.realized_pnl_pct,
                        "stop_loss/take_profit",
                        self.paper_mode
                    )

            # 5. Generate signals
            logger.info("Generating trading signals...")
            signals = signal_generator.generate_all_signals(
                market_data,
                sentiment_hours=24
            )

            # Log signals
            for sig in signals:
                trade_logger.log_signal(
                    sig.symbol,
                    sig.signal_type.value,
                    sig.technical_score,
                    sig.sentiment_score,
                    sig.combined_score
                )

            # 6. Get actionable signals
            actionable = signal_generator.get_actionable_signals(signals)
            logger.info(f"Found {len(actionable)} actionable signals")

            # 7. Execute signals
            for signal in actionable:
                trade = self.trader.execute_signal(signal, current_prices)
                if trade:
                    trade_logger.log_trade_open(
                        trade.symbol,
                        trade.trade_type.value,
                        trade.quantity,
                        trade.entry_price,
                        trade.stop_loss,
                        trade.take_profit,
                        self.paper_mode
                    )

            # 8. Stock cycle (uses the same trader/portfolio; no-op if disabled or market closed)
            self.run_stock_cycle(current_prices)

            # 9. Save portfolio snapshot
            self.trader.save_portfolio_snapshot()

            # 9. Log portfolio state
            summary = self.trader.get_portfolio_summary()
            trade_logger.log_portfolio_update(
                summary["total_value"],
                summary["cash_balance"],
                summary["positions_value"],
                summary["unrealized_pnl"],
                self.paper_mode
            )

            # 10. Log daily summary
            daily = self.order_manager.get_daily_summary()
            logger.info(
                f"Daily summary: {daily['trades_opened']} opened, "
                f"{daily['trades_closed']} closed, "
                f"P&L: ${daily['total_pnl']:.2f}"
            )

        except Exception as e:
            logger.exception(f"Error in trading cycle: {e}")

        logger.info(f"Trading cycle completed at {datetime.utcnow()}")
        logger.info("=" * 50)

    def run_sentiment_update(self):
        """Run sentiment data collection."""
        logger.info("Running sentiment update...")
        self.fetch_sentiment_data()
        logger.info("Sentiment update completed")

    def start(self):
        """Start the trading bot with scheduler."""
        logger.info("Starting trading bot scheduler...")

        self.scheduler = BlockingScheduler()
        self.running = True

        # Schedule trading cycle
        self.scheduler.add_job(
            self.run_trading_cycle,
            IntervalTrigger(minutes=settings.scheduler.signal_check_interval_minutes),
            id="trading_cycle",
            name="Trading Cycle",
            next_run_time=datetime.utcnow()  # Run immediately on start
        )

        # Schedule sentiment update (less frequent)
        self.scheduler.add_job(
            self.run_sentiment_update,
            IntervalTrigger(minutes=settings.scheduler.sentiment_update_interval_minutes),
            id="sentiment_update",
            name="Sentiment Update",
            next_run_time=datetime.utcnow()  # Run immediately on start
        )

        # Schedule stock screener (daily refresh of the active list)
        if settings.enable_stocks:
            self.scheduler.add_job(
                self.run_stock_screen,
                IntervalTrigger(hours=24),
                id="stock_screen",
                name="Stock Screener",
                next_run_time=datetime.utcnow()  # Seed the list immediately
            )

        # Handle shutdown signals
        def shutdown(signum, frame):
            logger.info("Shutdown signal received, stopping bot...")
            self.stop()

        signal.signal(signal.SIGINT, shutdown)
        signal.signal(signal.SIGTERM, shutdown)

        try:
            logger.info("Scheduler started - Press Ctrl+C to stop")
            self.scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            pass

    def stop(self):
        """Stop the trading bot."""
        logger.info("Stopping trading bot...")
        self.running = False

        if self.scheduler:
            self.scheduler.shutdown(wait=False)

        # Save final portfolio state
        self.trader.save_portfolio_snapshot()

        logger.info("Trading bot stopped")

    def run_once(self):
        """Run a single trading cycle and exit."""
        logger.info("Running single trading cycle...")

        # Fetch sentiment first
        self.run_sentiment_update()

        # Seed the active stock list before the trading cycle
        if settings.enable_stocks:
            self.run_stock_screen()

        # Run trading cycle (includes stock cycle if enabled)
        self.run_trading_cycle()

        logger.info("Single cycle completed")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Crypto Trading Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument(
        "--paper",
        action="store_true",
        default=True,
        help="Run in paper trading mode (default)"
    )

    parser.add_argument(
        "--live",
        action="store_true",
        help="Run in live trading mode (USE WITH CAUTION)"
    )

    parser.add_argument(
        "--once",
        action="store_true",
        help="Run once and exit (no scheduler)"
    )

    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging"
    )

    args = parser.parse_args()

    # Setup logging
    log_level = "DEBUG" if args.debug else settings.log_level
    setup_logging(log_level=log_level)

    # Determine trading mode
    paper_mode = not args.live

    if args.live:
        logger.warning("=" * 60)
        logger.warning("LIVE TRADING MODE ENABLED - REAL MONEY WILL BE USED")
        logger.warning("=" * 60)
        response = input("Are you sure you want to continue? (yes/no): ")
        if response.lower() != "yes":
            logger.info("Aborted by user")
            sys.exit(0)

    # Create and run bot
    bot = TradingBot(paper_mode=paper_mode)

    if args.once:
        bot.run_once()
    else:
        bot.start()


if __name__ == "__main__":
    main()
