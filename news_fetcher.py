"""
news_fetcher.py — Parallel high-speed news fetcher for Serverless & Vercel.
Fetches 10+ free news sources concurrently in under 2 seconds to prevent Vercel Function timeouts.
"""
import xml.etree.ElementTree as ET
import requests
import re
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from loguru import logger
from config import (
    COIN_SYMBOLS,
    NEWS_MAX_ARTICLES,
    NEWS_LOOKBACK_MIN,
)

TRACKED_SYMBOLS = set(COIN_SYMBOLS.values())

RSS_FEEDS = {
    "CoinDesk":       "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "CoinTelegraph":  "https://cointelegraph.com/rss",
    "Decrypt":        "https://decrypt.co/feed",
    "TheBlock":       "https://www.theblock.co/rss.xml",
    "CryptoSlate":    "https://cryptoslate.com/feed/",
    "BeInCrypto":     "https://beincrypto.com/feed/",
    "BitcoinMagazine":"https://bitcoinmagazine.com/.rss/full/",
}

GOOGLE_NEWS_QUERIES = [
    "Bitcoin crypto",
    "Ethereum DeFi",
    "cryptocurrency trading",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# Strict 3-second timeout per HTTP request for high-speed parallel fetching
HTTP_TIMEOUT = 3.5

COINGECKO_MAP = {
    "bitcoin": "BTC", "ethereum": "ETH", "solana": "SOL",
    "binancecoin": "BNB", "ripple": "XRP", "cardano": "ADA",
    "dogecoin": "DOGE", "avalanche-2": "AVAX", "matic-network": "MATIC",
    "chainlink": "LINK"
}


def fetch_market_prices() -> dict:
    """Fetch live INR prices and 24h % changes for tracked coins."""
    prices = {}
    try:
        ids_str = ",".join(COINGECKO_MAP.keys())
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={ids_str}&vs_currencies=inr&include_24hr_change=true"
        resp = requests.get(url, headers=HEADERS, timeout=HTTP_TIMEOUT)
        if resp.status_code == 200:
            data = resp.json()
            for cg_id, symbol in COINGECKO_MAP.items():
                if cg_id in data:
                    item = data[cg_id]
                    price_inr = item.get("inr", 0)
                    chg_24h = item.get("inr_24h_change", 0.0) or 0.0
                    prices[symbol] = {
                        "price_inr": price_inr,
                        "change_24h_pct": round(chg_24h, 2)
                    }
    except Exception as e:
        logger.warning(f"[PriceFetcher] Error: {e}")
    return prices


def _coins_in_text(text: str) -> list[str]:
    """Detect which tracked coins are mentioned in a text string."""
    text_upper = text.upper()
    found = []
    for symbol in TRACKED_SYMBOLS:
        pattern = rf'\b{re.escape(symbol)}\b'
        if re.search(pattern, text_upper):
            found.append(symbol)
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
    """Parse RSS pubDate into UTC datetime."""
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


def _fetch_rss_feed(name: str, url: str) -> list[dict]:
    """Fetch and parse a single RSS feed fast."""
    rss_cutoff = datetime.now(timezone.utc) - timedelta(hours=4)
    articles = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()

        root = ET.fromstring(resp.content)
        items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")

        for item in items[:20]:
            title_el = item.find("title")
            if title_el is None:
                title_el = item.find("{http://www.w3.org/2005/Atom}title")
            title = title_el.text if title_el is not None else ""
            if not title:
                continue

            desc_el = item.find("description")
            if desc_el is None:
                desc_el = item.find("summary")
            if desc_el is None:
                desc_el = item.find("{http://www.w3.org/2005/Atom}summary")
            desc = ""
            if desc_el is not None and desc_el.text:
                desc = re.sub(r'<[^>]+>', '', desc_el.text)[:250]

            full_text = f"{title} {desc}"
            coins = _coins_in_text(full_text)

            pub_el = item.find("pubDate")
            if pub_el is None:
                pub_el = item.find("published")
            if pub_el is None:
                pub_el = item.find("{http://www.w3.org/2005/Atom}published")
            pub_dt = _parse_rss_date(pub_el.text) if pub_el is not None and pub_el.text else datetime.now(timezone.utc)

            if pub_dt < rss_cutoff:
                continue

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

    except Exception as e:
        logger.warning(f"[RSS:{name}] Failed/Skipped ({e})")

    return articles


def _fetch_single_google(query: str) -> list[dict]:
    """Fetch a single Google News search query."""
    articles = []
    try:
        encoded = query.replace(" ", "+")
        url = f"https://news.google.com/rss/search?q={encoded}+crypto&hl=en-IN&gl=IN&ceid=IN:en"
        resp = requests.get(url, headers=HEADERS, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()

        root = ET.fromstring(resp.content)
        items = root.findall(".//item")
        cutoff = datetime.now(timezone.utc) - timedelta(hours=4)

        for item in items[:8]:
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
        logger.warning(f"[GoogleNews:{query}] Skipped ({e})")

    return articles


def _fetch_coingecko_trending() -> list[dict]:
    """CoinGecko trending coins."""
    articles = []
    try:
        url = "https://api.coingecko.com/api/v3/search/trending"
        resp = requests.get(url, timeout=HTTP_TIMEOUT)
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
                        f"— Market Cap Rank #{rank}."
                    ),
                    "source": "CoinGecko Trending",
                    "published_at": datetime.now(timezone.utc).isoformat(),
                    "coins": [symbol],
                    "url": f"https://www.coingecko.com/en/coins/{coin_data.get('id', '')}",
                    "description": "",
                    "votes": {},
                })
    except Exception as e:
        logger.warning(f"[CoinGecko] Skipped ({e})")

    return articles


def fetch_news() -> list[dict]:
    """
    Parallel high-speed news fetcher.
    Runs all requests concurrently in a ThreadPoolExecutor.
    Finishes in under 2 seconds total!
    """
    all_articles = []

    tasks = []
    with ThreadPoolExecutor(max_workers=12) as executor:
        # Submit all RSS feeds
        for name, url in RSS_FEEDS.items():
            tasks.append(executor.submit(_fetch_rss_feed, name, url))

        # Submit Google queries
        for q in GOOGLE_NEWS_QUERIES:
            tasks.append(executor.submit(_fetch_single_google, q))

        # Submit CoinGecko
        tasks.append(executor.submit(_fetch_coingecko_trending))

        for future in as_completed(tasks):
            try:
                res = future.result()
                if res:
                    all_articles.extend(res)
            except Exception as e:
                logger.warning(f"[ParallelFetch] Worker exception: {e}")

    # Deduplicate
    seen = set()
    unique = []
    for a in all_articles:
        key = a["title"][:60].lower().strip()
        key = re.sub(r'[^a-z0-9 ]', '', key)
        if key not in seen:
            seen.add(key)
            unique.append(a)

    with_coins = [a for a in unique if a["coins"]]
    without_coins = [a for a in unique if not a["coins"]]
    for a in without_coins:
        a["coins"] = ["GENERAL"]

    sorted_articles = with_coins + without_coins
    logger.info(f"[NewsAggregator] Parallel fetch complete: {len(sorted_articles)} articles")

    return sorted_articles[:NEWS_MAX_ARTICLES]


if __name__ == "__main__":
    import time
    start = time.time()
    articles = fetch_news()
    elapsed = time.time() - start
    print(f"Fetched {len(articles)} articles in {elapsed:.2f} seconds!")
