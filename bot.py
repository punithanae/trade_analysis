"""
bot.py — Main orchestrator for the AI Crypto Trading Bot.

Usage:
  python bot.py              # Live trading mode
  python bot.py --dry-run    # Simulate only (no real orders)
  python bot.py --test       # Test API connections and exit

Scheduler:
  - Every 5 min: Fetch news → NVIDIA analysis → Execute trades
  - Every 1 min: Monitor positions (SL/TP check)
  - Every 30 sec: Refresh dashboard
"""
import sys
import time
import argparse
import signal as sys_signal
from datetime import datetime, timezone
from loguru import logger
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED

# ─── Bot modules ───
import config
from config import (
    DRY_RUN,
    NEWS_FETCH_INTERVAL_SEC,
    POSITION_MONITOR_SEC,
    DASHBOARD_REFRESH_SEC,
    LOG_FILE,
    BOT_VERSION,
    MUDREX_API_SECRET,
    NVIDIA_API_KEY,
)
from news_fetcher import fetch_news
from nvidia_analyzer import analyze_news
from mudrex_client import get_wallet_balance, get_open_positions
from risk_manager import RiskManager
from trade_executor import TradeExecutor
from dashboard import state as dash_state, render_dashboard, print_startup_banner

# ─── Global instances ───
risk_manager = RiskManager()
executor = TradeExecutor(risk_manager)
scheduler = BackgroundScheduler(timezone="UTC")
running = True


# ─────────────────────────────────────────────
#  SETUP
# ─────────────────────────────────────────────

def setup_logging():
    """Configure structured logging."""
    logger.remove()
    logger.add(
        sys.stderr,
        level="INFO",
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
        colorize=True,
    )
    logger.add(
        LOG_FILE,
        level="DEBUG",
        rotation="10 MB",
        retention="7 days",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} | {message}",
        encoding="utf-8",
    )


def validate_api_keys():
    """Check that all required API keys are set."""
    missing = []
    if not MUDREX_API_SECRET:
        missing.append("MUDREX_API_SECRET")
    if not NVIDIA_API_KEY:
        missing.append("NVIDIA_API_KEY")

    if missing:
        logger.error(f"Missing required API keys in .env: {', '.join(missing)}")
        logger.error("Copy .env.example to .env and fill in your keys.")
        return False

    logger.info("✅ All required API keys are set")
    return True


def test_connections():
    """Test all API connections and print results."""
    print("\n" + "="*60)
    print("🔧 TESTING API CONNECTIONS")
    print("="*60)

    # Test Mudrex
    print("\n[1/3] Testing Mudrex API...")
    wallet = get_wallet_balance()
    if wallet["available"] >= 0:
        print(f"  ✅ Mudrex OK — Balance: {wallet['available']} {wallet['currency']}")
    else:
        print("  ❌ Mudrex connection FAILED")

    # Test NVIDIA NIM
    print("\n[2/3] Testing NVIDIA NIM API...")
    try:
        from nvidia_analyzer import _client
        models = _client.models.list()
        print(f"  ✅ NVIDIA NIM OK — Connected to {config.NVIDIA_MODEL}")
    except Exception as e:
        print(f"  ❌ NVIDIA NIM FAILED: {e}")

    # Test News Fetcher
    print("\n[3/3] Testing News Fetcher...")
    try:
        articles = fetch_news()
        print(f"  ✅ News OK — Fetched {len(articles)} articles")
        for a in articles[:3]:
            print(f"     [{','.join(a['coins'])}] {a['title'][:60]}...")
    except Exception as e:
        print(f"  ❌ News Fetcher FAILED: {e}")

    print("\n" + "="*60)
    print("✅ Connection tests complete")
    print("="*60 + "\n")


# ─────────────────────────────────────────────
#  SCHEDULED TASKS
# ─────────────────────────────────────────────

def job_news_and_trade():
    """
    MAIN 5-MINUTE JOB:
    1. Fetch crypto news
    2. Analyze with NVIDIA NIM LLM
    3. Execute actionable trade signals
    """
    global running
    try:
        dash_state.cycle_count += 1
        dash_state.bot_status = "ANALYZING"
        dash_state.last_cycle_time = datetime.now(timezone.utc)
        dash_state.next_cycle_in = NEWS_FETCH_INTERVAL_SEC

        logger.info(f"\n{'─'*50}")
        logger.info(f"🔄 CYCLE #{dash_state.cycle_count} — {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}")
        logger.info(f"{'─'*50}")

        # Step 1: Fetch News
        logger.info("📰 Step 1: Fetching news...")
        articles = fetch_news()
        dash_state.last_news = articles

        if not articles:
            logger.warning("No news articles fetched — skipping analysis")
            dash_state.bot_status = "RUNNING"
            return

        logger.info(f"✅ Fetched {len(articles)} articles")

        # Step 2: Analyze with NVIDIA NIM
        logger.info(f"🤖 Step 2: Sending to NVIDIA NIM ({config.NVIDIA_MODEL})...")
        signals = analyze_news(articles)
        dash_state.last_signals = signals

        if not signals:
            logger.info("No signals generated — market is quiet")
            dash_state.bot_status = "RUNNING"
            return

        logger.info(f"📊 Got {len(signals)} signals, {sum(1 for s in signals if s.get('actionable'))} actionable")
        for s in signals:
            actionable_icon = "✅" if s.get("actionable") else "⬜"
            logger.info(
                f"  {actionable_icon} {s['coin']} → {s['signal']} "
                f"({s['confidence']}% confidence, {s['urgency']} urgency)"
            )

        # Step 3: Execute Trades
        dash_state.bot_status = "EXECUTING"
        logger.info("💹 Step 3: Processing signals...")
        executed = executor.process_signals(signals)

        if executed:
            logger.info(f"🚀 Executed {len(executed)} trade(s) this cycle")
        else:
            logger.info("💤 No trades executed this cycle")

        # Update dashboard state
        _refresh_account_data()
        dash_state.bot_status = "DRY RUN" if DRY_RUN else "RUNNING"

    except Exception as e:
        logger.error(f"[MainJob] Unexpected error: {e}", exc_info=True)
        dash_state.bot_status = "ERROR"
        dash_state.errors_today += 1


def job_monitor_positions():
    """
    1-MINUTE JOB: Monitor open positions for SL/TP.
    Safety net in case exchange orders don't trigger.
    """
    try:
        executor.monitor_and_close_positions()
        _refresh_account_data()
    except Exception as e:
        logger.error(f"[MonitorJob] Error: {e}")


def job_refresh_dashboard():
    """30-SECOND JOB: Refresh the live terminal dashboard."""
    try:
        dash_state.next_cycle_in = max(
            0,
            dash_state.next_cycle_in - DASHBOARD_REFRESH_SEC,
        )
        dash_state.recent_trades = executor.get_recent_trades(10)
        dash_state.risk_stats = risk_manager.get_stats()
        render_dashboard()
    except Exception as e:
        logger.error(f"[DashboardJob] Error: {e}")


def _refresh_account_data():
    """Helper to refresh wallet and position data in dashboard state."""
    try:
        wallet = get_wallet_balance()
        dash_state.wallet_available = wallet["available"]
        dash_state.wallet_balance = wallet["total"]
        dash_state.open_positions = get_open_positions()
        dash_state.risk_stats = risk_manager.get_stats()
    except Exception as e:
        logger.warning(f"[Refresh] Could not refresh account data: {e}")


def job_daily_reset():
    """Midnight UTC: Reset daily stats."""
    risk_manager.reset_daily_stats()
    logger.info("🌅 New trading day — daily stats reset")


# ─────────────────────────────────────────────
#  SCHEDULER EVENTS
# ─────────────────────────────────────────────

def on_job_error(event):
    """Log scheduler job errors."""
    if event.exception:
        logger.error(f"[Scheduler] Job {event.job_id} failed: {event.exception}")
        dash_state.errors_today += 1


# ─────────────────────────────────────────────
#  GRACEFUL SHUTDOWN
# ─────────────────────────────────────────────

def handle_shutdown(sig, frame):
    """Handle Ctrl+C gracefully."""
    global running
    logger.info("\n🛑 Shutdown signal received — stopping bot...")
    running = False
    scheduler.shutdown(wait=False)
    logger.info("✅ Bot stopped cleanly. Trade log saved.")
    sys.exit(0)


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="AI Crypto Trading Bot")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Simulate trades without real orders",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        default=False,
        help="Test API connections and exit",
    )
    args = parser.parse_args()

    # Apply CLI flags
    if args.dry_run:
        config.DRY_RUN = True
        import trade_executor
        trade_executor.DRY_RUN = True

    setup_logging()
    print_startup_banner()

    logger.info(f"🤖 AI Crypto Trading Bot v{BOT_VERSION} starting...")
    logger.info(f"Mode: {'DRY RUN' if config.DRY_RUN else 'LIVE TRADING'}")

    # Validate keys
    if not validate_api_keys():
        sys.exit(1)

    # Test mode
    if args.test:
        test_connections()
        sys.exit(0)

    # Register shutdown handler
    sys_signal.signal(sys_signal.SIGINT, handle_shutdown)
    sys_signal.signal(sys_signal.SIGTERM, handle_shutdown)

    # Initial data load
    logger.info("📊 Loading initial account data...")
    _refresh_account_data()

    # ─── Schedule Jobs ───
    scheduler.add_listener(on_job_error, EVENT_JOB_ERROR)

    # Main 5-min trading cycle
    scheduler.add_job(
        job_news_and_trade,
        "interval",
        seconds=NEWS_FETCH_INTERVAL_SEC,
        id="news_and_trade",
        name="News Analysis & Trading",
        next_run_time=datetime.now(timezone.utc),  # Run immediately on start
    )

    # 1-min position monitor
    scheduler.add_job(
        job_monitor_positions,
        "interval",
        seconds=POSITION_MONITOR_SEC,
        id="monitor_positions",
        name="Position Monitor",
    )

    # 30-sec dashboard refresh
    scheduler.add_job(
        job_refresh_dashboard,
        "interval",
        seconds=DASHBOARD_REFRESH_SEC,
        id="refresh_dashboard",
        name="Dashboard Refresh",
    )

    # Daily reset at midnight UTC
    scheduler.add_job(
        job_daily_reset,
        "cron",
        hour=0,
        minute=0,
        id="daily_reset",
        name="Daily Reset",
    )

    scheduler.start()

    logger.info("✅ Bot started! Scheduler running.")
    logger.info(f"📅 Jobs: News every {NEWS_FETCH_INTERVAL_SEC//60}min | Monitor every {POSITION_MONITOR_SEC}s | Dashboard every {DASHBOARD_REFRESH_SEC}s")
    logger.info("Press Ctrl+C to stop the bot")

    # Keep main thread alive
    try:
        while running:
            time.sleep(1)
    except KeyboardInterrupt:
        handle_shutdown(None, None)


if __name__ == "__main__":
    main()
