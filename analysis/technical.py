"""Technical analysis indicators module using pandas-ta."""

import logging
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import pandas as pd
import pandas_ta as ta

from ..config import settings
from ..storage import db

logger = logging.getLogger(__name__)


@dataclass
class TechnicalSignals:
    """Container for technical analysis signals."""
    rsi: Optional[float] = None
    rsi_signal: str = "neutral"  # oversold, overbought, neutral

    macd: Optional[float] = None
    macd_signal_line: Optional[float] = None
    macd_histogram: Optional[float] = None
    macd_signal: str = "neutral"  # bullish_crossover, bearish_crossover, neutral

    bb_upper: Optional[float] = None
    bb_middle: Optional[float] = None
    bb_lower: Optional[float] = None
    bb_signal: str = "neutral"  # oversold, overbought, neutral

    ema_20: Optional[float] = None
    ema_50: Optional[float] = None
    ema_200: Optional[float] = None
    ema_signal: str = "neutral"  # bullish, bearish, neutral

    atr: Optional[float] = None
    volume_ratio: Optional[float] = None

    current_price: Optional[float] = None
    overall_score: float = 0.0  # -1 to 1 scale


class TechnicalAnalyzer:
    """Performs technical analysis on market data."""

    def __init__(self):
        """Initialize the technical analyzer."""
        self.config = settings.trading

    def calculate_indicators(self, df: pd.DataFrame) -> Dict[str, float]:
        """
        Calculate all technical indicators for a DataFrame.

        Args:
            df: DataFrame with OHLCV data (columns: open, high, low, close, volume)

        Returns:
            Dictionary of indicator values
        """
        if df.empty or len(df) < 200:
            logger.warning("Insufficient data for technical analysis")
            return {}

        indicators = {}

        try:
            # RSI
            rsi = ta.rsi(df["close"], length=self.config.rsi_period)
            if rsi is not None and len(rsi) > 0:
                indicators["rsi"] = rsi.iloc[-1]

            # MACD
            macd = ta.macd(
                df["close"],
                fast=self.config.macd_fast,
                slow=self.config.macd_slow,
                signal=self.config.macd_signal
            )
            if macd is not None and not macd.empty:
                indicators["macd"] = macd.iloc[-1, 0]  # MACD line
                indicators["macd_signal"] = macd.iloc[-1, 2]  # Signal line
                indicators["macd_histogram"] = macd.iloc[-1, 1]  # Histogram

            # Bollinger Bands
            bb = ta.bbands(
                df["close"],
                length=self.config.bb_period,
                std=self.config.bb_std
            )
            if bb is not None and not bb.empty:
                indicators["bb_lower"] = bb.iloc[-1, 0]
                indicators["bb_middle"] = bb.iloc[-1, 1]
                indicators["bb_upper"] = bb.iloc[-1, 2]

            # EMAs
            ema_20 = ta.ema(df["close"], length=self.config.ema_short)
            ema_50 = ta.ema(df["close"], length=self.config.ema_medium)
            ema_200 = ta.ema(df["close"], length=self.config.ema_long)

            if ema_20 is not None and len(ema_20) > 0:
                indicators["ema_20"] = ema_20.iloc[-1]
            if ema_50 is not None and len(ema_50) > 0:
                indicators["ema_50"] = ema_50.iloc[-1]
            if ema_200 is not None and len(ema_200) > 0:
                indicators["ema_200"] = ema_200.iloc[-1]

            # ATR
            atr = ta.atr(
                df["high"],
                df["low"],
                df["close"],
                length=self.config.atr_period
            )
            if atr is not None and len(atr) > 0:
                indicators["atr"] = atr.iloc[-1]

            # Volume analysis
            volume_sma = ta.sma(df["volume"], length=20)
            if volume_sma is not None and len(volume_sma) > 0:
                indicators["volume_sma"] = volume_sma.iloc[-1]
                current_vol = df["volume"].iloc[-1]
                if volume_sma.iloc[-1] > 0:
                    indicators["volume_ratio"] = current_vol / volume_sma.iloc[-1]

        except Exception as e:
            logger.error(f"Error calculating indicators: {e}")

        return indicators

    def analyze(self, df: pd.DataFrame) -> TechnicalSignals:
        """
        Perform complete technical analysis.

        Args:
            df: DataFrame with OHLCV data

        Returns:
            TechnicalSignals with all analysis results
        """
        signals = TechnicalSignals()

        if df.empty:
            return signals

        indicators = self.calculate_indicators(df)
        current_price = df["close"].iloc[-1]
        signals.current_price = current_price

        # Populate signal values
        signals.rsi = indicators.get("rsi")
        signals.macd = indicators.get("macd")
        signals.macd_signal_line = indicators.get("macd_signal")
        signals.macd_histogram = indicators.get("macd_histogram")
        signals.bb_upper = indicators.get("bb_upper")
        signals.bb_middle = indicators.get("bb_middle")
        signals.bb_lower = indicators.get("bb_lower")
        signals.ema_20 = indicators.get("ema_20")
        signals.ema_50 = indicators.get("ema_50")
        signals.ema_200 = indicators.get("ema_200")
        signals.atr = indicators.get("atr")
        signals.volume_ratio = indicators.get("volume_ratio")

        # Analyze RSI
        if signals.rsi is not None:
            if signals.rsi < self.config.rsi_oversold:
                signals.rsi_signal = "oversold"
            elif signals.rsi > self.config.rsi_overbought:
                signals.rsi_signal = "overbought"

        # Analyze MACD
        if signals.macd is not None and signals.macd_signal_line is not None:
            # Check for crossover (current vs previous)
            if len(df) >= 2:
                prev_macd = self._get_previous_macd(df)
                if prev_macd:
                    prev_diff = prev_macd[0] - prev_macd[1]
                    curr_diff = signals.macd - signals.macd_signal_line

                    if prev_diff <= 0 and curr_diff > 0:
                        signals.macd_signal = "bullish_crossover"
                    elif prev_diff >= 0 and curr_diff < 0:
                        signals.macd_signal = "bearish_crossover"

        # Analyze Bollinger Bands
        if all(v is not None for v in [signals.bb_lower, signals.bb_upper]):
            if current_price <= signals.bb_lower:
                signals.bb_signal = "oversold"
            elif current_price >= signals.bb_upper:
                signals.bb_signal = "overbought"

        # Analyze EMA crossovers
        if all(v is not None for v in [signals.ema_20, signals.ema_50]):
            if signals.ema_20 > signals.ema_50:
                signals.ema_signal = "bullish"
            elif signals.ema_20 < signals.ema_50:
                signals.ema_signal = "bearish"

        # Calculate overall score (-1 to 1)
        signals.overall_score = self._calculate_overall_score(signals)

        return signals

    def _get_previous_macd(self, df: pd.DataFrame) -> Optional[Tuple[float, float]]:
        """Get previous MACD values for crossover detection."""
        try:
            macd = ta.macd(
                df["close"],
                fast=self.config.macd_fast,
                slow=self.config.macd_slow,
                signal=self.config.macd_signal
            )
            if macd is not None and len(macd) >= 2:
                return (macd.iloc[-2, 0], macd.iloc[-2, 2])
        except Exception:
            pass
        return None

    def _calculate_overall_score(self, signals: TechnicalSignals) -> float:
        """
        Calculate overall technical score from individual signals.

        Returns:
            Score from -1 (strong sell) to 1 (strong buy)
        """
        scores = []
        weights = []

        # RSI score (weight: 0.25)
        if signals.rsi is not None:
            if signals.rsi_signal == "oversold":
                scores.append(0.8)  # Bullish
            elif signals.rsi_signal == "overbought":
                scores.append(-0.8)  # Bearish
            else:
                # Scale RSI to score
                rsi_score = (50 - signals.rsi) / 50  # 30 RSI -> 0.4, 70 RSI -> -0.4
                scores.append(max(-1, min(1, rsi_score)))
            weights.append(0.25)

        # MACD score (weight: 0.25)
        if signals.macd_signal == "bullish_crossover":
            scores.append(1.0)
            weights.append(0.25)
        elif signals.macd_signal == "bearish_crossover":
            scores.append(-1.0)
            weights.append(0.25)
        elif signals.macd_histogram is not None:
            # Use histogram direction
            hist_score = max(-1, min(1, signals.macd_histogram / 100))
            scores.append(hist_score)
            weights.append(0.15)  # Lower weight for non-crossover

        # Bollinger Bands score (weight: 0.20)
        if signals.bb_signal == "oversold":
            scores.append(0.7)  # Bullish bounce expected
            weights.append(0.20)
        elif signals.bb_signal == "overbought":
            scores.append(-0.7)  # Bearish reversal expected
            weights.append(0.20)

        # EMA score (weight: 0.30)
        if signals.ema_signal == "bullish":
            scores.append(0.6)
            weights.append(0.30)
        elif signals.ema_signal == "bearish":
            scores.append(-0.6)
            weights.append(0.30)

        # Calculate weighted average
        if not scores:
            return 0.0

        total_weight = sum(weights)
        if total_weight == 0:
            return 0.0

        return sum(s * w for s, w in zip(scores, weights)) / total_weight

    def analyze_and_store(self, symbol: str, df: pd.DataFrame) -> TechnicalSignals:
        """
        Analyze market data and store indicators in database.

        Args:
            symbol: Coin symbol
            df: DataFrame with OHLCV data

        Returns:
            TechnicalSignals
        """
        signals = self.analyze(df)
        indicators = self.calculate_indicators(df)

        if indicators:
            try:
                db.save_technical_indicators(symbol, indicators)
                logger.info(f"Stored technical indicators for {symbol}")
            except Exception as e:
                logger.error(f"Error storing indicators for {symbol}: {e}")

        return signals

    def get_stop_loss_price(
        self,
        entry_price: float,
        atr: float,
        trade_type: str = "buy"
    ) -> float:
        """
        Calculate stop-loss price based on ATR.

        Args:
            entry_price: Entry price
            atr: Average True Range
            trade_type: "buy" or "sell"

        Returns:
            Stop-loss price
        """
        stop_distance = atr * self.config.stop_loss_atr_multiplier

        if trade_type == "buy":
            return entry_price - stop_distance
        else:
            return entry_price + stop_distance

    def get_take_profit_price(
        self,
        entry_price: float,
        stop_loss: float,
        trade_type: str = "buy"
    ) -> float:
        """
        Calculate take-profit price based on risk/reward ratio.

        Args:
            entry_price: Entry price
            stop_loss: Stop-loss price
            trade_type: "buy" or "sell"

        Returns:
            Take-profit price
        """
        risk = abs(entry_price - stop_loss)
        reward = risk * self.config.take_profit_ratio

        if trade_type == "buy":
            return entry_price + reward
        else:
            return entry_price - reward


# Global instance
technical_analyzer = TechnicalAnalyzer()
