"""Twitter/X sentiment data collector using Tweepy."""

import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import tweepy

from ..config import settings, SUPPORTED_COINS, CoinInfo
from ..storage import db, SentimentSource

logger = logging.getLogger(__name__)


class TwitterCollector:
    """Collects sentiment data from Twitter/X."""

    def __init__(self):
        """Initialize the Twitter API connection."""
        self.client = None
        self._initialize_client()

    def _initialize_client(self):
        """Initialize Tweepy client."""
        if not settings.twitter.bearer_token:
            logger.warning("Twitter API credentials not configured")
            return

        try:
            self.client = tweepy.Client(
                bearer_token=settings.twitter.bearer_token,
                consumer_key=settings.twitter.api_key,
                consumer_secret=settings.twitter.api_secret,
                access_token=settings.twitter.access_token,
                access_token_secret=settings.twitter.access_token_secret,
                wait_on_rate_limit=True
            )
            logger.info("Twitter API connection established")
        except Exception as e:
            logger.error(f"Failed to initialize Twitter client: {e}")
            self.client = None

    def _is_available(self) -> bool:
        """Check if Twitter API is available."""
        return self.client is not None

    def search_tweets(
        self,
        query: str,
        max_results: int = 100,
        start_time: Optional[datetime] = None
    ) -> List[dict]:
        """
        Search for tweets matching a query.

        Args:
            query: Search query
            max_results: Maximum tweets to return (10-100 for recent search)
            start_time: Start time for search

        Returns:
            List of tweet dictionaries
        """
        if not self._is_available():
            logger.warning("Twitter API not available")
            return []

        tweets = []

        try:
            # Use recent search endpoint
            if start_time is None:
                start_time = datetime.utcnow() - timedelta(days=1)

            # Ensure max_results is within API limits
            max_results = min(max(max_results, 10), 100)

            response = self.client.search_recent_tweets(
                query=query,
                max_results=max_results,
                start_time=start_time,
                tweet_fields=["created_at", "public_metrics", "author_id", "lang"],
                expansions=["author_id"],
                user_fields=["username", "verified", "public_metrics"]
            )

            if response.data:
                # Build user lookup
                users = {}
                if response.includes and "users" in response.includes:
                    for user in response.includes["users"]:
                        users[user.id] = {
                            "username": user.username,
                            "verified": user.verified if hasattr(user, "verified") else False,
                            "followers": user.public_metrics.get("followers_count", 0)
                            if hasattr(user, "public_metrics") else 0
                        }

                for tweet in response.data:
                    user_info = users.get(tweet.author_id, {})
                    tweets.append({
                        "id": tweet.id,
                        "text": tweet.text,
                        "created_at": tweet.created_at,
                        "author_id": tweet.author_id,
                        "username": user_info.get("username", "unknown"),
                        "verified": user_info.get("verified", False),
                        "followers": user_info.get("followers", 0),
                        "metrics": tweet.public_metrics if hasattr(tweet, "public_metrics") else {},
                        "lang": tweet.lang if hasattr(tweet, "lang") else "en"
                    })

            logger.info(f"Found {len(tweets)} tweets for query: {query[:50]}...")

        except tweepy.TooManyRequests:
            logger.warning("Twitter rate limit reached, waiting...")
        except tweepy.TwitterServerError as e:
            logger.error(f"Twitter server error: {e}")
        except Exception as e:
            logger.error(f"Error searching tweets: {e}")

        return tweets

    def search_coin_tweets(
        self,
        coin: CoinInfo,
        max_results: int = 100
    ) -> List[dict]:
        """
        Search for tweets about a specific coin.

        Args:
            coin: CoinInfo object
            max_results: Maximum results per hashtag

        Returns:
            List of relevant tweets
        """
        if not self._is_available():
            return []

        all_tweets = []
        seen_ids = set()

        # Search by hashtags
        for hashtag in coin.twitter_hashtags:
            query = f"{hashtag} -is:retweet lang:en"
            tweets = self.search_tweets(query, max_results=max_results // len(coin.twitter_hashtags))

            for tweet in tweets:
                if tweet["id"] not in seen_ids:
                    seen_ids.add(tweet["id"])
                    all_tweets.append(tweet)

        # Also search by symbol and name
        symbol_query = f"${coin.symbol} OR {coin.name} crypto -is:retweet lang:en"
        tweets = self.search_tweets(symbol_query, max_results=max_results // 2)

        for tweet in tweets:
            if tweet["id"] not in seen_ids:
                seen_ids.add(tweet["id"])
                all_tweets.append(tweet)

        return all_tweets

    def get_user_tweets(
        self,
        username: str,
        max_results: int = 10
    ) -> List[dict]:
        """
        Get recent tweets from a specific user.

        Args:
            username: Twitter username (without @)
            max_results: Maximum tweets to fetch

        Returns:
            List of tweet dictionaries
        """
        if not self._is_available():
            return []

        tweets = []

        try:
            # Get user ID first
            user = self.client.get_user(username=username)
            if not user.data:
                logger.warning(f"User not found: {username}")
                return []

            # Get user's tweets
            response = self.client.get_users_tweets(
                user.data.id,
                max_results=min(max(max_results, 5), 100),
                tweet_fields=["created_at", "public_metrics"],
                exclude=["retweets", "replies"]
            )

            if response.data:
                for tweet in response.data:
                    tweets.append({
                        "id": tweet.id,
                        "text": tweet.text,
                        "created_at": tweet.created_at,
                        "author_id": user.data.id,
                        "username": username,
                        "verified": True,  # Assume influential accounts are verified
                        "followers": 0,
                        "metrics": tweet.public_metrics if hasattr(tweet, "public_metrics") else {}
                    })

        except Exception as e:
            logger.error(f"Error fetching tweets from @{username}: {e}")

        return tweets

    def collect_sentiment_data(
        self,
        coin: CoinInfo,
        max_results: int = 100
    ) -> List[dict]:
        """
        Collect raw sentiment data for a coin.

        Args:
            coin: CoinInfo object
            max_results: Maximum tweets to collect

        Returns:
            List of text content with metadata
        """
        tweets = self.search_coin_tweets(coin, max_results)
        sentiment_data = []

        for tweet in tweets:
            # Filter out non-English tweets
            if tweet.get("lang", "en") != "en":
                continue

            metrics = tweet.get("metrics", {})
            engagement = (
                metrics.get("like_count", 0) +
                metrics.get("retweet_count", 0) * 2 +
                metrics.get("reply_count", 0)
            )

            sentiment_data.append({
                "text": tweet["text"],
                "engagement": engagement,
                "followers": tweet.get("followers", 0),
                "verified": tweet.get("verified", False),
                "created_at": tweet["created_at"],
                "source_id": str(tweet["id"]),
                "username": tweet.get("username", "unknown")
            })

        return sentiment_data

    def fetch_and_store(
        self,
        coin: CoinInfo,
        sentiment_analyzer,
        max_results: int = 100
    ) -> int:
        """
        Fetch Twitter data, analyze sentiment, and store results.

        Args:
            coin: CoinInfo object
            sentiment_analyzer: Sentiment analysis instance
            max_results: Maximum tweets

        Returns:
            Number of sentiment records stored
        """
        raw_data = self.collect_sentiment_data(coin, max_results)

        if not raw_data:
            logger.info(f"No Twitter data found for {coin.symbol}")
            return 0

        stored_count = 0

        # Aggregate sentiment with engagement weighting
        texts = [d["text"] for d in raw_data]

        # Weight by engagement and verified status
        weights = []
        for d in raw_data:
            weight = max(d["engagement"], 1)
            if d["verified"]:
                weight *= 2  # Double weight for verified accounts
            if d["followers"] > 10000:
                weight *= 1.5  # Boost for influential accounts
            weights.append(weight)

        try:
            # Analyze sentiment (batch)
            scores = sentiment_analyzer.analyze_batch(texts)

            # Calculate weighted average
            total_weight = sum(weights)
            weighted_score = sum(
                s * w for s, w in zip(scores, weights)
            ) / total_weight if total_weight > 0 else 0

            # Get top engaged tweet for sample
            top_tweet = max(raw_data, key=lambda x: x["engagement"])

            # Store aggregated sentiment
            db.save_sentiment_data(
                symbol=coin.symbol,
                source=SentimentSource.TWITTER,
                score=weighted_score,
                magnitude=min(len(raw_data) / 100, 1.0),
                text_sample=top_tweet["text"][:500],
                post_count=len(raw_data),
                metadata=json.dumps({
                    "verified_count": sum(1 for d in raw_data if d["verified"]),
                    "total_engagement": sum(d["engagement"] for d in raw_data),
                    "unique_users": len(set(d["username"] for d in raw_data))
                })
            )
            stored_count = 1

            logger.info(
                f"Stored Twitter sentiment for {coin.symbol}: "
                f"score={weighted_score:.3f}, tweets={len(raw_data)}"
            )

        except Exception as e:
            logger.error(f"Error analyzing/storing Twitter sentiment for {coin.symbol}: {e}")

        return stored_count

    def fetch_all_coins(
        self,
        sentiment_analyzer,
        max_results_per_coin: int = 50
    ) -> Dict[str, int]:
        """
        Fetch and store sentiment for all supported coins.

        Args:
            sentiment_analyzer: Sentiment analysis instance
            max_results_per_coin: Maximum tweets per coin

        Returns:
            Dictionary mapping symbol to stored count
        """
        results = {}

        for symbol, coin_info in SUPPORTED_COINS.items():
            count = self.fetch_and_store(coin_info, sentiment_analyzer, max_results_per_coin)
            results[symbol] = count

        return results


# Global instance
twitter_collector = TwitterCollector()
