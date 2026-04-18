"""Portfolio state tracking and management."""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from ..config import settings
from ..storage import db, Trade, Portfolio, TradeStatus, TradeType

logger = logging.getLogger(__name__)


@dataclass
class Position:
    """Represents an open position."""
    symbol: str
    trade_id: int
    trade_type: TradeType
    quantity: float
    entry_price: float
    current_price: float
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None

    @property
    def value(self) -> float:
        """Current value of the position."""
        return self.quantity * self.current_price

    @property
    def cost_basis(self) -> float:
        """Original cost of the position."""
        return self.quantity * self.entry_price

    @property
    def unrealized_pnl(self) -> float:
        """Unrealized profit/loss."""
        if self.trade_type == TradeType.BUY:
            return (self.current_price - self.entry_price) * self.quantity
        else:
            return (self.entry_price - self.current_price) * self.quantity

    @property
    def unrealized_pnl_pct(self) -> float:
        """Unrealized P&L as percentage."""
        if self.cost_basis == 0:
            return 0.0
        return (self.unrealized_pnl / self.cost_basis) * 100


@dataclass
class PortfolioState:
    """Current state of the portfolio."""
    timestamp: datetime = field(default_factory=datetime.utcnow)
    cash_balance: float = 10000.0  # Default starting balance
    positions: List[Position] = field(default_factory=list)

    # Performance tracking
    starting_value: float = 10000.0
    peak_value: float = 10000.0

    is_paper: bool = True

    @property
    def positions_value(self) -> float:
        """Total value of all positions."""
        return sum(p.value for p in self.positions)

    @property
    def total_value(self) -> float:
        """Total portfolio value (cash + positions)."""
        return self.cash_balance + self.positions_value

    @property
    def unrealized_pnl(self) -> float:
        """Total unrealized P&L."""
        return sum(p.unrealized_pnl for p in self.positions)

    @property
    def total_pnl(self) -> float:
        """Total P&L since start."""
        return self.total_value - self.starting_value

    @property
    def total_pnl_pct(self) -> float:
        """Total P&L as percentage."""
        if self.starting_value == 0:
            return 0.0
        return (self.total_pnl / self.starting_value) * 100

    @property
    def max_drawdown(self) -> float:
        """Current drawdown from peak."""
        if self.peak_value == 0:
            return 0.0
        return (self.peak_value - self.total_value) / self.peak_value

    @property
    def open_positions_count(self) -> int:
        """Number of open positions."""
        return len(self.positions)

    def get_position(self, symbol: str) -> Optional[Position]:
        """Get position for a symbol."""
        for p in self.positions:
            if p.symbol == symbol:
                return p
        return None

    def has_position(self, symbol: str) -> bool:
        """Check if portfolio has position in symbol."""
        return self.get_position(symbol) is not None


class PortfolioTracker:
    """Tracks portfolio state and performance."""

    def __init__(
        self,
        initial_balance: float = 10000.0,
        is_paper: bool = True
    ):
        """
        Initialize portfolio tracker.

        Args:
            initial_balance: Starting cash balance
            is_paper: Whether this is paper trading
        """
        self.is_paper = is_paper
        self.state = PortfolioState(
            cash_balance=initial_balance,
            starting_value=initial_balance,
            peak_value=initial_balance,
            is_paper=is_paper
        )

        # Load existing state from database
        self._load_state()

    def _load_state(self):
        """Load portfolio state from database."""
        try:
            # Get latest portfolio snapshot
            portfolio = db.get_latest_portfolio(self.is_paper)
            if portfolio:
                self.state.cash_balance = portfolio.cash_balance_usd
                self.state.starting_value = portfolio.total_value_usd - portfolio.total_pnl
                self.state.peak_value = max(
                    self.state.peak_value,
                    portfolio.total_value_usd
                )

            # Load open positions
            open_trades = db.get_open_trades(is_paper=self.is_paper)
            for trade in open_trades:
                position = Position(
                    symbol=trade.symbol,
                    trade_id=trade.id,
                    trade_type=trade.trade_type,
                    quantity=trade.quantity,
                    entry_price=trade.entry_price,
                    current_price=trade.entry_price,  # Will be updated
                    stop_loss=trade.stop_loss,
                    take_profit=trade.take_profit
                )
                self.state.positions.append(position)

            logger.info(
                f"Loaded portfolio: ${self.state.total_value:.2f}, "
                f"{len(self.state.positions)} positions"
            )

        except Exception as e:
            logger.error(f"Error loading portfolio state: {e}")

    def update_prices(self, prices: Dict[str, float]):
        """
        Update position prices.

        Args:
            prices: Dictionary mapping symbol to current price
        """
        for position in self.state.positions:
            if position.symbol in prices:
                position.current_price = prices[position.symbol]

        # Update peak value
        if self.state.total_value > self.state.peak_value:
            self.state.peak_value = self.state.total_value

        self.state.timestamp = datetime.utcnow()

    def open_position(
        self,
        trade: Trade,
        current_price: Optional[float] = None
    ) -> bool:
        """
        Open a new position.

        Args:
            trade: Trade object
            current_price: Current market price

        Returns:
            True if position was opened
        """
        # Check if we have enough cash
        cost = trade.position_size_usd
        if cost > self.state.cash_balance:
            logger.warning(
                f"Insufficient cash for {trade.symbol}: "
                f"need ${cost:.2f}, have ${self.state.cash_balance:.2f}"
            )
            return False

        # Deduct cash
        self.state.cash_balance -= cost

        # Add position
        position = Position(
            symbol=trade.symbol,
            trade_id=trade.id,
            trade_type=trade.trade_type,
            quantity=trade.quantity,
            entry_price=trade.entry_price,
            current_price=current_price or trade.entry_price,
            stop_loss=trade.stop_loss,
            take_profit=trade.take_profit
        )
        self.state.positions.append(position)

        logger.info(
            f"Opened position: {trade.symbol} {trade.trade_type.value} "
            f"{trade.quantity:.6f} @ ${trade.entry_price:.2f}"
        )

        return True

    def close_position(
        self,
        symbol: str,
        exit_price: float,
        fees: float = 0.0
    ) -> Optional[float]:
        """
        Close a position and return realized P&L.

        Args:
            symbol: Coin symbol
            exit_price: Exit price
            fees: Trading fees

        Returns:
            Realized P&L or None if no position found
        """
        position = self.state.get_position(symbol)
        if not position:
            logger.warning(f"No position found for {symbol}")
            return None

        # Calculate P&L
        if position.trade_type == TradeType.BUY:
            pnl = (exit_price - position.entry_price) * position.quantity
        else:
            pnl = (position.entry_price - exit_price) * position.quantity

        pnl -= fees

        # Add proceeds to cash
        proceeds = position.quantity * exit_price - fees
        self.state.cash_balance += proceeds

        # Remove position
        self.state.positions = [
            p for p in self.state.positions if p.symbol != symbol
        ]

        logger.info(
            f"Closed position: {symbol} @ ${exit_price:.2f}, "
            f"P&L: ${pnl:.2f}"
        )

        return pnl

    def get_available_cash(self) -> float:
        """Get available cash for trading."""
        return self.state.cash_balance

    def get_buying_power(
        self,
        max_position_pct: Optional[float] = None
    ) -> float:
        """
        Get maximum amount available for a single trade.

        Args:
            max_position_pct: Maximum position size as percentage

        Returns:
            Maximum trade size in USD
        """
        max_pct = max_position_pct or settings.trading.max_position_size_pct
        max_by_pct = self.state.total_value * max_pct

        return min(self.state.cash_balance, max_by_pct)

    def save_snapshot(self):
        """Save current portfolio state to database."""
        try:
            portfolio = Portfolio(
                timestamp=datetime.utcnow(),
                total_value_usd=self.state.total_value,
                cash_balance_usd=self.state.cash_balance,
                positions_value_usd=self.state.positions_value,
                daily_pnl=0.0,  # Would need to track daily
                daily_pnl_pct=0.0,
                total_pnl=self.state.total_pnl,
                total_pnl_pct=self.state.total_pnl_pct,
                open_positions_count=self.state.open_positions_count,
                max_drawdown=self.state.max_drawdown,
                is_paper=self.is_paper
            )

            db.save_portfolio_snapshot(portfolio)
            logger.debug(f"Saved portfolio snapshot: ${self.state.total_value:.2f}")

        except Exception as e:
            logger.error(f"Error saving portfolio snapshot: {e}")

    def get_summary(self) -> Dict:
        """Get portfolio summary."""
        return {
            "total_value": self.state.total_value,
            "cash_balance": self.state.cash_balance,
            "positions_value": self.state.positions_value,
            "total_pnl": self.state.total_pnl,
            "total_pnl_pct": self.state.total_pnl_pct,
            "unrealized_pnl": self.state.unrealized_pnl,
            "max_drawdown": self.state.max_drawdown,
            "open_positions": self.state.open_positions_count,
            "is_paper": self.is_paper,
            "positions": [
                {
                    "symbol": p.symbol,
                    "quantity": p.quantity,
                    "entry_price": p.entry_price,
                    "current_price": p.current_price,
                    "value": p.value,
                    "unrealized_pnl": p.unrealized_pnl,
                    "unrealized_pnl_pct": p.unrealized_pnl_pct
                }
                for p in self.state.positions
            ]
        }


# Global instance for paper trading
portfolio_tracker = PortfolioTracker(is_paper=True)
