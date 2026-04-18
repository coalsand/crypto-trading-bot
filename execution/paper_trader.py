"""Paper trading simulator for testing strategies without real money."""

import logging
import random
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from ..config import settings, TRADEABLE_COINS
from ..storage import db, Trade, TradeStatus, TradeType, SignalType
from ..strategy import TradingSignal, RiskManager, PortfolioTracker, risk_manager

logger = logging.getLogger(__name__)


class PaperTrader:
    """Simulates trading without real money."""

    def __init__(
        self,
        initial_balance: float = 10000.0,
        slippage_pct: float = 0.001,  # 0.1% slippage
        fee_pct: float = 0.001  # 0.1% fee (Kraken maker fee)
    ):
        """
        Initialize the paper trader.

        Args:
            initial_balance: Starting balance in USD
            slippage_pct: Simulated slippage percentage
            fee_pct: Trading fee percentage
        """
        self.slippage_pct = slippage_pct
        self.fee_pct = fee_pct

        self.portfolio = PortfolioTracker(
            initial_balance=initial_balance,
            is_paper=True
        )

        self.risk_manager = risk_manager

    def execute_signal(
        self,
        signal: TradingSignal,
        current_prices: Dict[str, float]
    ) -> Optional[Trade]:
        """
        Execute a trading signal (paper trade).

        Args:
            signal: TradingSignal to execute
            current_prices: Current market prices

        Returns:
            Trade object if executed, None otherwise
        """
        if signal.signal_type == SignalType.HOLD:
            logger.debug(f"Signal is HOLD for {signal.symbol}, skipping")
            return None

        # Get current price
        current_price = current_prices.get(signal.symbol)
        if not current_price:
            logger.warning(f"No current price for {signal.symbol}")
            return None

        # Get open trades for risk assessment
        open_trades = db.get_open_trades(is_paper=True)

        # Assess risk
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
                f"Trade rejected for {signal.symbol}: "
                f"{'; '.join(assessment.rejection_reasons)}"
            )
            return None

        # Simulate execution with slippage
        executed_price = self._apply_slippage(
            current_price,
            trade_type
        )

        # Calculate fees
        fees = assessment.position_size_usd * self.fee_pct

        # Create trade record
        trade = Trade(
            symbol=signal.symbol,
            trade_type=TradeType.BUY if signal.signal_type == SignalType.BUY else TradeType.SELL,
            status=TradeStatus.OPEN,
            created_at=datetime.utcnow(),
            opened_at=datetime.utcnow(),
            entry_price=executed_price,
            quantity=assessment.quantity,
            position_size_usd=assessment.position_size_usd,
            stop_loss=assessment.stop_loss,
            take_profit=assessment.take_profit,
            fees=fees,
            entry_order_id=f"paper_{uuid.uuid4().hex[:12]}",
            is_paper=True,
            notes=f"Signal strength: {signal.strength:.2f}, confidence: {signal.confidence:.2f}"
        )

        # Save to database
        trade_id = db.create_trade(trade)
        trade.id = trade_id

        # Update portfolio
        self.portfolio.open_position(trade, current_price)

        logger.info(
            f"Paper trade executed: {trade.trade_type.value} {signal.symbol} "
            f"qty={trade.quantity:.6f} @ ${executed_price:.2f} "
            f"(SL: ${trade.stop_loss:.2f}, TP: ${trade.take_profit:.2f})"
        )

        return trade

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
        closed_trades = []
        open_trades = db.get_open_trades(is_paper=True)

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
        exit_price: float,
        reason: str
    ) -> Optional[Trade]:
        """
        Close a paper trading position.

        Args:
            trade: Trade to close
            exit_price: Exit price
            reason: Reason for closing

        Returns:
            Updated trade object
        """
        # Apply slippage to exit
        trade_type = "sell" if trade.trade_type == TradeType.BUY else "buy"
        executed_exit = self._apply_slippage(exit_price, trade_type)

        # Calculate fees
        exit_fees = trade.quantity * executed_exit * self.fee_pct
        total_fees = trade.fees + exit_fees

        # Calculate P&L
        if trade.trade_type == TradeType.BUY:
            pnl = (executed_exit - trade.entry_price) * trade.quantity - total_fees
        else:
            pnl = (trade.entry_price - executed_exit) * trade.quantity - total_fees

        pnl_pct = (pnl / trade.position_size_usd) * 100 if trade.position_size_usd else 0

        # Update trade
        db.update_trade(
            trade.id,
            status=TradeStatus.CLOSED,
            closed_at=datetime.utcnow(),
            exit_price=executed_exit,
            realized_pnl=pnl,
            realized_pnl_pct=pnl_pct,
            fees=total_fees,
            exit_order_id=f"paper_{uuid.uuid4().hex[:12]}",
            notes=f"{trade.notes or ''}\nClosed: {reason}"
        )

        # Update portfolio
        self.portfolio.close_position(trade.symbol, executed_exit, exit_fees)

        # Update risk manager daily P&L
        self.risk_manager.update_daily_pnl(pnl)

        logger.info(
            f"Position closed: {trade.symbol} @ ${executed_exit:.2f} "
            f"P&L: ${pnl:.2f} ({pnl_pct:+.2f}%) - {reason}"
        )

        # Update and return trade
        trade.status = TradeStatus.CLOSED
        trade.exit_price = executed_exit
        trade.realized_pnl = pnl
        trade.realized_pnl_pct = pnl_pct

        return trade

    def _apply_slippage(
        self,
        price: float,
        trade_type: str
    ) -> float:
        """
        Apply simulated slippage to a price.

        Args:
            price: Original price
            trade_type: "buy" or "sell"

        Returns:
            Price with slippage applied
        """
        # Random slippage between 0 and max slippage
        slippage = random.uniform(0, self.slippage_pct)

        if trade_type == "buy":
            # Buying is slightly more expensive
            return price * (1 + slippage)
        else:
            # Selling is slightly cheaper
            return price * (1 - slippage)

    def manual_close(
        self,
        symbol: str,
        current_price: float
    ) -> Optional[Trade]:
        """
        Manually close a position.

        Args:
            symbol: Symbol to close
            current_price: Current market price

        Returns:
            Closed trade or None
        """
        open_trades = db.get_open_trades(symbol=symbol, is_paper=True)

        if not open_trades:
            logger.warning(f"No open position for {symbol}")
            return None

        trade = open_trades[0]
        return self._close_position(trade, current_price, "manual_close")

    def update_portfolio_prices(self, prices: Dict[str, float]):
        """Update portfolio with current prices."""
        self.portfolio.update_prices(prices)

    def get_portfolio_summary(self) -> Dict:
        """Get current portfolio summary."""
        return self.portfolio.get_summary()

    def save_portfolio_snapshot(self):
        """Save current portfolio state."""
        self.portfolio.save_snapshot()


# Global instance
paper_trader = PaperTrader()
