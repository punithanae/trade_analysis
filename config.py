"""
config.py — Central configuration for the AI Trading Bot
All tunable parameters live here.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────
#  API CREDENTIALS
# ─────────────────────────────────────────────
MUDREX_API_SECRET      = os.getenv("MUDREX_API_SECRET", "")
NVIDIA_API_KEY         = os.getenv("NVIDIA_API_KEY", "")

# ─────────────────────────────────────────────
#  MUDREX API
# ─────────────────────────────────────────────
MUDREX_BASE_URL        = "https://trade.mudrex.com/fapi/v1"
MUDREX_TRADE_CURRENCY  = "INR"          # Using INR-margined futures

# ─────────────────────────────────────────────
#  NVIDIA NIM API (OpenAI-compatible)
# ─────────────────────────────────────────────
NVIDIA_BASE_URL        = "https://integrate.api.nvidia.com/v1"
NVIDIA_MODEL           = "meta/llama-3.1-70b-instruct"
NVIDIA_MAX_TOKENS      = 512
NVIDIA_TEMPERATURE     = 0.1            # Low temp = deterministic, factual

# ─────────────────────────────────────────────
#  TRADING PAIRS (Top 10 by Mudrex INR volume)
# ─────────────────────────────────────────────
TRADING_PAIRS = [
    "BTC/INR",
    "ETH/INR",
    "SOL/INR",
    "BNB/INR",
    "XRP/INR",
    "ADA/INR",
    "DOGE/INR",
    "AVAX/INR",
    "MATIC/INR",
    "LINK/INR",
]

# Coin symbols for news filtering (maps pair → short symbol)
COIN_SYMBOLS = {
    "BTC/INR": "BTC",
    "ETH/INR": "ETH",
    "SOL/INR": "SOL",
    "BNB/INR": "BNB",
    "XRP/INR": "XRP",
    "ADA/INR": "ADA",
    "DOGE/INR": "DOGE",
    "AVAX/INR": "AVAX",
    "MATIC/INR": "MATIC",
    "LINK/INR": "LINK",
}

# Reverse: symbol → pair
SYMBOL_TO_PAIR = {v: k for k, v in COIN_SYMBOLS.items()}

# ─────────────────────────────────────────────
#  RISK MANAGEMENT
# ─────────────────────────────────────────────
RISK_PER_TRADE_PCT     = float(os.getenv("RISK_PER_TRADE", 1.5))   # % of wallet
LEVERAGE               = int(os.getenv("LEVERAGE", 5))
MAX_OPEN_POSITIONS     = int(os.getenv("MAX_POSITIONS", 3))
STOP_LOSS_PCT          = 2.0            # % below/above entry → stop loss triggers
TAKE_PROFIT_PCT        = 4.0            # % above/below entry → take profit triggers
MAX_DAILY_LOSS_PCT     = 5.0            # % of wallet — bot pauses if exceeded

# ─────────────────────────────────────────────
#  SIGNAL THRESHOLDS (LLM output filters)
# ─────────────────────────────────────────────
MIN_CONFIDENCE_TO_TRADE = 55            # Actively capture opportunities with confidence >= 55%
URGENCY_LEVELS_TO_TRADE = ["HIGH", "MEDIUM", "LOW"]

# ─────────────────────────────────────────────
#  SCHEDULER INTERVALS
# ─────────────────────────────────────────────
NEWS_FETCH_INTERVAL_SEC    = 300        # Every 5 minutes
POSITION_MONITOR_SEC       = 60         # Every 1 minute
DASHBOARD_REFRESH_SEC      = 30         # Every 30 seconds

# ─────────────────────────────────────────────
#  NEWS FETCHING (100% free RSS sources)
# ─────────────────────────────────────────────
# Sources: CoinDesk, CoinTelegraph, Decrypt, TheBlock, CryptoSlate,
#          BeInCrypto, Bitcoin Magazine, Google News RSS, CoinGecko, Binance
NEWS_MAX_ARTICLES   = 20               # Max news items to send to LLM per cycle
NEWS_LOOKBACK_MIN   = 15               # Look at last 15 minutes (RSS updates slower than APIs)

# ─────────────────────────────────────────────
#  MISC
# ─────────────────────────────────────────────
DRY_RUN            = os.getenv("DRY_RUN", "false").lower() == "true"
TRADE_LOG_FILE     = "trade_log.json"
LOG_FILE           = "bot.log"
BOT_VERSION        = "1.0.0"
