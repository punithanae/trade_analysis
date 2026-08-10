"""
api/trade.py — Vercel Serverless Function for the trading bot.

Vercel calls this endpoint every 5 minutes via cron (configured in vercel.json).
Each call runs ONE complete trading cycle:
  1. Fetch crypto news (free RSS)
  2. Send to NVIDIA NIM LLM for analysis
  3. Execute trade signals on Mudrex INR Futures

Environment variables are set in Vercel Dashboard → Settings → Environment Variables.
No .env file needed on Vercel — os.getenv() reads them automatically.
"""
import sys
import os
import json
import logging
from http.server import BaseHTTPRequestHandler
from datetime import datetime, timezone

# Add parent directory to path so we can import our bot modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set up basic logging (Vercel captures stdout)
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger(__name__)


def run_trading_cycle() -> dict:
    """
    Execute one complete trading cycle.
    Returns a summary dict with results.
    """
    result = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "ok",
        "articles_fetched": 0,
        "signals_generated": 0,
        "trades_executed": 0,
        "signals": [],
        "trades": [],
        "errors": [],
    }

    try:
        # ── Step 1: Fetch News & Live 24h Market Prices ───────────
        log.info("Step 1: Fetching news & live 24h market trends...")
        from news_fetcher import fetch_news, fetch_market_prices
        articles = fetch_news()
        prices = fetch_market_prices()
        result["articles_fetched"] = len(articles)
        result["market_prices_scanned"] = len(prices)
        log.info(f"Fetched {len(articles)} articles and {len(prices)} coin tickers")

        # ── Step 2: Analyze with NVIDIA NIM Quant AI ─────────────
        log.info("Step 2: Analyzing with NVIDIA NIM Quant AI...")
        from nvidia_analyzer import analyze_news
        signals = analyze_news(articles, prices)
        result["signals_generated"] = len(signals)
        result["signals"] = [
            {
                "coin": s["coin"],
                "signal": s["signal"],
                "confidence": s["confidence"],
                "urgency": s["urgency"],
                "reasoning": s["reasoning"],
                "actionable": s["actionable"],
            }
            for s in signals
        ]
        log.info(f"Got {len(signals)} signals, {sum(1 for s in signals if s.get('actionable'))} actionable")

        # ── Step 3: Execute Trades ──────────────────────────────
        log.info("Step 3: Executing trades...")
        from risk_manager import RiskManager
        from trade_executor import TradeExecutor

        risk = RiskManager()
        executor = TradeExecutor(risk)
        executed = executor.process_signals(signals)

        result["trades_executed"] = len(executed)
        result["trades"] = [
            {
                "id": t.get("id"),
                "pair": t.get("pair"),
                "side": t.get("side"),
                "quantity": t.get("quantity"),
                "entry_price": t.get("entry_price"),
                "sl_price": t.get("sl_price"),
                "tp_price": t.get("tp_price"),
                "status": t.get("status"),
                "reasoning": t.get("reasoning"),
            }
            for t in executed
        ]
        log.info(f"Executed {len(executed)} trades")

    except Exception as e:
        log.error(f"Trading cycle error: {e}", exc_info=True)
        result["status"] = "error"
        result["errors"].append(str(e))

    return result


class handler(BaseHTTPRequestHandler):
    """Vercel serverless handler."""

    def do_GET(self):
        """Handle GET request from Vercel cron."""
        log.info(f"[Vercel] Cron triggered at {datetime.now(timezone.utc).isoformat()}")

        # Security: verify this is a Vercel cron call (not a public request)
        vercel_cron = self.headers.get("x-vercel-cron", "")
        authorization = self.headers.get("authorization", "")
        cron_secret = os.getenv("CRON_SECRET", "")

        # Only enforce auth if CRON_SECRET is set
        if cron_secret and authorization != f"Bearer {cron_secret}":
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Unauthorized"}).encode())
            return

        # Run the trading cycle
        result = run_trading_cycle()

        # Respond with JSON summary
        response_body = json.dumps(result, indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)

    def log_message(self, format, *args):
        """Suppress default HTTP server logs (Vercel handles logging)."""
        log.info(f"[HTTP] {format % args}")
