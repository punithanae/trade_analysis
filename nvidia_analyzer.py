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

# Initialize NVIDIA NIM client (OpenAI-compatible)
_client = OpenAI(
    base_url=NVIDIA_BASE_URL,
    api_key=NVIDIA_API_KEY,
)

SYSTEM_PROMPT = """You are an elite quantitative crypto trading analyst with 15 years of experience.
You analyze news and market sentiment to generate precise trading signals.

Your analysis is always:
1. Evidence-based (from the provided news only)
2. Risk-aware (prefer HOLD when uncertain)
3. Coin-specific (identify which exact coin is affected)

You must respond ONLY with a JSON array — no explanation, no markdown, no extra text."""

ANALYSIS_PROMPT_TEMPLATE = """Analyze the following {n} crypto news articles and for each coin that has clear trading implications, generate ONE trading signal.

NEWS ARTICLES:
{news_text}

TRACKED COINS: {coins}

For each coin with a clear signal, output a JSON object with EXACTLY these fields:
{{
  "coin": "BTC" (must be one of the tracked coins above),
  "signal": "BUY" or "SELL" or "HOLD",
  "confidence": 0-100 (integer, how confident you are),
  "urgency": "HIGH" or "MEDIUM" or "LOW",
  "reasoning": "One clear sentence explaining why",
  "news_basis": "Which headline(s) drove this signal"
}}

Rules:
- Only include coins where news is clearly bullish or bearish
- BUY = strong positive catalyst (major partnerships, bullish regulation, big institutional buy)
- SELL = strong negative catalyst (hack, ban, major sell-off, key person leaving, FUD confirmed)
- HOLD = unclear or mixed signals
- Confidence >= 70 means "act on this", < 70 means noise
- If no coins have clear signals, return an empty array []

Respond with ONLY a valid JSON array of signal objects. Example: [{{"coin":"BTC","signal":"BUY","confidence":82,"urgency":"HIGH","reasoning":"...","news_basis":"..."}}]"""


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


def analyze_news(articles: list[dict]) -> list[dict]:
    """
    Send news to NVIDIA NIM LLM and get trade signals.
    Returns list of validated signal dicts.
    """
    if not articles:
        logger.info("[NVIDIA] No articles to analyze")
        return []

    if not NVIDIA_API_KEY:
        logger.error("[NVIDIA] No API key set — cannot analyze news")
        return []

    tracked_coins = list(set(COIN_SYMBOLS.values()))
    news_text = _format_news_for_llm(articles)
    prompt = ANALYSIS_PROMPT_TEMPLATE.format(
        n=len(articles),
        news_text=news_text,
        coins=", ".join(tracked_coins),
    )

    logger.info(f"[NVIDIA] Sending {len(articles)} articles to {NVIDIA_MODEL}...")

    try:
        response = _client.chat.completions.create(
            model=NVIDIA_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=NVIDIA_MAX_TOKENS,
            temperature=NVIDIA_TEMPERATURE,
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
