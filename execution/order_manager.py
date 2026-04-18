"""Order management and trade tracking."""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from ..config import settings
from ..storage import db, Trade, TradeStatus, TradeType, PerformanceMetrics

logger = logging.getLogger(__name__)


class OrderManager:
    """Manages orders and provides trade analytics."""

    def __init__(self, is_paper: bool = True):
        """
        Initialize the order manager.

        Args:
            is_paper: Whether managing paper or live trades
        """
        self.is_paper = is_paper

    def get_open_positions(self) -> List[Trade]:
        """Get all open positions."""
        return db.get_open_trades(is_paper=self.is_paper)

    def get_position(self, symbol: str) -> Optional[Trade]:
        """Get open position for a specific symbol."""
        positions = db.get_open_trades(symbol=symbol, is_paper=self.is_paper)
        return positions[0] if positions else None

    def get_trade_history(
        self,
        days: int = 30,
        symbol: Optional[str] = None
    ) -> List[Trade]:
        """
        Get trade history.

        Args:
            days: Number of days to look back
            symbol: Filter by symbol (optional)

        Returns:
            List of trades
        """
        start_time = datetime.utcnow() - timedelta(days=days)
        end_time = datetime.utcnow()

        trades = db.get_trades_for_period(start_time, end_time, self.is_paper)

        if symbol:
            trades = [t for t in trades if t.symbol == symbol]

        return trades

    def get_performance_summary(
        self,
        days: int = 30
    ) -> Dict:
        """
        Get performance summary for a period.

        Args:
            days: Number of days to analyze

        Returns:
            Performance metrics dictionary
        """
        trades = self.get_trade_history(days=days)
        metrics = db.calculate_performance_metrics(trades)

        # Add additional metrics
        closed_trades = [t for t in trades if t.status == TradeStatus.CLOSED]

        if closed_trades:
            # Best and worst trades
            by_pnl = sorted(closed_trades, key=lambda t: t.realized_pnl or 0)
            metrics["worst_trade"] = {
                "symbol": by_pnl[0].symbol,
                "pnl": by_pnl[0].realized_pnl
            }
            metrics["best_trade"] = {
                "symbol": by_pnl[-1].symbol,
                "pnl": by_pnl[-1].realized_pnl
            }

            # Average holding time
            holding_times = []
            for t in closed_trades:
                if t.opened_at and t.closed_at:
                    holding_times.append(
                        (t.closed_at - t.opened_at).total_seconds() / 3600
                    )
            if holding_times:
                metrics["avg_holding_hours"] = sum(holding_times) / len(holding_times)

            # Win/loss streaks
            metrics["longest_win_streak"] = self._calculate_streak(closed_trades, True)
            metrics["longest_loss_streak"] = self._calculate_streak(closed_trades, False)

        # Asset breakdown
        metrics["by_asset"] = self._get_asset_breakdown(closed_trades)

        return metrics

    def _calculate_streak(
        self,
        trades: List[Trade],
        winning: bool
    ) -> int:
        """Calculate longest win or loss streak."""
        if not trades:
            return 0

        # Sort by close time
        sorted_trades = sorted(trades, key=lambda t: t.closed_at or datetime.min)

        max_streak = 0
        current_streak = 0

        for trade in sorted_trades:
            is_win = (trade.realized_pnl or 0) > 0

            if is_win == winning:
                current_streak += 1
                max_streak = max(max_streak, current_streak)
            else:
                current_streak = 0

        return max_streak

    def _get_asset_breakdown(
        self,
        trades: List[Trade]
    ) -> Dict[str, Dict]:
        """Get performance breakdown by asset."""
        breakdown = {}

        for trade in trades:
            if trade.symbol not in breakdown:
                breakdown[trade.symbol] = {
                    "trades": 0,
                    "wins": 0,
                    "total_pnl": 0.0
                }

            breakdown[trade.symbol]["trades"] += 1
            if (trade.realized_pnl or 0) > 0:
                breakdown[trade.symbol]["wins"] += 1
            breakdown[trade.symbol]["total_pnl"] += trade.realized_pnl or 0

        # Calculate win rate per asset
        for symbol in breakdown:
            trades_count = breakdown[symbol]["trades"]
            wins = breakdown[symbol]["wins"]
            breakdown[symbol]["win_rate"] = wins / trades_count if trades_count > 0 else 0

        return breakdown

    def save_performance_metrics(self, period: str = "daily"):
        """
        Calculate and save performance metrics.

        Args:
            period: "daily", "weekly", "monthly", or "all_time"
        """
        days_map = {
            "daily": 1,
            "weekly": 7,
            "monthly": 30,
            "all_time": 365 * 10
        }

        days = days_map.get(period, 30)
        trades = self.get_trade_history(days=days)
        metrics_dict = db.calculate_performance_metrics(trades)

        metrics = PerformanceMetrics(
            timestamp=datetime.utcnow(),
            period=period,
            total_trades=metrics_dict["total_trades"],
            winning_trades=metrics_dict["winning_trades"],
            losing_trades=metrics_dict["losing_trades"],
            win_rate=metrics_dict["win_rate"],
            total_pnl=metrics_dict["total_pnl"],
            average_win=metrics_dict["average_win"],
            average_loss=metrics_dict["average_loss"],
            profit_factor=metrics_dict["profit_factor"],
            is_paper=self.is_paper
        )

        db.save_performance_metrics(metrics)
        logger.info(f"Saved {period} performance metrics")

    def get_daily_summary(self) -> Dict:
        """Get today's trading summary."""
        today_start = datetime.utcnow().replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        trades = db.get_trades_for_period(
            today_start,
            datetime.utcnow(),
            self.is_paper
        )

        closed_today = [t for t in trades if t.status == TradeStatus.CLOSED]
        opened_today = [t for t in trades if t.opened_at and t.opened_at >= today_start]

        total_pnl = sum(t.realized_pnl or 0 for t in closed_today)
        wins = sum(1 for t in closed_today if (t.realized_pnl or 0) > 0)

        return {
            "date": today_start.date().isoformat(),
            "trades_opened": len(opened_today),
            "trades_closed": len(closed_today),
            "total_pnl": total_pnl,
            "wins": wins,
            "losses": len(closed_today) - wins,
            "win_rate": wins / len(closed_today) if closed_today else 0
        }

    def get_open_positions_summary(
        self,
        current_prices: Dict[str, float]
    ) -> Dict:
        """
        Get summary of open positions with current values.

        Args:
            current_prices: Current market prices

        Returns:
            Summary of open positions
        """
        positions = self.get_open_positions()

        summary = {
            "count": len(positions),
            "total_value": 0.0,
            "total_unrealized_pnl": 0.0,
            "positions": []
        }

        for pos in positions:
            current_price = current_prices.get(pos.symbol, pos.entry_price)
            value = pos.quantity * current_price

            if pos.trade_type == TradeType.BUY:
                unrealized = (current_price - pos.entry_price) * pos.quantity
            else:
                unrealized = (pos.entry_price - current_price) * pos.quantity

            unrealized_pct = (unrealized / pos.position_size_usd * 100
                            if pos.position_size_usd else 0)

            summary["total_value"] += value
            summary["total_unrealized_pnl"] += unrealized

            summary["positions"].append({
                "symbol": pos.symbol,
                "type": pos.trade_type.value,
                "quantity": pos.quantity,
                "entry_price": pos.entry_price,
                "current_price": current_price,
                "value": value,
                "unrealized_pnl": unrealized,
                "unrealized_pnl_pct": unrealized_pct,
                "stop_loss": pos.stop_loss,
                "take_profit": pos.take_profit,
                "opened_at": pos.opened_at.isoformat() if pos.opened_at else None
            })

        return summary


# Global instances
paper_order_manager = OrderManager(is_paper=True)
live_order_manager = OrderManager(is_paper=False)
