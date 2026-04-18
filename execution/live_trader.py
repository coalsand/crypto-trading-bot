"""Live trading execution via Kraken API."""

import logging
import time
from datetime import datetime
from typing import Dict, List, Optional

import ccxt

from ..config import settings, TRADEABLE_COINS
from ..storage import db, Trade, TradeStatus, TradeType, SignalType
from ..strategy import TradingSignal, RiskManager, PortfolioTracker, risk_manager

logger = logging.getLogger(__name__)


class LiveTrader:
    """
    Executes live trades on Kraken exchange.

    WARNING: This trades with real money. Use with extreme caution.
    """

    def __init__(self):
        """Initialize the live trader."""
        if settings.paper_trading:
            logger.warning(
                "Paper trading mode is enabled. "
                "Live trader will not execute real trades."
            )

        self.exchange = ccxt.kraken({
            "apiKey": settings.kraken.api_key,
            "secret": settings.kraken.api_secret,
            "enableRateLimit": True,
        })

        self.portfolio = PortfolioTracker(is_paper=False)
        self.risk_manager = risk_manager
        self._markets_loaded = False

    def _ensure_markets_loaded(self):
        """Ensure markets are loaded."""
        if not self._markets_loaded:
            self.exchange.load_markets()
            self._markets_loaded = True

    def _is_live_trading_enabled(self) -> bool:
        """Check if live trading is enabled."""
        if settings.paper_trading:
            logger.warning("Live trading disabled - paper trading mode is on")
            return False

        if not settings.kraken.api_key or not settings.kraken.api_secret:
            logger.error("Kraken API credentials not configured")
            return False

        return True

    def execute_signal(
        self,
        signal: TradingSignal,
        current_prices: Dict[str, float]
    ) -> Optional[Trade]:
        """
        Execute a trading signal on Kraken.

        Args:
            signal: TradingSignal to execute
            current_prices: Current market prices

        Returns:
            Trade object if executed, None otherwise
        """
        if not self._is_live_trading_enabled():
            logger.info("Live trading not enabled, skipping execution")
            return None

        if signal.signal_type == SignalType.HOLD:
            return None

        self._ensure_markets_loaded()

        # Get coin info
        coin_info = TRADEABLE_COINS.get(signal.symbol)
        if not coin_info:
            logger.error(f"Unknown symbol: {signal.symbol}")
            return None

        kraken_pair = coin_info.kraken_pair
        current_price = current_prices.get(signal.symbol)

        if not current_price:
            logger.warning(f"No current price for {signal.symbol}")
            return None

        # Risk assessment
        open_trades = db.get_open_trades(is_paper=False)
        trade_type = "buy" if signal.signal_type == SignalType.BUY else "sell"

        assessment = self.risk_manager.assess_trade(
            symbol=signal.symbol,
            trade_type=trade_type,
            entry_price=signal.entry_price or current_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            portfolio_value=self.portfolio.state.total_value,
            current_positions=open_trades,
            suggested_position_pct=signal.position_size_pct
        )

        if not assessment.is_approved:
            logger.info(
                f"Trade rejected: {'; '.join(assessment.rejection_reasons)}"
            )
            return None

        try:
            # Place market order
            order = self._place_order(
                symbol=kraken_pair,
                side=trade_type,
                amount=assessment.quantity,
                order_type="market"
            )

            if not order:
                return None

            # Get executed price
            executed_price = order.get("average") or order.get("price") or current_price
            fees = order.get("fee", {}).get("cost", 0)

            # Create trade record
            trade = Trade(
                symbol=signal.symbol,
                trade_type=TradeType.BUY if signal.signal_type == SignalType.BUY else TradeType.SELL,
                status=TradeStatus.OPEN,
                created_at=datetime.utcnow(),
                opened_at=datetime.utcnow(),
                entry_price=executed_price,
                quantity=order.get("filled", assessment.quantity),
                position_size_usd=assessment.position_size_usd,
                stop_loss=assessment.stop_loss,
                take_profit=assessment.take_profit,
                fees=fees,
                entry_order_id=order.get("id"),
                is_paper=False,
                notes=f"Kraken order: {order.get('id')}"
            )

            # Save to database
            trade_id = db.create_trade(trade)
            trade.id = trade_id

            # Update portfolio
            self.portfolio.open_position(trade, current_price)

            logger.info(
                f"LIVE trade executed: {trade.trade_type.value} {signal.symbol} "
                f"qty={trade.quantity:.6f} @ ${executed_price:.2f}"
            )

            return trade

        except ccxt.InsufficientFunds as e:
            logger.error(f"Insufficient funds for {signal.symbol}: {e}")
        except ccxt.InvalidOrder as e:
            logger.error(f"Invalid order for {signal.symbol}: {e}")
        except ccxt.ExchangeError as e:
            logger.error(f"Exchange error for {signal.symbol}: {e}")
        except Exception as e:
            logger.error(f"Error executing trade for {signal.symbol}: {e}")

        return None

    def _place_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        order_type: str = "market",
        price: Optional[float] = None
    ) -> Optional[dict]:
        """
        Place an order on Kraken.

        Args:
            symbol: Trading pair (e.g., "BTC/USD")
            side: "buy" or "sell"
            amount: Quantity to trade
            order_type: "market" or "limit"
            price: Limit price (for limit orders)

        Returns:
            Order response or None on error
        """
        try:
            if order_type == "market":
                order = self.exchange.create_market_order(symbol, side, amount)
            else:
                if price is None:
                    raise ValueError("Price required for limit orders")
                order = self.exchange.create_limit_order(symbol, side, amount, price)

            logger.info(f"Order placed: {order.get('id')} - {side} {amount} {symbol}")
            return order

        except Exception as e:
            logger.error(f"Failed to place order: {e}")
            return None

    def check_and_close_positions(
        self,
        current_prices: Dict[str, float]
    ) -> List[Trade]:
        """
        Check all open positions and close if stop-loss or take-profit hit.

        Args:
            current_prices: Current market prices

        Returns:
            List of closed trades
        """
        if not self._is_live_trading_enabled():
            return []

        closed_trades = []
        open_trades = db.get_open_trades(is_paper=False)

        for trade in open_trades:
            current_price = current_prices.get(trade.symbol)
            if not current_price:
                continue

            should_close, reason = self.risk_manager.should_close_position(
                trade,
                current_price
            )

            if should_close:
                closed_trade = self._close_position(trade, current_price, reason)
                if closed_trade:
                    closed_trades.append(closed_trade)

        return closed_trades

    def _close_position(
        self,
        trade: Trade,
        current_price: float,
        reason: str
    ) -> Optional[Trade]:
        """
        Close a live trading position.

        Args:
            trade: Trade to close
            current_price: Current market price
            reason: Reason for closing

        Returns:
            Updated trade object or None on error
        """
        coin_info = TRADEABLE_COINS.get(trade.symbol)
        if not coin_info:
            logger.error(f"Unknown symbol: {trade.symbol}")
            return None

        # Determine close side (opposite of entry)
        close_side = "sell" if trade.trade_type == TradeType.BUY else "buy"

        try:
            order = self._place_order(
                symbol=coin_info.kraken_pair,
                side=close_side,
                amount=trade.quantity,
                order_type="market"
            )

            if not order:
                return None

            # Get executed price
            exit_price = order.get("average") or order.get("price") or current_price
            exit_fees = order.get("fee", {}).get("cost", 0)
            total_fees = trade.fees + exit_fees

            # Calculate P&L
            if trade.trade_type == TradeType.BUY:
                pnl = (exit_price - trade.entry_price) * trade.quantity - total_fees
            else:
                pnl = (trade.entry_price - exit_price) * trade.quantity - total_fees

            pnl_pct = (pnl / trade.position_size_usd) * 100 if trade.position_size_usd else 0

            # Update trade
            db.update_trade(
                trade.id,
                status=TradeStatus.CLOSED,
                closed_at=datetime.utcnow(),
                exit_price=exit_price,
                realized_pnl=pnl,
                realized_pnl_pct=pnl_pct,
                fees=total_fees,
                exit_order_id=order.get("id"),
                notes=f"{trade.notes or ''}\nClosed: {reason}"
            )

            # Update portfolio
            self.portfolio.close_position(trade.symbol, exit_price, exit_fees)

            # Update risk manager
            self.risk_manager.update_daily_pnl(pnl)

            logger.info(
                f"LIVE position closed: {trade.symbol} @ ${exit_price:.2f} "
                f"P&L: ${pnl:.2f} ({pnl_pct:+.2f}%) - {reason}"
            )

            trade.status = TradeStatus.CLOSED
            trade.exit_price = exit_price
            trade.realized_pnl = pnl

            return trade

        except Exception as e:
            logger.error(f"Failed to close position {trade.symbol}: {e}")
            return None

    def get_account_balance(self) -> Dict[str, float]:
        """Get account balances from Kraken."""
        if not self._is_live_trading_enabled():
            return {}

        try:
            self._ensure_markets_loaded()
            balance = self.exchange.fetch_balance()
            return balance.get("total", {})
        except Exception as e:
            logger.error(f"Failed to fetch balance: {e}")
            return {}

    def sync_portfolio_from_exchange(self):
        """Sync portfolio state with Kraken account."""
        if not self._is_live_trading_enabled():
            return

        balance = self.get_account_balance()
        if not balance:
            return

        # Update cash balance (USD or USDT)
        usd_balance = balance.get("USD", 0) + balance.get("ZUSD", 0)
        self.portfolio.state.cash_balance = usd_balance

        logger.info(f"Synced portfolio from Kraken: ${usd_balance:.2f} USD")


# Global instance
live_trader = LiveTrader()
