"""Database connection and query utilities."""

from datetime import datetime, timedelta
from typing import List, Optional
from contextlib import contextmanager

from sqlalchemy import create_engine, desc, inspect, text
from sqlalchemy.orm import sessionmaker, Session

from .models import (
    Base, MarketData, SentimentData, TechnicalIndicator,
    Signal, Trade, Portfolio, PerformanceMetrics,
    TradeStatus, TradeType, SignalType, SentimentSource
)
from ..config import settings


class Database:
    """Database manager for the trading bot."""

    def __init__(self, database_url: Optional[str] = None):
        """Initialize database connection."""
        self.database_url = database_url or settings.database.database_url
        self.engine = create_engine(self.database_url, echo=False)
        self.SessionLocal = sessionmaker(bind=self.engine, expire_on_commit=False)

    def create_tables(self):
        """Create all tables in the database, then apply lightweight migrations."""
        Base.metadata.create_all(self.engine)
        self._migrate_asset_type()

    def _migrate_asset_type(self):
        """Add the asset_type column to trades and signals tables if missing."""
        inspector = inspect(self.engine)
        for table_name in ("trades", "signals"):
            if table_name not in inspector.get_table_names():
                continue
            cols = {c["name"] for c in inspector.get_columns(table_name)}
            if "asset_type" not in cols:
                with self.engine.begin() as conn:
                    conn.execute(text(
                        f"ALTER TABLE {table_name} "
                        f"ADD COLUMN asset_type VARCHAR(10) NOT NULL DEFAULT 'crypto'"
                    ))

    def drop_tables(self):
        """Drop all tables (use with caution)."""
        Base.metadata.drop_all(self.engine)

    @contextmanager
    def get_session(self) -> Session:
        """Get a database session with automatic cleanup."""
        session = self.SessionLocal()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # Market Data Operations
    def save_market_data(self, data: List[dict], symbol: str, timeframe: str = "1h"):
        """Save OHLCV market data."""
        with self.get_session() as session:
            for candle in data:
                market_data = MarketData(
                    symbol=symbol,
                    timestamp=candle["timestamp"],
                    open=candle["open"],
                    high=candle["high"],
                    low=candle["low"],
                    close=candle["close"],
                    volume=candle["volume"],
                    timeframe=timeframe
                )
                session.merge(market_data)

    def get_market_data(
        self,
        symbol: str,
        timeframe: str = "1h",
        limit: int = 500,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None
    ) -> List[MarketData]:
        """Get market data for a symbol."""
        with self.get_session() as session:
            query = session.query(MarketData).filter(
                MarketData.symbol == symbol,
                MarketData.timeframe == timeframe
            )

            if start_time:
                query = query.filter(MarketData.timestamp >= start_time)
            if end_time:
                query = query.filter(MarketData.timestamp <= end_time)

            return query.order_by(desc(MarketData.timestamp)).limit(limit).all()

    # Sentiment Data Operations
    def save_sentiment_data(
        self,
        symbol: str,
        source: SentimentSource,
        score: float,
        magnitude: float = 0.0,
        text_sample: Optional[str] = None,
        post_count: int = 1,
        metadata: Optional[str] = None
    ):
        """Save sentiment data."""
        with self.get_session() as session:
            sentiment = SentimentData(
                symbol=symbol,
                source=source,
                timestamp=datetime.utcnow(),
                score=score,
                magnitude=magnitude,
                text_sample=text_sample,
                post_count=post_count,
                extra_data=metadata
            )
            session.add(sentiment)

    def get_recent_sentiment(
        self,
        symbol: str,
        hours: int = 24,
        source: Optional[SentimentSource] = None
    ) -> List[SentimentData]:
        """Get recent sentiment data for a symbol."""
        with self.get_session() as session:
            since = datetime.utcnow() - timedelta(hours=hours)
            query = session.query(SentimentData).filter(
                SentimentData.symbol == symbol,
                SentimentData.timestamp >= since
            )

            if source:
                query = query.filter(SentimentData.source == source)

            return query.order_by(desc(SentimentData.timestamp)).all()

    def get_aggregated_sentiment(
        self,
        symbol: str,
        hours: int = 24
    ) -> dict:
        """Get aggregated sentiment across all sources."""
        sentiments = self.get_recent_sentiment(symbol, hours)

        if not sentiments:
            return {"score": 0.0, "count": 0, "sources": {}}

        total_score = 0.0
        total_weight = 0.0
        sources = {}

        for s in sentiments:
            weight = s.magnitude if s.magnitude > 0 else 1.0
            total_score += s.score * weight
            total_weight += weight

            source_name = s.source.value
            if source_name not in sources:
                sources[source_name] = {"score": 0.0, "count": 0}
            sources[source_name]["score"] += s.score
            sources[source_name]["count"] += 1

        # Calculate averages
        for source in sources:
            sources[source]["score"] /= sources[source]["count"]

        return {
            "score": total_score / total_weight if total_weight > 0 else 0.0,
            "count": len(sentiments),
            "sources": sources
        }

    # Technical Indicator Operations
    def save_technical_indicators(self, symbol: str, indicators: dict):
        """Save technical indicators."""
        with self.get_session() as session:
            tech = TechnicalIndicator(
                symbol=symbol,
                timestamp=datetime.utcnow(),
                **indicators
            )
            session.add(tech)

    def get_latest_indicators(self, symbol: str) -> Optional[TechnicalIndicator]:
        """Get the latest technical indicators for a symbol."""
        with self.get_session() as session:
            return session.query(TechnicalIndicator).filter(
                TechnicalIndicator.symbol == symbol
            ).order_by(desc(TechnicalIndicator.timestamp)).first()

    # Signal Operations
    def save_signal(self, signal: Signal) -> int:
        """Save a trading signal and return its ID."""
        with self.get_session() as session:
            session.add(signal)
            session.flush()
            return signal.id

    def get_unexecuted_signals(
        self,
        symbol: Optional[str] = None
    ) -> List[Signal]:
        """Get signals that haven't been executed."""
        with self.get_session() as session:
            query = session.query(Signal).filter(Signal.executed == False)

            if symbol:
                query = query.filter(Signal.symbol == symbol)

            return query.order_by(desc(Signal.timestamp)).all()

    def mark_signal_executed(self, signal_id: int, trade_id: int):
        """Mark a signal as executed."""
        with self.get_session() as session:
            signal = session.query(Signal).get(signal_id)
            if signal:
                signal.executed = True
                signal.trade_id = trade_id

    # Trade Operations
    def create_trade(self, trade: Trade) -> int:
        """Create a new trade and return its ID."""
        with self.get_session() as session:
            session.add(trade)
            session.flush()
            return trade.id

    def update_trade(self, trade_id: int, **kwargs):
        """Update a trade."""
        with self.get_session() as session:
            trade = session.query(Trade).get(trade_id)
            if trade:
                for key, value in kwargs.items():
                    setattr(trade, key, value)

    def get_open_trades(
        self,
        symbol: Optional[str] = None,
        is_paper: bool = True
    ) -> List[Trade]:
        """Get all open trades."""
        with self.get_session() as session:
            query = session.query(Trade).filter(
                Trade.status == TradeStatus.OPEN,
                Trade.is_paper == is_paper
            )

            if symbol:
                query = query.filter(Trade.symbol == symbol)

            return query.all()

    def get_trade_by_id(self, trade_id: int) -> Optional[Trade]:
        """Get a trade by ID."""
        with self.get_session() as session:
            return session.query(Trade).get(trade_id)

    def get_recent_trades(
        self,
        limit: int = 50,
        is_paper: bool = True
    ) -> List[Trade]:
        """Get recent trades."""
        with self.get_session() as session:
            return session.query(Trade).filter(
                Trade.is_paper == is_paper
            ).order_by(desc(Trade.created_at)).limit(limit).all()

    def get_trades_for_period(
        self,
        start_time: datetime,
        end_time: datetime,
        is_paper: bool = True
    ) -> List[Trade]:
        """Get trades within a time period."""
        with self.get_session() as session:
            return session.query(Trade).filter(
                Trade.created_at >= start_time,
                Trade.created_at <= end_time,
                Trade.is_paper == is_paper
            ).order_by(Trade.created_at).all()

    # Portfolio Operations
    def save_portfolio_snapshot(self, portfolio: Portfolio):
        """Save a portfolio snapshot."""
        with self.get_session() as session:
            session.add(portfolio)

    def get_latest_portfolio(self, is_paper: bool = True) -> Optional[Portfolio]:
        """Get the latest portfolio state."""
        with self.get_session() as session:
            return session.query(Portfolio).filter(
                Portfolio.is_paper == is_paper
            ).order_by(desc(Portfolio.timestamp)).first()

    def get_portfolio_history(
        self,
        days: int = 30,
        is_paper: bool = True
    ) -> List[Portfolio]:
        """Get portfolio history."""
        with self.get_session() as session:
            since = datetime.utcnow() - timedelta(days=days)
            return session.query(Portfolio).filter(
                Portfolio.timestamp >= since,
                Portfolio.is_paper == is_paper
            ).order_by(Portfolio.timestamp).all()

    # Performance Metrics Operations
    def save_performance_metrics(self, metrics: PerformanceMetrics):
        """Save performance metrics."""
        with self.get_session() as session:
            session.add(metrics)

    def get_latest_metrics(
        self,
        period: str = "daily",
        is_paper: bool = True
    ) -> Optional[PerformanceMetrics]:
        """Get the latest performance metrics."""
        with self.get_session() as session:
            return session.query(PerformanceMetrics).filter(
                PerformanceMetrics.period == period,
                PerformanceMetrics.is_paper == is_paper
            ).order_by(desc(PerformanceMetrics.timestamp)).first()

    def calculate_performance_metrics(
        self,
        trades: List[Trade]
    ) -> dict:
        """Calculate performance metrics from trades."""
        if not trades:
            return {
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "win_rate": 0.0,
                "total_pnl": 0.0,
                "average_win": 0.0,
                "average_loss": 0.0,
                "profit_factor": 0.0
            }

        closed_trades = [t for t in trades if t.status == TradeStatus.CLOSED]
        winning = [t for t in closed_trades if (t.realized_pnl or 0) > 0]
        losing = [t for t in closed_trades if (t.realized_pnl or 0) < 0]

        total_wins = sum(t.realized_pnl for t in winning) if winning else 0
        total_losses = abs(sum(t.realized_pnl for t in losing)) if losing else 0

        return {
            "total_trades": len(closed_trades),
            "winning_trades": len(winning),
            "losing_trades": len(losing),
            "win_rate": len(winning) / len(closed_trades) if closed_trades else 0.0,
            "total_pnl": sum(t.realized_pnl or 0 for t in closed_trades),
            "average_win": total_wins / len(winning) if winning else 0.0,
            "average_loss": total_losses / len(losing) if losing else 0.0,
            "profit_factor": total_wins / total_losses if total_losses > 0 else float('inf')
        }


# Global database instance
db = Database()
