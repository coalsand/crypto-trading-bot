"""Signal generation module combining technical and sentiment analysis."""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

from ..config import settings, TRADEABLE_COINS
from ..analysis import TechnicalAnalyzer, TechnicalSignals, SentimentAnalyzer, AggregateSentiment
from ..storage import db, Signal, SignalType

logger = logging.getLogger(__name__)


@dataclass
class TradingSignal:
    """Complete trading signal with all analysis details."""
    symbol: str
    signal_type: SignalType = SignalType.HOLD
    timestamp: datetime = field(default_factory=datetime.utcnow)
    asset_type: str = "crypto"  # "crypto" | "stock"

    # Scores
    technical_score: float = 0.0
    sentiment_score: float = 0.0
    combined_score: float = 0.0

    # Signal quality
    strength: float = 0.0  # 0-1 scale
    confidence: float = 0.0  # 0-1 scale

    # Trade parameters
    current_price: float = 0.0
    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    position_size_pct: float = 0.0

    # Analysis details
    technical_signals: Optional[TechnicalSignals] = None
    sentiment_data: Optional[AggregateSentiment] = None
    reasoning: Dict = field(default_factory=dict)

    def to_db_signal(self) -> Signal:
        """Convert to database Signal model."""
        return Signal(
            symbol=self.symbol,
            timestamp=self.timestamp,
            signal_type=self.signal_type,
            technical_score=self.technical_score,
            sentiment_score=self.sentiment_score,
            combined_score=self.combined_score,
            strength=self.strength,
            confidence=self.confidence,
            entry_price=self.entry_price,
            stop_loss=self.stop_loss,
            take_profit=self.take_profit,
            position_size_pct=self.position_size_pct,
            reasoning=json.dumps(self.reasoning),
            asset_type=self.asset_type,
        )


class SignalGenerator:
    """Generates trading signals by combining technical and sentiment analysis."""

    def __init__(
        self,
        technical_analyzer: Optional[TechnicalAnalyzer] = None,
        sentiment_analyzer: Optional[SentimentAnalyzer] = None
    ):
        """Initialize the signal generator."""
        from ..analysis import technical_analyzer as default_ta
        from ..analysis import sentiment_analyzer as default_sa

        self.technical_analyzer = technical_analyzer or default_ta
        self.sentiment_analyzer = sentiment_analyzer or default_sa
        self.config = settings.trading

    def generate_signal(
        self,
        symbol: str,
        market_data: pd.DataFrame,
        sentiment_hours: int = 24,
        asset_type: str = "crypto",
    ) -> TradingSignal:
        """
        Generate a trading signal for a symbol.

        Args:
            symbol: Trading symbol (coin or stock ticker)
            market_data: DataFrame with OHLCV data
            sentiment_hours: Hours of sentiment data to consider
            asset_type: "crypto" or "stock" — stocks skip sentiment analysis

        Returns:
            TradingSignal with analysis and recommendations
        """
        signal = TradingSignal(symbol=symbol, asset_type=asset_type)
        reasoning = {}

        # Technical Analysis
        tech_signals = self.technical_analyzer.analyze(market_data)
        signal.technical_signals = tech_signals
        signal.technical_score = tech_signals.overall_score
        signal.current_price = tech_signals.current_price or market_data["close"].iloc[-1]

        reasoning["technical"] = {
            "score": tech_signals.overall_score,
            "rsi": tech_signals.rsi,
            "rsi_signal": tech_signals.rsi_signal,
            "macd_signal": tech_signals.macd_signal,
            "bb_signal": tech_signals.bb_signal,
            "ema_signal": tech_signals.ema_signal
        }

        # Sentiment Analysis — crypto only; stocks skip this for MVP
        if asset_type == "crypto":
            sentiment = self.sentiment_analyzer.get_aggregated_sentiment(symbol, sentiment_hours)
            signal.sentiment_data = sentiment
            signal.sentiment_score = sentiment.overall_score

            reasoning["sentiment"] = {
                "overall": sentiment.overall_score,
                "reddit": sentiment.reddit_score,
                "twitter": sentiment.twitter_score,
                "news": sentiment.news_score,
                "source_count": sentiment.source_count,
                "post_count": sentiment.post_count
            }
        else:
            signal.sentiment_score = 0.0
            reasoning["sentiment"] = {"skipped": "no sentiment sources for stocks (MVP)"}

        # Combined Score — for stocks, sentiment weight folds into technical
        if asset_type == "crypto":
            signal.combined_score = (
                signal.technical_score * self.config.technical_weight +
                signal.sentiment_score * self.config.sentiment_weight
            )
        else:
            signal.combined_score = signal.technical_score

        # Determine signal type
        signal.signal_type = self._determine_signal_type(signal)
        reasoning["decision"] = {
            "combined_score": signal.combined_score,
            "signal_type": signal.signal_type.value,
            "buy_threshold": self.config.buy_signal_threshold,
            "sell_threshold": self.config.sell_signal_threshold
        }

        # Calculate signal strength and confidence
        signal.strength = abs(signal.combined_score)
        signal.confidence = self._calculate_confidence(signal)

        # Calculate trade parameters for actionable signals
        if signal.signal_type != SignalType.HOLD:
            self._set_trade_parameters(signal, tech_signals)

        signal.reasoning = reasoning

        logger.info(
            f"Generated signal for {symbol}: {signal.signal_type.value} "
            f"(combined={signal.combined_score:.3f}, tech={signal.technical_score:.3f}, "
            f"sentiment={signal.sentiment_score:.3f})"
        )

        return signal

    def _determine_signal_type(self, signal: TradingSignal) -> SignalType:
        """Determine signal type based on combined score."""
        # Check for strong buy conditions
        if signal.combined_score >= self.config.buy_signal_threshold:
            # Additional confirmation checks
            tech = signal.technical_signals
            sentiment = signal.sentiment_data

            buy_confirmations = 0

            # RSI oversold
            if tech and tech.rsi_signal == "oversold":
                buy_confirmations += 1

            # MACD bullish crossover
            if tech and tech.macd_signal == "bullish_crossover":
                buy_confirmations += 1

            # EMA bullish
            if tech and tech.ema_signal == "bullish":
                buy_confirmations += 1

            # Positive sentiment
            if sentiment and sentiment.overall_score >= self.config.sentiment_threshold:
                buy_confirmations += 1

            # Need at least min_confirmations
            min_conf = getattr(self.config, 'min_confirmations', 2)
            if buy_confirmations >= min_conf:
                return SignalType.BUY

        # Check for strong sell conditions
        elif signal.combined_score <= self.config.sell_signal_threshold:
            tech = signal.technical_signals
            sentiment = signal.sentiment_data

            sell_confirmations = 0

            # RSI overbought
            if tech and tech.rsi_signal == "overbought":
                sell_confirmations += 1

            # MACD bearish crossover
            if tech and tech.macd_signal == "bearish_crossover":
                sell_confirmations += 1

            # EMA bearish
            if tech and tech.ema_signal == "bearish":
                sell_confirmations += 1

            # Negative sentiment
            if sentiment and sentiment.overall_score <= -self.config.sentiment_threshold:
                sell_confirmations += 1

            # Need at least min_confirmations
            min_conf = getattr(self.config, 'min_confirmations', 2)
            if sell_confirmations >= min_conf:
                return SignalType.SELL

        return SignalType.HOLD

    def _calculate_confidence(self, signal: TradingSignal) -> float:
        """Calculate confidence level for the signal."""
        confidence_factors = []

        # Technical analysis confidence
        if signal.technical_signals:
            tech = signal.technical_signals

            # Multiple confirming signals increase confidence
            confirmations = 0
            if tech.rsi_signal != "neutral":
                confirmations += 1
            if tech.macd_signal != "neutral":
                confirmations += 1
            if tech.bb_signal != "neutral":
                confirmations += 1
            if tech.ema_signal != "neutral":
                confirmations += 1

            confidence_factors.append(min(confirmations / 4, 1.0))

        # Sentiment confidence
        if signal.sentiment_data:
            # More data sources = higher confidence
            source_confidence = min(signal.sentiment_data.source_count / 3, 1.0)
            confidence_factors.append(source_confidence)

            # More posts = higher confidence
            post_confidence = min(signal.sentiment_data.post_count / 100, 1.0)
            confidence_factors.append(post_confidence)

        # Signal strength as confidence factor
        confidence_factors.append(signal.strength)

        if not confidence_factors:
            return 0.0

        return sum(confidence_factors) / len(confidence_factors)

    def _set_trade_parameters(
        self,
        signal: TradingSignal,
        tech_signals: TechnicalSignals
    ):
        """Set trade parameters (entry, stop-loss, take-profit)."""
        signal.entry_price = signal.current_price

        # Calculate ATR-based stop-loss
        atr = tech_signals.atr if tech_signals else None

        if atr:
            trade_type = "buy" if signal.signal_type == SignalType.BUY else "sell"

            signal.stop_loss = self.technical_analyzer.get_stop_loss_price(
                signal.entry_price,
                atr,
                trade_type
            )

            signal.take_profit = self.technical_analyzer.get_take_profit_price(
                signal.entry_price,
                signal.stop_loss,
                trade_type
            )
        else:
            # Default to percentage-based stops
            if signal.signal_type == SignalType.BUY:
                signal.stop_loss = signal.entry_price * 0.95  # 5% stop
                signal.take_profit = signal.entry_price * 1.15  # 15% target
            else:
                signal.stop_loss = signal.entry_price * 1.05
                signal.take_profit = signal.entry_price * 0.85

        # Position sizing based on signal strength and confidence
        base_size = self.config.min_position_size_pct
        size_range = self.config.max_position_size_pct - base_size
        signal.position_size_pct = base_size + (size_range * signal.confidence)

    def generate_all_signals(
        self,
        market_data_dict: Dict[str, pd.DataFrame],
        sentiment_hours: int = 24,
        symbols: Optional[List[str]] = None,
        asset_type: str = "crypto",
    ) -> List[TradingSignal]:
        """
        Generate signals for the given symbols (defaults to TRADEABLE_COINS).

        Args:
            market_data_dict: Dictionary mapping symbol to DataFrame
            sentiment_hours: Hours of sentiment data to consider
            symbols: Explicit symbol list (e.g. stock tickers from the screener).
                     When None, uses TRADEABLE_COINS (crypto).
            asset_type: "crypto" or "stock"

        Returns:
            List of TradingSignals
        """
        signals = []
        target_symbols = symbols if symbols is not None else list(TRADEABLE_COINS)

        for symbol in target_symbols:
            if symbol not in market_data_dict:
                logger.warning(f"No market data for {symbol}")
                continue

            try:
                signal = self.generate_signal(
                    symbol,
                    market_data_dict[symbol],
                    sentiment_hours,
                    asset_type=asset_type,
                )
                signals.append(signal)
            except Exception as e:
                logger.error(f"Error generating signal for {symbol}: {e}")

        return signals

    def save_signals(self, signals: List[TradingSignal]) -> List[int]:
        """
        Save signals to database.

        Args:
            signals: List of TradingSignals

        Returns:
            List of saved signal IDs
        """
        signal_ids = []

        for signal in signals:
            try:
                db_signal = signal.to_db_signal()
                signal_id = db.save_signal(db_signal)
                signal_ids.append(signal_id)
                logger.debug(f"Saved signal {signal_id} for {signal.symbol}")
            except Exception as e:
                logger.error(f"Error saving signal for {signal.symbol}: {e}")

        return signal_ids

    def get_actionable_signals(
        self,
        signals: List[TradingSignal]
    ) -> List[TradingSignal]:
        """
        Filter signals to only actionable (BUY/SELL) signals.

        Args:
            signals: List of all signals

        Returns:
            List of actionable signals
        """
        return [s for s in signals if s.signal_type != SignalType.HOLD]


# Global instance
signal_generator = SignalGenerator()
