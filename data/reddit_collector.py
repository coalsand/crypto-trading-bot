"""Reddit sentiment data collector using PRAW."""

import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import praw
from praw.models import Submission, Comment

from ..config import settings, SUPPORTED_COINS, CoinInfo
from ..storage import db, SentimentSource

logger = logging.getLogger(__name__)


class RedditCollector:
    """Collects sentiment data from Reddit cryptocurrency subreddits."""

    def __init__(self):
        """Initialize the Reddit API connection."""
        self.reddit = None
        self._initialize_client()

    def _initialize_client(self):
        """Initialize PRAW client."""
        if not settings.reddit.client_id or not settings.reddit.client_secret:
            logger.warning("Reddit API credentials not configured")
            return

        try:
            self.reddit = praw.Reddit(
                client_id=settings.reddit.client_id,
                client_secret=settings.reddit.client_secret,
                user_agent=settings.reddit.user_agent
            )
            # Test connection
            self.reddit.user.me()
            logger.info("Reddit API connection established")
        except Exception as e:
            logger.warning(f"Failed to initialize Reddit client: {e}")
            # Continue with read-only mode
            try:
                self.reddit = praw.Reddit(
                    client_id=settings.reddit.client_id,
                    client_secret=settings.reddit.client_secret,
                    user_agent=settings.reddit.user_agent
                )
            except Exception as e2:
                logger.error(f"Failed to initialize Reddit client in read-only mode: {e2}")
                self.reddit = None

    def _is_available(self) -> bool:
        """Check if Reddit API is available."""
        return self.reddit is not None

    def fetch_subreddit_posts(
        self,
        subreddit_name: str,
        limit: int = 100,
        time_filter: str = "day"
    ) -> List[dict]:
        """
        Fetch posts from a subreddit.

        Args:
            subreddit_name: Name of the subreddit
            limit: Maximum number of posts to fetch
            time_filter: Time filter (hour, day, week, month, year, all)

        Returns:
            List of post dictionaries
        """
        if not self._is_available():
            logger.warning("Reddit API not available")
            return []

        posts = []

        try:
            subreddit = self.reddit.subreddit(subreddit_name)

            # Fetch hot and top posts
            for submission in subreddit.hot(limit=limit // 2):
                posts.append(self._parse_submission(submission))

            for submission in subreddit.top(time_filter=time_filter, limit=limit // 2):
                # Avoid duplicates
                if not any(p["id"] == submission.id for p in posts):
                    posts.append(self._parse_submission(submission))

            logger.info(f"Fetched {len(posts)} posts from r/{subreddit_name}")

        except Exception as e:
            logger.error(f"Error fetching posts from r/{subreddit_name}: {e}")

        return posts

    def _parse_submission(self, submission: Submission) -> dict:
        """Parse a Reddit submission into a dictionary."""
        return {
            "id": submission.id,
            "title": submission.title,
            "selftext": submission.selftext or "",
            "score": submission.score,
            "upvote_ratio": submission.upvote_ratio,
            "num_comments": submission.num_comments,
            "created_utc": datetime.utcfromtimestamp(submission.created_utc),
            "subreddit": submission.subreddit.display_name,
            "url": submission.url,
            "is_self": submission.is_self
        }

    def fetch_comments_for_post(
        self,
        post_id: str,
        limit: int = 50
    ) -> List[dict]:
        """
        Fetch top comments for a post.

        Args:
            post_id: Reddit post ID
            limit: Maximum number of comments

        Returns:
            List of comment dictionaries
        """
        if not self._is_available():
            return []

        comments = []

        try:
            submission = self.reddit.submission(id=post_id)
            submission.comments.replace_more(limit=0)

            for comment in submission.comments[:limit]:
                if isinstance(comment, Comment):
                    comments.append({
                        "id": comment.id,
                        "body": comment.body,
                        "score": comment.score,
                        "created_utc": datetime.utcfromtimestamp(comment.created_utc)
                    })

        except Exception as e:
            logger.error(f"Error fetching comments for post {post_id}: {e}")

        return comments

    def search_coin_mentions(
        self,
        coin: CoinInfo,
        limit: int = 100
    ) -> List[dict]:
        """
        Search for mentions of a specific coin across subreddits.

        Args:
            coin: CoinInfo object
            limit: Maximum results

        Returns:
            List of relevant posts
        """
        if not self._is_available():
            return []

        results = []
        search_terms = [coin.symbol, coin.name]

        try:
            # Search in coin-specific subreddits first
            for subreddit_name in coin.subreddits:
                posts = self.fetch_subreddit_posts(subreddit_name, limit=limit // len(coin.subreddits))
                results.extend(posts)

            # Also search in general crypto subreddits
            for subreddit_name in settings.reddit.subreddits:
                if subreddit_name not in coin.subreddits:
                    subreddit = self.reddit.subreddit(subreddit_name)

                    for term in search_terms:
                        for submission in subreddit.search(term, time_filter="day", limit=20):
                            if not any(r["id"] == submission.id for r in results):
                                results.append(self._parse_submission(submission))

        except Exception as e:
            logger.error(f"Error searching for {coin.symbol} mentions: {e}")

        return results

    def collect_sentiment_data(
        self,
        coin: CoinInfo,
        limit: int = 100
    ) -> List[dict]:
        """
        Collect raw sentiment data for a coin.

        Args:
            coin: CoinInfo object
            limit: Maximum posts to collect

        Returns:
            List of text content with metadata
        """
        posts = self.search_coin_mentions(coin, limit)
        sentiment_data = []

        for post in posts:
            # Combine title and body for analysis
            text = f"{post['title']} {post['selftext']}".strip()

            if text:
                sentiment_data.append({
                    "text": text,
                    "score": post["score"],
                    "upvote_ratio": post["upvote_ratio"],
                    "created_at": post["created_utc"],
                    "source_id": post["id"],
                    "subreddit": post["subreddit"]
                })

        return sentiment_data

    def fetch_and_store(
        self,
        coin: CoinInfo,
        sentiment_analyzer,
        limit: int = 100
    ) -> int:
        """
        Fetch Reddit data, analyze sentiment, and store results.

        Args:
            coin: CoinInfo object
            sentiment_analyzer: Sentiment analysis instance
            limit: Maximum posts

        Returns:
            Number of sentiment records stored
        """
        raw_data = self.collect_sentiment_data(coin, limit)

        if not raw_data:
            logger.info(f"No Reddit data found for {coin.symbol}")
            return 0

        stored_count = 0

        # Aggregate sentiment
        texts = [d["text"] for d in raw_data]
        weights = [d["score"] for d in raw_data]

        try:
            # Analyze sentiment (batch)
            scores = sentiment_analyzer.analyze_batch(texts)

            # Calculate weighted average
            total_weight = sum(max(w, 1) for w in weights)  # Ensure positive weights
            weighted_score = sum(
                s * max(w, 1) for s, w in zip(scores, weights)
            ) / total_weight if total_weight > 0 else 0

            # Get a sample text for reference
            top_post = max(raw_data, key=lambda x: x["score"])

            # Store aggregated sentiment
            db.save_sentiment_data(
                symbol=coin.symbol,
                source=SentimentSource.REDDIT,
                score=weighted_score,
                magnitude=min(len(raw_data) / 100, 1.0),  # Normalize by post count
                text_sample=top_post["text"][:500],  # Truncate long text
                post_count=len(raw_data),
                metadata=json.dumps({
                    "subreddits": list(set(d["subreddit"] for d in raw_data)),
                    "avg_upvote_ratio": sum(d["upvote_ratio"] for d in raw_data) / len(raw_data),
                    "total_score": sum(d["score"] for d in raw_data)
                })
            )
            stored_count = 1

            logger.info(
                f"Stored Reddit sentiment for {coin.symbol}: "
                f"score={weighted_score:.3f}, posts={len(raw_data)}"
            )

        except Exception as e:
            logger.error(f"Error analyzing/storing Reddit sentiment for {coin.symbol}: {e}")

        return stored_count

    def fetch_all_coins(
        self,
        sentiment_analyzer,
        limit_per_coin: int = 50
    ) -> Dict[str, int]:
        """
        Fetch and store sentiment for all supported coins.

        Args:
            sentiment_analyzer: Sentiment analysis instance
            limit_per_coin: Maximum posts per coin

        Returns:
            Dictionary mapping symbol to stored count
        """
        results = {}

        for symbol, coin_info in SUPPORTED_COINS.items():
            count = self.fetch_and_store(coin_info, sentiment_analyzer, limit_per_coin)
            results[symbol] = count

        return results


# Global instance
reddit_collector = RedditCollector()
