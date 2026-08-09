"""
news_fetcher.py — Fetches crypto news from 100% FREE sources (no paid API needed).
Sources: CoinDesk RSS, CoinTelegraph RSS, Decrypt RSS, Google News RSS, CoinGecko, Binance
Runs every 5 minutes and returns structured news items for LLM analysis.
"""
import xml.etree.ElementTree as ET
import requests
import re
from datetime import datetime, timezone, timedelta
from loguru import logger
from config import (
    COIN_SYMBOLS,
    NEWS_MAX_ARTICLES,
    NEWS_LOOKBACK_MIN,
)

TRACKED_SYMBOLS = set(COIN_SYMBOLS.values())

# ─────────────────────────────────────────────
#  100% FREE RSS SOURCES (no key required)
# ─────────────────────────────────────────────
RSS_FEEDS = {
    "CoinDesk":       "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "CoinTelegraph":  "https://cointelegraph.com/rss",
    "Decrypt":        "https://decrypt.co/feed",
    "TheBlock":       "https://www.theblock.co/rss.xml",
    "CryptoSlate":    "https://cryptoslate.com/feed/",
    "BeInCrypto":     "https://beincrypto.com/feed/",
    "BitcoinMagazine":"https://bitcoinmagazine.com/.rss/full/",
}

# Google News RSS (searches for crypto topics, completely free)
GOOGLE_NEWS_QUERIES = [
    "Bitcoin crypto",
    "Ethereum DeFi",
    "cryptocurrency trading",
    "crypto regulation SEC",
    "altcoin news",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def _coins_in_text(text: str) -> list[str]:
    """Detect which tracked coins are mentioned in a text string."""
    text_upper = text.upper()
    found = []
    for symbol in TRACKED_SYMBOLS:
        # Match whole word to avoid false positives (e.g. "LINK" in "linking")
        pattern = rf'\b{re.escape(symbol)}\b'
        if re.search(pattern, text_upper):
            found.append(symbol)
    # Also match full names
    name_map = {
        "BITCOIN": "BTC", "ETHEREUM": "ETH", "SOLANA": "SOL",
        "BINANCE": "BNB", "RIPPLE": "XRP", "CARDANO": "ADA",
        "DOGECOIN": "DOGE", "AVALANCHE": "AVAX", "POLYGON": "MATIC",
        "CHAINLINK": "LINK",
    }
    for name, symbol in name_map.items():
        if name in text_upper and symbol not in found:
            found.append(symbol)
    return found


def _parse_rss_date(date_str: str) -> datetime:
    """Parse RSS pubDate into UTC datetime (handles multiple formats)."""
    formats = [
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S GMT",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return datetime.now(timezone.utc)


def _fetch_rss_feed(name: str, url: str, cutoff: datetime) -> list[dict]:
    """Fetch and parse a single RSS feed, return articles matching tracked coins.
    Uses a wider 2-hour window for RSS (RSS feeds update every 15-60 min).
    """
    # RSS feeds don't update every second — use 2-hour window to get enough articles
    rss_cutoff = datetime.now(timezone.utc) - timedelta(hours=2)
    articles = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.raise_for_status()

        root = ET.fromstring(resp.content)
        # Handle both RSS 2.0 and Atom feeds
        items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")

        for item in items[:30]:
            # Get title (fix deprecation: use find() with None check)
            title_el = item.find("title")
            if title_el is None:
                title_el = item.find("{http://www.w3.org/2005/Atom}title")
            title = title_el.text if title_el is not None else ""
            if not title:
                continue

            # Get description/summary
            desc_el = item.find("description")
            if desc_el is None:
                desc_el = item.find("summary")
            if desc_el is None:
                desc_el = item.find("{http://www.w3.org/2005/Atom}summary")
            desc = ""
            if desc_el is not None and desc_el.text:
                desc = re.sub(r'<[^>]+>', '', desc_el.text)[:300]

            full_text = f"{title} {desc}"

            # Find matching coins
            coins = _coins_in_text(full_text)

            # Get publication date
            pub_el = item.find("pubDate")
            if pub_el is None:
                pub_el = item.find("published")
            if pub_el is None:
                pub_el = item.find("{http://www.w3.org/2005/Atom}published")
            pub_dt = _parse_rss_date(pub_el.text) if pub_el is not None and pub_el.text else datetime.now(timezone.utc)

            # Use 2-hour window for RSS
            if pub_dt < rss_cutoff:
                continue

            # Get URL
            link_el = item.find("link")
            if link_el is None:
                link_el = item.find("{http://www.w3.org/2005/Atom}link")
            link = ""
            if link_el is not None:
                link = link_el.text or link_el.get("href", "")

            articles.append({
                "title": title.strip(),
                "source": name,
                "published_at": pub_dt.isoformat(),
                "coins": coins,
                "url": link,
                "description": desc,
                "votes": {},
            })

        logger.info(f"[RSS:{name}] {len(articles)} articles (last 2h)")
    except requests.exceptions.Timeout:
        logger.warning(f"[RSS:{name}] Timeout -- skipping")
    except requests.exceptions.RequestException as e:
        logger.warning(f"[RSS:{name}] Error: {e}")
    except ET.ParseError as e:
        logger.warning(f"[RSS:{name}] XML parse error: {e}")
    except Exception as e:
        logger.warning(f"[RSS:{name}] Unexpected error: {e}")

    return articles


def _fetch_google_news(cutoff: datetime) -> list[dict]:
    """Fetch crypto news from Google News RSS (completely free, no key)."""
    articles = []
    for query in GOOGLE_NEWS_QUERIES[:3]:  # Limit to avoid rate limiting
        try:
            encoded = query.replace(" ", "+")
            url = f"https://news.google.com/rss/search?q={encoded}+crypto&hl=en-IN&gl=IN&ceid=IN:en"
            resp = requests.get(url, headers=HEADERS, timeout=10)
            resp.raise_for_status()

            root = ET.fromstring(resp.content)
            items = root.findall(".//item")

            for item in items[:10]:
                title_el = item.find("title")
                title = title_el.text if title_el is not None else ""
                if not title:
                    continue

                coins = _coins_in_text(title)
                pub_el = item.find("pubDate")
                pub_dt = _parse_rss_date(pub_el.text) if pub_el is not None and pub_el.text else datetime.now(timezone.utc)

                if pub_dt < cutoff:
                    continue

                link_el = item.find("link")
                link = link_el.text if link_el is not None else ""

                articles.append({
                    "title": title.strip(),
                    "source": "Google News",
                    "published_at": pub_dt.isoformat(),
                    "coins": coins,
                    "url": link,
                    "description": "",
                    "votes": {},
                })

        except Exception as e:
            logger.warning(f"[GoogleNews] Query '{query}' failed: {e}")

    logger.info(f"[GoogleNews] {len(articles)} articles")
    return articles


def _fetch_coingecko_trending(cutoff: datetime) -> list[dict]:
    """CoinGecko trending coins (free, no key needed)."""
    articles = []
    try:
        url = "https://api.coingecko.com/api/v3/search/trending"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        for item in data.get("coins", []):
            coin_data = item.get("item", {})
            symbol = coin_data.get("symbol", "").upper()
            name = coin_data.get("name", symbol)
            rank = coin_data.get("market_cap_rank", "?")

            if symbol in TRACKED_SYMBOLS:
                articles.append({
                    "title": (
                        f"{name} ({symbol}) is trending #1 on CoinGecko "
                        f"— Market Cap Rank #{rank}. "
                        f"Price change 24h: {coin_data.get('data', {}).get('price_change_percentage_24h', {}).get('usd', 'N/A')}%"
                    ),
                    "source": "CoinGecko Trending",
                    "published_at": datetime.now(timezone.utc).isoformat(),
                    "coins": [symbol],
                    "url": f"https://www.coingecko.com/en/coins/{coin_data.get('id', '')}",
                    "description": "",
                    "votes": {},
                })

        logger.info(f"[CoinGecko] {len(articles)} trending items for tracked coins")
    except Exception as e:
        logger.warning(f"[CoinGecko] Error: {e}")

    return articles


def _fetch_binance_news(cutoff: datetime) -> list[dict]:
    """Binance announcements (free, no key needed)."""
    articles = []
    try:
        url = "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
        params = {"type": 1, "pageNo": 1, "pageSize": 15}
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        for item in data.get("data", {}).get("articles", []):
            title = item.get("title", "")
            coins = _coins_in_text(title)
            if coins:
                articles.append({
                    "title": f"[BINANCE ANNOUNCEMENT] {title}",
                    "source": "Binance",
                    "published_at": datetime.now(timezone.utc).isoformat(),
                    "coins": coins,
                    "url": f"https://www.binance.com/en/support/announcement/{item.get('id', '')}",
                    "description": "",
                    "votes": {},
                })

        logger.info(f"[Binance] {len(articles)} relevant announcements")
    except Exception as e:
        logger.warning(f"[Binance] Error: {e}")

    return articles


# ─────────────────────────────────────────────
#  MAIN ENTRY POINT
# ─────────────────────────────────────────────

def fetch_news() -> list[dict]:
    """
    Fetch crypto news from all FREE sources.
    No API keys required for any source.
    
    Priority:
    1. RSS feeds (CoinDesk, CoinTelegraph, Decrypt, TheBlock, etc.)
    2. Google News RSS
    3. CoinGecko trending
    4. Binance announcements
    
    Returns deduplicated list of up to NEWS_MAX_ARTICLES articles.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=NEWS_LOOKBACK_MIN)
    all_articles = []

    # 1. RSS Feeds (parallel-ish via sequential requests)
    for name, url in RSS_FEEDS.items():
        articles = _fetch_rss_feed(name, url, cutoff)
        all_articles.extend(articles)

    # 2. Google News RSS
    all_articles.extend(_fetch_google_news(cutoff))

    # 3. CoinGecko trending
    all_articles.extend(_fetch_coingecko_trending(cutoff))

    # 4. Binance announcements
    all_articles.extend(_fetch_binance_news(cutoff))

    # Deduplicate by title similarity (first 60 chars)
    seen = set()
    unique = []
    for a in all_articles:
        key = a["title"][:60].lower().strip()
        # Remove punctuation for fuzzy dedup
        key = re.sub(r'[^a-z0-9 ]', '', key)
        if key not in seen:
            seen.add(key)
            unique.append(a)

    # Sort: articles WITH coin matches first, then by time
    with_coins = [a for a in unique if a["coins"]]
    without_coins = [a for a in unique if not a["coins"]]
    
    # For articles without specific coins, add GENERAL tag
    for a in without_coins:
        a["coins"] = ["GENERAL"]

    sorted_articles = with_coins + without_coins

    total = len(sorted_articles)
    with_coin_count = len(with_coins)
    logger.info(
        f"[NewsAggregator] Total: {total} unique articles "
        f"({with_coin_count} with coin tags, {total - with_coin_count} general)"
    )

    return sorted_articles[:NEWS_MAX_ARTICLES]


if __name__ == "__main__":
    # Quick test
    print("Testing news fetcher (all free sources)...")
    print("=" * 70)
    articles = fetch_news()
    print(f"\nFetched {len(articles)} articles total\n")
    for i, a in enumerate(articles, 1):
        coins = ", ".join(a["coins"]) or "GENERAL"
        print(f"[{i:02d}] [{coins:20s}] {a['title'][:60]}...")
        print(f"       Source: {a['source']} | {a['published_at'][:16]}")
    print(f"\nDone. {len(articles)} articles ready for LLM analysis.")
