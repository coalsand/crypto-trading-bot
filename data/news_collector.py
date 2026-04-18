"""News sentiment data collector using RSS feeds and web scraping."""

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from xml.etree import ElementTree

import requests

from ..config import settings, SUPPORTED_COINS, CoinInfo
from ..storage import db, SentimentSource

logger = logging.getLogger(__name__)


# Crypto news RSS feeds
NEWS_FEEDS = [
    {
        "name": "CoinDesk",
        "url": "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "type": "rss"
    },
    {
        "name": "Cointelegraph",
        "url": "https://cointelegraph.com/rss",
        "type": "rss"
    },
    {
        "name": "CryptoSlate",
        "url": "https://cryptoslate.com/feed/",
        "type": "rss"
    },
    {
        "name": "Bitcoin Magazine",
        "url": "https://bitcoinmagazine.com/.rss/full/",
        "type": "rss"
    },
    {
        "name": "Decrypt",
        "url": "https://decrypt.co/feed",
        "type": "rss"
    }
]


class NewsCollector:
    """Collects sentiment data from crypto news sources."""

    def __init__(self):
        """Initialize the news collector."""
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "CryptoTradingBot/1.0 (News Aggregator)"
        })

    def fetch_rss_feed(
        self,
        feed_url: str,
        feed_name: str
    ) -> List[dict]:
        """
        Fetch articles from an RSS feed.

        Args:
            feed_url: URL of the RSS feed
            feed_name: Name of the news source

        Returns:
            List of article dictionaries
        """
        articles = []

        try:
            response = self.session.get(feed_url, timeout=30)
            response.raise_for_status()

            # Parse RSS XML
            root = ElementTree.fromstring(response.content)

            # Find all items (RSS 2.0 format)
            channel = root.find("channel")
            if channel is None:
                # Try Atom format
                items = root.findall(".//{http://www.w3.org/2005/Atom}entry")
            else:
                items = channel.findall("item")

            for item in items:
                article = self._parse_rss_item(item, feed_name)
                if article:
                    articles.append(article)

            logger.info(f"Fetched {len(articles)} articles from {feed_name}")

        except requests.RequestException as e:
            logger.error(f"Error fetching RSS feed {feed_name}: {e}")
        except ElementTree.ParseError as e:
            logger.error(f"Error parsing RSS feed {feed_name}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error with {feed_name}: {e}")

        return articles

    def _parse_rss_item(
        self,
        item: ElementTree.Element,
        source: str
    ) -> Optional[dict]:
        """Parse an RSS item into an article dictionary."""
        try:
            # RSS 2.0 format
            title = item.findtext("title", "")
            description = item.findtext("description", "")
            link = item.findtext("link", "")
            pub_date = item.findtext("pubDate", "")

            # Atom format fallback
            if not title:
                title = item.findtext("{http://www.w3.org/2005/Atom}title", "")
            if not description:
                description = item.findtext("{http://www.w3.org/2005/Atom}summary", "")
            if not link:
                link_elem = item.find("{http://www.w3.org/2005/Atom}link")
                link = link_elem.get("href", "") if link_elem is not None else ""
            if not pub_date:
                pub_date = item.findtext("{http://www.w3.org/2005/Atom}published", "")

            # Clean HTML tags from description
            description = re.sub(r"<[^>]+>", "", description)
            description = description.strip()

            # Parse publication date
            created_at = self._parse_date(pub_date) if pub_date else datetime.utcnow()

            if not title:
                return None

            return {
                "title": title.strip(),
                "description": description[:1000],  # Limit description length
                "link": link,
                "source": source,
                "created_at": created_at
            }

        except Exception as e:
            logger.debug(f"Error parsing RSS item: {e}")
            return None

    def _parse_date(self, date_str: str) -> datetime:
        """Parse various date formats from RSS feeds."""
        formats = [
            "%a, %d %b %Y %H:%M:%S %z",  # RFC 822
            "%a, %d %b %Y %H:%M:%S GMT",
            "%Y-%m-%dT%H:%M:%S%z",  # ISO 8601
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%d %H:%M:%S",
        ]

        for fmt in formats:
            try:
                return datetime.strptime(date_str, fmt).replace(tzinfo=None)
            except ValueError:
                continue

        return datetime.utcnow()

    def fetch_all_feeds(self) -> List[dict]:
        """
        Fetch articles from all configured news feeds.

        Returns:
            List of all articles
        """
        all_articles = []

        for feed in NEWS_FEEDS:
            articles = self.fetch_rss_feed(feed["url"], feed["name"])
            all_articles.extend(articles)

        # Sort by date
        all_articles.sort(key=lambda x: x["created_at"], reverse=True)

        return all_articles

    def filter_articles_for_coin(
        self,
        articles: List[dict],
        coin: CoinInfo
    ) -> List[dict]:
        """
        Filter articles relevant to a specific coin.

        Args:
            articles: List of articles
            coin: CoinInfo object

        Returns:
            Filtered list of relevant articles
        """
        relevant = []

        # Create search patterns
        patterns = [
            re.compile(rf"\b{coin.symbol}\b", re.IGNORECASE),
            re.compile(rf"\b{coin.name}\b", re.IGNORECASE),
        ]

        for article in articles:
            text = f"{article['title']} {article['description']}"

            for pattern in patterns:
                if pattern.search(text):
                    relevant.append(article)
                    break

        return relevant

    def collect_sentiment_data(
        self,
        coin: CoinInfo,
        hours: int = 24
    ) -> List[dict]:
        """
        Collect news sentiment data for a coin.

        Args:
            coin: CoinInfo object
            hours: Hours to look back

        Returns:
            List of text content with metadata
        """
        # Fetch all articles
        articles = self.fetch_all_feeds()

        # Filter by time
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        articles = [a for a in articles if a["created_at"] >= cutoff]

        # Filter by coin relevance
        relevant = self.filter_articles_for_coin(articles, coin)

        sentiment_data = []
        for article in relevant:
            # Combine title and description
            text = f"{article['title']}. {article['description']}"

            sentiment_data.append({
                "text": text,
                "source": article["source"],
                "created_at": article["created_at"],
                "link": article["link"],
                "title": article["title"]
            })

        return sentiment_data

    def fetch_and_store(
        self,
        coin: CoinInfo,
        sentiment_analyzer,
        hours: int = 24
    ) -> int:
        """
        Fetch news data, analyze sentiment, and store results.

        Args:
            coin: CoinInfo object
            sentiment_analyzer: Sentiment analysis instance
            hours: Hours to look back

        Returns:
            Number of sentiment records stored
        """
        raw_data = self.collect_sentiment_data(coin, hours)

        if not raw_data:
            logger.info(f"No news data found for {coin.symbol}")
            return 0

        stored_count = 0

        # Analyze sentiment
        texts = [d["text"] for d in raw_data]

        try:
            # Analyze sentiment (batch)
            scores = sentiment_analyzer.analyze_batch(texts)

            # Calculate simple average (news articles equally weighted)
            avg_score = sum(scores) / len(scores) if scores else 0

            # Get most recent article for sample
            latest = max(raw_data, key=lambda x: x["created_at"])

            # Store aggregated sentiment
            db.save_sentiment_data(
                symbol=coin.symbol,
                source=SentimentSource.NEWS,
                score=avg_score,
                magnitude=min(len(raw_data) / 20, 1.0),  # Normalize
                text_sample=latest["title"][:500],
                post_count=len(raw_data),
                metadata=json.dumps({
                    "sources": list(set(d["source"] for d in raw_data)),
                    "article_count": len(raw_data),
                    "time_range_hours": hours
                })
            )
            stored_count = 1

            logger.info(
                f"Stored news sentiment for {coin.symbol}: "
                f"score={avg_score:.3f}, articles={len(raw_data)}"
            )

        except Exception as e:
            logger.error(f"Error analyzing/storing news sentiment for {coin.symbol}: {e}")

        return stored_count

    def fetch_all_coins(
        self,
        sentiment_analyzer,
        hours: int = 24
    ) -> Dict[str, int]:
        """
        Fetch and store news sentiment for all supported coins.

        Args:
            sentiment_analyzer: Sentiment analysis instance
            hours: Hours to look back

        Returns:
            Dictionary mapping symbol to stored count
        """
        results = {}

        for symbol, coin_info in SUPPORTED_COINS.items():
            count = self.fetch_and_store(coin_info, sentiment_analyzer, hours)
            results[symbol] = count

        return results


# Global instance
news_collector = NewsCollector()
