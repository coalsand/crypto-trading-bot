"""Sentiment analysis module using FinBERT for financial text."""

import logging
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer, pipeline

from ..config import settings
from ..storage import db, SentimentSource

logger = logging.getLogger(__name__)


@dataclass
class SentimentResult:
    """Container for sentiment analysis results."""
    score: float  # -1 (negative) to 1 (positive)
    label: str  # positive, negative, neutral
    confidence: float  # 0 to 1


@dataclass
class AggregateSentiment:
    """Aggregated sentiment across all sources."""
    overall_score: float
    reddit_score: Optional[float] = None
    twitter_score: Optional[float] = None
    news_score: Optional[float] = None
    source_count: int = 0
    post_count: int = 0


class SentimentAnalyzer:
    """Analyzes sentiment of financial text using FinBERT."""

    # FinBERT model for financial sentiment
    MODEL_NAME = "ProsusAI/finbert"

    def __init__(self, use_gpu: bool = True):
        """
        Initialize the sentiment analyzer.

        Args:
            use_gpu: Whether to use GPU if available
        """
        self.model = None
        self.tokenizer = None
        self.pipeline = None
        self.device = "cuda" if use_gpu and torch.cuda.is_available() else "cpu"
        self._initialized = False

    def _ensure_initialized(self):
        """Lazy initialization of the model."""
        if self._initialized:
            return

        try:
            logger.info(f"Loading FinBERT model on {self.device}...")

            self.tokenizer = AutoTokenizer.from_pretrained(self.MODEL_NAME)
            self.model = AutoModelForSequenceClassification.from_pretrained(self.MODEL_NAME)

            if self.device == "cuda":
                self.model = self.model.to(self.device)

            # Create pipeline for easier inference
            self.pipeline = pipeline(
                "sentiment-analysis",
                model=self.model,
                tokenizer=self.tokenizer,
                device=0 if self.device == "cuda" else -1,
                truncation=True,
                max_length=512
            )

            self._initialized = True
            logger.info("FinBERT model loaded successfully")

        except Exception as e:
            logger.error(f"Failed to load FinBERT model: {e}")
            # Fall back to simple rule-based analysis
            self._initialized = True
            self.pipeline = None

    def preprocess_text(self, text: str) -> str:
        """
        Preprocess text for sentiment analysis.

        Args:
            text: Raw text input

        Returns:
            Cleaned text
        """
        # Remove URLs
        text = re.sub(r"https?://\S+|www\.\S+", "", text)

        # Remove mentions and hashtags (keep the text without symbols)
        text = re.sub(r"@\w+", "", text)
        text = re.sub(r"#(\w+)", r"\1", text)

        # Remove excessive whitespace
        text = " ".join(text.split())

        # Remove special characters but keep basic punctuation
        text = re.sub(r"[^\w\s.,!?'-]", "", text)

        return text.strip()

    def analyze(self, text: str) -> SentimentResult:
        """
        Analyze sentiment of a single text.

        Args:
            text: Text to analyze

        Returns:
            SentimentResult with score and label
        """
        self._ensure_initialized()

        text = self.preprocess_text(text)

        if not text:
            return SentimentResult(score=0.0, label="neutral", confidence=0.0)

        try:
            if self.pipeline:
                result = self.pipeline(text[:512])[0]

                # FinBERT labels: positive, negative, neutral
                label = result["label"].lower()
                confidence = result["score"]

                # Convert to score (-1 to 1)
                if label == "positive":
                    score = confidence
                elif label == "negative":
                    score = -confidence
                else:
                    score = 0.0

                return SentimentResult(
                    score=score,
                    label=label,
                    confidence=confidence
                )
            else:
                # Fallback to simple rule-based analysis
                return self._simple_sentiment(text)

        except Exception as e:
            logger.error(f"Error analyzing sentiment: {e}")
            return SentimentResult(score=0.0, label="neutral", confidence=0.0)

    def analyze_batch(self, texts: List[str], batch_size: int = 32) -> List[float]:
        """
        Analyze sentiment for multiple texts.

        Args:
            texts: List of texts to analyze
            batch_size: Batch size for processing

        Returns:
            List of sentiment scores (-1 to 1)
        """
        self._ensure_initialized()

        if not texts:
            return []

        # Preprocess all texts
        processed = [self.preprocess_text(t)[:512] for t in texts]
        processed = [t if t else "neutral" for t in processed]  # Handle empty texts

        scores = []

        try:
            if self.pipeline:
                # Process in batches
                for i in range(0, len(processed), batch_size):
                    batch = processed[i:i + batch_size]
                    results = self.pipeline(batch)

                    for result in results:
                        label = result["label"].lower()
                        confidence = result["score"]

                        if label == "positive":
                            scores.append(confidence)
                        elif label == "negative":
                            scores.append(-confidence)
                        else:
                            scores.append(0.0)
            else:
                # Fallback
                for text in processed:
                    result = self._simple_sentiment(text)
                    scores.append(result.score)

        except Exception as e:
            logger.error(f"Error in batch sentiment analysis: {e}")
            # Return neutral scores on error
            scores = [0.0] * len(texts)

        return scores

    def _simple_sentiment(self, text: str) -> SentimentResult:
        """
        Simple rule-based sentiment analysis as fallback.

        Args:
            text: Text to analyze

        Returns:
            SentimentResult
        """
        text_lower = text.lower()

        # Positive keywords
        positive_words = [
            "bullish", "moon", "pump", "rally", "surge", "soar", "gain",
            "profit", "buy", "long", "breakout", "support", "accumulate",
            "hodl", "growth", "positive", "good", "great", "excellent",
            "strong", "up", "rise", "high", "success", "win", "boost"
        ]

        # Negative keywords
        negative_words = [
            "bearish", "dump", "crash", "fall", "drop", "plunge", "sell",
            "short", "loss", "decline", "fear", "panic", "weak", "down",
            "low", "fail", "scam", "fraud", "bad", "terrible", "warning",
            "risk", "danger", "concern", "worried"
        ]

        pos_count = sum(1 for word in positive_words if word in text_lower)
        neg_count = sum(1 for word in negative_words if word in text_lower)

        total = pos_count + neg_count
        if total == 0:
            return SentimentResult(score=0.0, label="neutral", confidence=0.5)

        score = (pos_count - neg_count) / total

        if score > 0.2:
            label = "positive"
        elif score < -0.2:
            label = "negative"
        else:
            label = "neutral"

        return SentimentResult(
            score=score,
            label=label,
            confidence=min(total / 10, 1.0)
        )

    def get_aggregated_sentiment(
        self,
        symbol: str,
        hours: int = 24
    ) -> AggregateSentiment:
        """
        Get aggregated sentiment for a symbol from all sources.

        Args:
            symbol: Coin symbol
            hours: Hours to look back

        Returns:
            AggregateSentiment with scores from all sources
        """
        sentiment_data = db.get_aggregated_sentiment(symbol, hours)

        result = AggregateSentiment(
            overall_score=sentiment_data.get("score", 0.0),
            source_count=len(sentiment_data.get("sources", {})),
            post_count=sentiment_data.get("count", 0)
        )

        sources = sentiment_data.get("sources", {})

        if "reddit" in sources:
            result.reddit_score = sources["reddit"]["score"]
        if "twitter" in sources:
            result.twitter_score = sources["twitter"]["score"]
        if "news" in sources:
            result.news_score = sources["news"]["score"]

        return result

    def calculate_weighted_sentiment(
        self,
        reddit_score: Optional[float],
        twitter_score: Optional[float],
        news_score: Optional[float],
        weights: Optional[Dict[str, float]] = None
    ) -> float:
        """
        Calculate weighted sentiment score from multiple sources.

        Args:
            reddit_score: Reddit sentiment score
            twitter_score: Twitter sentiment score
            news_score: News sentiment score
            weights: Custom weights for each source

        Returns:
            Weighted average sentiment score
        """
        if weights is None:
            weights = {
                "reddit": 0.3,
                "twitter": 0.35,
                "news": 0.35
            }

        scores = []
        total_weight = 0.0

        if reddit_score is not None:
            scores.append(reddit_score * weights["reddit"])
            total_weight += weights["reddit"]

        if twitter_score is not None:
            scores.append(twitter_score * weights["twitter"])
            total_weight += weights["twitter"]

        if news_score is not None:
            scores.append(news_score * weights["news"])
            total_weight += weights["news"]

        if not scores or total_weight == 0:
            return 0.0

        return sum(scores) / total_weight


# Global instance
sentiment_analyzer = SentimentAnalyzer()
