"""StockTwits sentiment collector.

Public streams endpoint: https://api.stocktwits.com/api/2/streams/symbol/{SYMBOL}.json

Symbol format:
    - Stocks: plain ticker (e.g. AAPL, MSFT)
    - Crypto: suffix ".X" (e.g. BTC.X, ETH.X)

Many messages are user-tagged as Bullish/Bearish — use that directly (+1 / -1).
Untagged messages fall back to the NLP sentiment analyzer.

No auth needed; public rate limit is ~200 requests/hour per IP.
"""

import json
from typing import Dict, List, Optional

import requests

from ..config import SUPPORTED_COINS
from ..storage import db, SentimentSource
from ..utils import get_logger

logger = get_logger("stocktwits")

_API = "https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"
_UA = "crypto-trading-bot/1.0 (sentiment collector)"


class StockTwitsCollector:
    """Collects sentiment from StockTwits streams."""

    def __init__(self, timeout: int = 10):
        self.timeout = timeout

    def fetch_messages(self, stocktwits_symbol: str, limit: int = 30) -> List[dict]:
        """Fetch recent StockTwits messages for a symbol (returns empty list on failure)."""
        try:
            r = requests.get(
                _API.format(symbol=stocktwits_symbol),
                headers={"User-Agent": _UA},
                timeout=self.timeout,
            )
            if r.status_code == 429:
                logger.warning(f"stocktwits: rate limited for {stocktwits_symbol}")
                return []
            if r.status_code == 404:
                logger.info(f"stocktwits: symbol not found: {stocktwits_symbol}")
                return []
            r.raise_for_status()
            return (r.json().get("messages") or [])[:limit]
        except Exception as e:
            logger.error(f"stocktwits: fetch failed for {stocktwits_symbol}: {e}")
            return []

    def _message_score(self, msg: dict, sentiment_analyzer) -> Optional[float]:
        """Return a sentiment score in [-1, 1] for a StockTwits message, or None if unscoreable."""
        ent = msg.get("entities") or {}
        tagged = (ent.get("sentiment") or {}).get("basic")
        if tagged == "Bullish":
            return 1.0
        if tagged == "Bearish":
            return -1.0

        text = (msg.get("body") or "").strip()
        if not text:
            return None
        try:
            result = sentiment_analyzer.analyze(text)
            return float(result.score)
        except Exception as e:
            logger.debug(f"stocktwits: NLP fallback failed: {e}")
            return None

    def fetch_and_store(
        self,
        symbol_key: str,
        stocktwits_symbol: str,
        sentiment_analyzer,
        limit: int = 30,
    ) -> int:
        """Fetch messages, score them, store the aggregate. Returns 1 if stored, 0 otherwise."""
        msgs = self.fetch_messages(stocktwits_symbol, limit=limit)
        if not msgs:
            return 0

        scores: List[float] = []
        weights: List[float] = []
        self_tagged = 0
        sample_text = ""
        max_engagement = -1

        for m in msgs:
            score = self._message_score(m, sentiment_analyzer)
            if score is None:
                continue
            if ((m.get("entities") or {}).get("sentiment") or {}).get("basic"):
                self_tagged += 1

            followers = ((m.get("user") or {}).get("followers")) or 0
            likes = ((m.get("likes") or {}).get("total")) or 0
            engagement = likes * 2 + followers / 1000
            weight = max(engagement, 1.0)

            scores.append(score)
            weights.append(weight)

            if engagement > max_engagement:
                max_engagement = engagement
                sample_text = (m.get("body") or "")[:500]

        if not scores:
            return 0

        total_w = sum(weights)
        weighted = sum(s * w for s, w in zip(scores, weights)) / total_w

        try:
            db.save_sentiment_data(
                symbol=symbol_key,
                source=SentimentSource.STOCKTWITS,
                score=weighted,
                magnitude=min(len(scores) / float(limit), 1.0),
                text_sample=sample_text,
                post_count=len(scores),
                metadata=json.dumps({
                    "self_tagged": self_tagged,
                    "message_count": len(msgs),
                    "scored_count": len(scores),
                }),
            )
            logger.info(
                f"stocktwits: {symbol_key} ({stocktwits_symbol}) "
                f"score={weighted:.3f} scored={len(scores)} self_tagged={self_tagged}"
            )
            return 1
        except Exception as e:
            logger.error(f"stocktwits: store failed for {symbol_key}: {e}")
            return 0

    def fetch_all_coins(
        self,
        sentiment_analyzer,
        max_results_per_coin: int = 30,
    ) -> Dict[str, int]:
        """Fetch StockTwits sentiment for every supported coin."""
        results: Dict[str, int] = {}
        for symbol, coin in SUPPORTED_COINS.items():
            stocktwits_symbol = getattr(coin, "stocktwits_symbol", f"{symbol}.X")
            results[symbol] = self.fetch_and_store(
                symbol, stocktwits_symbol, sentiment_analyzer, limit=max_results_per_coin
            )
        return results


stocktwits_collector = StockTwitsCollector()
