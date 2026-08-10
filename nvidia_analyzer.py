"""
nvidia_analyzer.py — Analyzes crypto news using NVIDIA NIM LLM.
Sends batched news to Llama 3.1 70B and returns structured trade signals.
"""
import json
import re
from openai import OpenAI
from loguru import logger
from config import (
    NVIDIA_API_KEY,
    NVIDIA_BASE_URL,
    NVIDIA_MODEL,
    NVIDIA_MAX_TOKENS,
    NVIDIA_TEMPERATURE,
    COIN_SYMBOLS,
    MIN_CONFIDENCE_TO_TRADE,
    URGENCY_LEVELS_TO_TRADE,
)

# NVIDIA NIM client instantiated lazily inside analyze_news()

SYSTEM_PROMPT = """You are a world-class Quantitative Crypto Trading AI.
You combine 24-hour price momentum and real-time news headlines to execute high-probability trading decisions.

MANDATE:
1. Evaluate both 24-hour price trends and news headlines for the tracked coins.
2. Be active and decision-driven: select at least ONE strong BUY or SELL candidate on every cycle.
3. If a coin has positive 24h momentum or bullish news, generate a BUY signal.
4. If a coin has negative 24h momentum or bearish news, generate a SELL signal.
5. Set confidence between 60% and 95%.

Respond ONLY with a valid JSON array."""

ANALYSIS_PROMPT_TEMPLATE = """Analyze the live 24-hour market price trends and latest news headlines below:

LIVE 24-HOUR MARKET TRENDS:
{market_trends_text}

LATEST NEWS HEADLINES:
{news_text}

TRACKED COINS: {coins}

Select the #1 BEST BUY candidate and/or #1 BEST SELL candidate.
Output a JSON array where each item has EXACTLY these fields:
{{
  "coin": "BTC" (must be one of the tracked coins above),
  "signal": "BUY" or "SELL",
  "confidence": 60-95 (integer rating),
  "urgency": "HIGH" or "MEDIUM" or "LOW",
  "reasoning": "Clear concise sentence combining price momentum and news sentiment",
  "news_basis": "Key trend or headline driving this recommendation"
}}

Respond with ONLY a valid JSON array."""


def _format_news_for_llm(articles: list[dict]) -> str:
    """Format news articles into a compact string for the LLM prompt."""
    lines = []
    for i, article in enumerate(articles, 1):
        coins_str = ", ".join(article.get("coins", ["GENERAL"])) or "GENERAL"
        lines.append(
            f"[{i}] [{coins_str}] {article['title']} (Source: {article['source']}, "
            f"Time: {article.get('published_at', 'Unknown')})"
        )
    return "\n".join(lines)


def _extract_json(text: str) -> list:
    """Robustly extract JSON array from LLM response."""
    text = text.strip()

    # Try direct parse first
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return [result]
    except json.JSONDecodeError:
        pass

    # Try finding JSON array in the text
    match = re.search(r'\[.*\]', text, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group())
            return result if isinstance(result, list) else [result]
        except json.JSONDecodeError:
            pass

    # Try finding individual JSON objects
    objects = re.findall(r'\{[^{}]+\}', text, re.DOTALL)
    if objects:
        parsed = []
        for obj in objects:
            try:
                parsed.append(json.loads(obj))
            except Exception:
                pass
        if parsed:
            return parsed

    logger.warning(f"[NVIDIA] Could not parse JSON from response: {text[:200]}")
    return []


def analyze_news(articles: list[dict], prices: dict = None) -> list[dict]:
    """
    Send market trends + news to NVIDIA NIM LLM and get trade signals.
    Returns list of validated signal dicts.
    """
    if not NVIDIA_API_KEY:
        logger.error("[NVIDIA] No API key set — cannot analyze market")
        return []

    tracked_coins = list(set(COIN_SYMBOLS.values()))
    news_text = _format_news_for_llm(articles) if articles else "No urgent breaking news."

    # Format live market trends
    trends_lines = []
    if prices:
        for sym, info in prices.items():
            chg = info.get("change_24h_pct", 0.0)
            prc = info.get("price_inr", 0)
            sign = "+" if chg >= 0 else ""
            trends_lines.append(f"• {sym}/INR: ₹{prc:,.2f} (24h Change: {sign}{chg}%)")
    market_trends_text = "\n".join(trends_lines) if trends_lines else "Market data unavailable."

    prompt = ANALYSIS_PROMPT_TEMPLATE.format(
        market_trends_text=market_trends_text,
        news_text=news_text,
        coins=", ".join(tracked_coins),
    )

    logger.info(f"[NVIDIA] Sending market trends + {len(articles)} articles to {NVIDIA_MODEL}...")

    try:
        client = OpenAI(
            base_url=NVIDIA_BASE_URL,
            api_key=NVIDIA_API_KEY,
        )
        response = client.chat.completions.create(
            model=NVIDIA_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=NVIDIA_MAX_TOKENS,
            temperature=NVIDIA_TEMPERATURE,
            timeout=8.0,
        )

        raw_response = response.choices[0].message.content
        logger.debug(f"[NVIDIA] Raw response: {raw_response[:500]}")

        signals = _extract_json(raw_response)
        validated = _validate_signals(signals, tracked_coins)

        logger.info(f"[NVIDIA] Extracted {len(validated)} valid signals from LLM")
        return validated

    except Exception as e:
        logger.error(f"[NVIDIA] LLM call failed: {e}")
        return []


def _validate_signals(signals: list, tracked_coins: list) -> list[dict]:
    """Validate and filter LLM signals for safety."""
    valid_signals = []
    valid_sides = {"BUY", "SELL", "HOLD"}
    valid_urgency = {"HIGH", "MEDIUM", "LOW"}

    for s in signals:
        if not isinstance(s, dict):
            continue

        coin = str(s.get("coin", "")).upper().strip()
        signal = str(s.get("signal", "")).upper().strip()
        urgency = str(s.get("urgency", "LOW")).upper().strip()
        confidence = s.get("confidence", 0)
        reasoning = s.get("reasoning", "")

        # Validate fields
        if coin not in tracked_coins:
            logger.debug(f"[NVIDIA] Skipping unknown coin: {coin}")
            continue
        if signal not in valid_sides:
            logger.debug(f"[NVIDIA] Invalid signal for {coin}: {signal}")
            continue
        if urgency not in valid_urgency:
            urgency = "LOW"

        try:
            confidence = int(confidence)
        except (TypeError, ValueError):
            confidence = 0

        validated_signal = {
            "coin": coin,
            "signal": signal,
            "confidence": confidence,
            "urgency": urgency,
            "reasoning": str(reasoning),
            "news_basis": str(s.get("news_basis", "")),
            "actionable": (
                confidence >= MIN_CONFIDENCE_TO_TRADE
                and urgency in URGENCY_LEVELS_TO_TRADE
                and signal in ("BUY", "SELL")
            ),
        }
        valid_signals.append(validated_signal)

    return valid_signals


if __name__ == "__main__":
    # Quick test with mock data
    test_articles = [
        {
            "title": "BlackRock adds $1.2 billion in Bitcoin to its ETF holdings — largest single-day purchase",
            "source": "CoinDesk",
            "published_at": "2024-01-15T10:30:00Z",
            "coins": ["BTC"],
        },
        {
            "title": "Ethereum network congestion spikes as NFT project launches",
            "source": "The Block",
            "published_at": "2024-01-15T10:25:00Z",
            "coins": ["ETH"],
        },
    ]
    results = analyze_news(test_articles)
    for r in results:
        print(f"\n{'='*60}")
        print(f"Coin: {r['coin']} | Signal: {r['signal']} | Confidence: {r['confidence']}%")
        print(f"Urgency: {r['urgency']} | Actionable: {r['actionable']}")
        print(f"Reason: {r['reasoning']}")
