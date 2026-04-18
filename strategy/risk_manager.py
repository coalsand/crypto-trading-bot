"""Risk management module for position sizing and trade validation."""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from ..config import settings, TRADEABLE_COINS
from ..storage import db, Trade, TradeStatus, TradeType

logger = logging.getLogger(__name__)


@dataclass
class RiskAssessment:
    """Result of risk assessment for a potential trade."""
    is_approved: bool
    position_size_usd: float
    position_size_pct: float
    quantity: float
    stop_loss: float
    take_profit: float
    risk_amount: float
    potential_reward: float
    risk_reward_ratio: float
    rejection_reasons: List[str]


class RiskManager:
    """Manages trading risk and position sizing."""

    def __init__(self):
        """Initialize the risk manager."""
        self.config = settings.trading
        self._daily_pnl = 0.0
        self._daily_pnl_reset_date = datetime.utcnow().date()

    def assess_trade(
        self,
        symbol: str,
        trade_type: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        portfolio_value: float,
        current_positions: List[Trade],
        suggested_position_pct: Optional[float] = None
    ) -> RiskAssessment:
        """
        Assess whether a trade should be executed and calculate position size.

        Args:
            symbol: Coin symbol
            trade_type: "buy" or "sell"
            entry_price: Proposed entry price
            stop_loss: Proposed stop-loss price
            take_profit: Proposed take-profit price
            portfolio_value: Current total portfolio value in USD
            current_positions: List of currently open trades
            suggested_position_pct: Suggested position size (optional)

        Returns:
            RiskAssessment with approval status and parameters
        """
        rejection_reasons = []

        # Check if trading is allowed (daily loss limit)
        self._check_daily_loss_limit()

        if self._daily_pnl <= -self.config.daily_loss_limit_pct * portfolio_value:
            rejection_reasons.append(
                f"Daily loss limit reached: ${self._daily_pnl:.2f}"
            )

        # Check max open positions
        open_positions = [t for t in current_positions if t.status == TradeStatus.OPEN]
        if len(open_positions) >= self.config.max_open_positions:
            rejection_reasons.append(
                f"Max open positions reached: {len(open_positions)}/{self.config.max_open_positions}"
            )

        # Check if already have position in this symbol
        existing_position = next(
            (t for t in open_positions if t.symbol == symbol),
            None
        )
        if existing_position:
            rejection_reasons.append(
                f"Already have open position in {symbol}"
            )

        # Check correlation (simplified - just check for similar assets)
        correlated_count = self._count_correlated_positions(symbol, open_positions)
        if correlated_count >= 2:
            rejection_reasons.append(
                f"Too many correlated positions: {correlated_count}"
            )

        # Calculate position size
        position_pct = suggested_position_pct or self.config.max_position_size_pct
        position_pct = min(position_pct, self.config.max_position_size_pct)
        position_pct = max(position_pct, self.config.min_position_size_pct)

        position_size_usd = portfolio_value * position_pct
        quantity = position_size_usd / entry_price

        # Calculate risk/reward
        if trade_type == "buy":
            risk_per_unit = entry_price - stop_loss
            reward_per_unit = take_profit - entry_price
        else:
            risk_per_unit = stop_loss - entry_price
            reward_per_unit = entry_price - take_profit

        risk_amount = risk_per_unit * quantity
        potential_reward = reward_per_unit * quantity

        # Calculate risk/reward ratio
        risk_reward_ratio = (
            potential_reward / risk_amount if risk_amount > 0 else 0
        )

        # Validate risk/reward ratio
        if risk_reward_ratio < self.config.take_profit_ratio:
            rejection_reasons.append(
                f"Risk/reward ratio too low: {risk_reward_ratio:.2f} "
                f"(min: {self.config.take_profit_ratio})"
            )

        # Validate stop-loss
        if trade_type == "buy" and stop_loss >= entry_price:
            rejection_reasons.append("Stop-loss must be below entry for buy")
        elif trade_type == "sell" and stop_loss <= entry_price:
            rejection_reasons.append("Stop-loss must be above entry for sell")

        # Validate take-profit
        if trade_type == "buy" and take_profit <= entry_price:
            rejection_reasons.append("Take-profit must be above entry for buy")
        elif trade_type == "sell" and take_profit >= entry_price:
            rejection_reasons.append("Take-profit must be below entry for sell")

        # Check minimum position size
        min_position = portfolio_value * self.config.min_position_size_pct
        if position_size_usd < min_position:
            rejection_reasons.append(
                f"Position size too small: ${position_size_usd:.2f}"
            )

        is_approved = len(rejection_reasons) == 0

        if not is_approved:
            logger.warning(
                f"Trade rejected for {symbol}: {'; '.join(rejection_reasons)}"
            )

        return RiskAssessment(
            is_approved=is_approved,
            position_size_usd=position_size_usd,
            position_size_pct=position_pct,
            quantity=quantity,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_amount=risk_amount,
            potential_reward=potential_reward,
            risk_reward_ratio=risk_reward_ratio,
            rejection_reasons=rejection_reasons
        )

    def _count_correlated_positions(
        self,
        symbol: str,
        open_positions: List[Trade]
    ) -> int:
        """
        Count positions in correlated assets.

        Simple correlation groups:
        - BTC: standalone
        - ETH, SOL, AVAX, ADA: smart contract platforms
        - DOGE: standalone (meme)
        """
        correlation_groups = {
            "BTC": ["BTC"],
            "ETH": ["ETH", "SOL", "AVAX", "ADA"],
            "SOL": ["ETH", "SOL", "AVAX", "ADA"],
            "AVAX": ["ETH", "SOL", "AVAX", "ADA"],
            "ADA": ["ETH", "SOL", "AVAX", "ADA"],
            "XRP": ["XRP"],
            "DOGE": ["DOGE"],
            "BNB": ["BNB"],
        }

        correlated = correlation_groups.get(symbol, [symbol])
        count = sum(1 for t in open_positions if t.symbol in correlated)

        return count

    def _check_daily_loss_limit(self):
        """Reset daily P&L tracker if new day."""
        today = datetime.utcnow().date()
        if today != self._daily_pnl_reset_date:
            self._daily_pnl = 0.0
            self._daily_pnl_reset_date = today

    def update_daily_pnl(self, pnl: float):
        """Update daily P&L tracker."""
        self._check_daily_loss_limit()
        self._daily_pnl += pnl
        logger.info(f"Daily P&L updated: ${self._daily_pnl:.2f}")

    def get_daily_pnl(self) -> float:
        """Get current daily P&L."""
        self._check_daily_loss_limit()
        return self._daily_pnl

    def should_close_position(
        self,
        trade: Trade,
        current_price: float
    ) -> Tuple[bool, str]:
        """
        Check if a position should be closed based on risk rules.

        Args:
            trade: Open trade to check
            current_price: Current market price

        Returns:
            Tuple of (should_close, reason)
        """
        if trade.trade_type == TradeType.BUY:
            # Check stop-loss
            if trade.stop_loss and current_price <= trade.stop_loss:
                return True, "stop_loss_hit"

            # Check take-profit
            if trade.take_profit and current_price >= trade.take_profit:
                return True, "take_profit_hit"

        else:  # SELL
            # Check stop-loss
            if trade.stop_loss and current_price >= trade.stop_loss:
                return True, "stop_loss_hit"

            # Check take-profit
            if trade.take_profit and current_price <= trade.take_profit:
                return True, "take_profit_hit"

        return False, ""

    def calculate_position_size(
        self,
        portfolio_value: float,
        entry_price: float,
        stop_loss: float,
        risk_per_trade_pct: Optional[float] = None
    ) -> Tuple[float, float]:
        """
        Calculate position size based on risk per trade.

        Args:
            portfolio_value: Total portfolio value
            entry_price: Entry price
            stop_loss: Stop-loss price
            risk_per_trade_pct: Maximum risk per trade (default from config)

        Returns:
            Tuple of (position_size_usd, quantity)
        """
        risk_pct = risk_per_trade_pct or self.config.max_position_size_pct
        max_risk_amount = portfolio_value * risk_pct

        # Calculate risk per unit
        risk_per_unit = abs(entry_price - stop_loss)

        if risk_per_unit <= 0:
            # No valid stop-loss, use max position
            position_size = max_risk_amount
            quantity = position_size / entry_price
        else:
            # Position size where risk = max_risk_amount
            quantity = max_risk_amount / risk_per_unit
            position_size = quantity * entry_price

            # Cap at max position size
            max_position = portfolio_value * self.config.max_position_size_pct
            if position_size > max_position:
                position_size = max_position
                quantity = position_size / entry_price

        return position_size, quantity

    def get_portfolio_exposure(
        self,
        open_trades: List[Trade],
        current_prices: Dict[str, float]
    ) -> Dict[str, float]:
        """
        Calculate current portfolio exposure by asset.

        Args:
            open_trades: List of open trades
            current_prices: Current prices for each symbol

        Returns:
            Dictionary mapping symbol to exposure in USD
        """
        exposure = {}

        for trade in open_trades:
            if trade.status != TradeStatus.OPEN:
                continue

            price = current_prices.get(trade.symbol, trade.entry_price)
            value = trade.quantity * price
            exposure[trade.symbol] = exposure.get(trade.symbol, 0) + value

        return exposure

    def get_total_risk(
        self,
        open_trades: List[Trade],
        current_prices: Dict[str, float]
    ) -> float:
        """
        Calculate total risk across all open positions.

        Args:
            open_trades: List of open trades
            current_prices: Current prices

        Returns:
            Total risk amount in USD
        """
        total_risk = 0.0

        for trade in open_trades:
            if trade.status != TradeStatus.OPEN or not trade.stop_loss:
                continue

            price = current_prices.get(trade.symbol, trade.entry_price)

            if trade.trade_type == TradeType.BUY:
                risk = max(0, price - trade.stop_loss) * trade.quantity
            else:
                risk = max(0, trade.stop_loss - price) * trade.quantity

            total_risk += risk

        return total_risk


# Global instance
risk_manager = RiskManager()
