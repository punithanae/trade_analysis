# ⚡ AI Crypto Algo Trading Bot
### NVIDIA NIM + Mudrex Futures + News Sentiment Analysis

> **Fully automated crypto trading bot** that analyzes crypto news every 5 minutes using NVIDIA's LLM API, generates trade signals, and executes them on Mudrex INR Futures — with built-in risk management.

---

## 🚀 Features

| Feature | Details |
|---------|---------|
| **10 Trading Pairs** | BTC, ETH, SOL, BNB, XRP, ADA, DOGE, AVAX, MATIC, LINK (all INR) |
| **AI-Powered Signals** | NVIDIA NIM (Llama 3.1 70B) analyzes news for BUY/SELL/HOLD signals |
| **News Sources** | CryptoPanic (primary) + CoinGecko Trending + Binance News (fallbacks) |
| **Risk Management** | 1.5% risk/trade, 5x leverage, auto SL/TP, daily loss limit |
| **Dry Run Mode** | Test the full pipeline without placing real orders |
| **Live Dashboard** | Beautiful terminal UI showing positions, signals, PnL |
| **Trade Logging** | Every signal and trade saved to `trade_log.json` |

---

## ⚠️ IMPORTANT DISCLAIMER

> **This bot trades REAL money. Crypto markets are highly volatile. You can lose your entire investment. This is NOT financial advice. Use at your own risk. Start with a very small amount to test.**

---

## 📋 Requirements

- Python 3.11+
- Mudrex account with API access (KYC + 2FA required)
- NVIDIA NIM API key (free at [build.nvidia.com](https://build.nvidia.com))
- CryptoPanic API key (free at [cryptopanic.com](https://cryptopanic.com/developers/api/))

---

## 🛠️ Setup

### Step 1: Install Dependencies
```bash
cd trading_bot
pip install -r requirements.txt
```

### Step 2: Configure API Keys
```bash
# Copy the template
copy .env.example .env

# Edit .env and fill in your real keys:
#   MUDREX_API_SECRET=your_key_here
#   NVIDIA_API_KEY=your_key_here  
#   CRYPTOPANIC_API_KEY=your_key_here
```

### Step 3: Test Connections
```bash
python bot.py --test
```
This will verify all 3 API connections work before any trading starts.

### Step 4: Dry Run (ALWAYS do this first!)
```bash
python bot.py --dry-run
```
Watch for 30+ minutes. Check that:
- ✅ News is being fetched every 5 min
- ✅ NVIDIA LLM is generating signals
- ✅ Dashboard shows wallet balance
- ✅ Signals look reasonable (no crazy trades)

### Step 5: Go Live (when ready)
```bash
python bot.py
```

---

## 📊 How It Works

```
Every 5 Minutes:
  1. 📰 Fetch latest crypto news (CryptoPanic + fallbacks)
  2. 🤖 Send news to NVIDIA Llama 3.1 70B for analysis
  3. 📊 LLM returns: { coin, signal, confidence, urgency, reasoning }
  4. ✅ Filter: confidence >= 70% AND urgency = HIGH or MEDIUM
  5. 💰 Calculate position size (1.5% wallet risk × 5x leverage)
  6. 📈 Place MARKET order on Mudrex INR Futures
  7. 🛑 Set Stop Loss (-2% from entry)
  8. 🎯 Set Take Profit (+4% from entry)

Every 1 Minute:
  - Monitor open positions
  - Auto-close if SL/TP hit (safety net)

Every 30 Seconds:
  - Refresh live dashboard
```

---

## ⚙️ Configuration

Edit `config.py` or add to `.env` to customize:

| Setting | Default | Description |
|---------|---------|-------------|
| `RISK_PER_TRADE` | `1.5` | % of wallet risked per trade |
| `LEVERAGE` | `5` | Futures leverage multiplier |
| `MAX_POSITIONS` | `3` | Max simultaneous open trades |
| `STOP_LOSS_PCT` | `2.0` | Stop loss % from entry |
| `TAKE_PROFIT_PCT` | `4.0` | Take profit % from entry |
| `MAX_DAILY_LOSS_PCT` | `5.0` | Bot pauses if daily loss exceeds this |
| `DRY_RUN` | `false` | Set to `true` to simulate only |

---

## 🔐 Security

- **Never** commit your `.env` file
- API keys are loaded via environment variables only
- `.gitignore` excludes all sensitive files
- Use minimum required permissions on Mudrex API key

---

## 📁 File Structure

```
trading_bot/
├── bot.py              # Main entry point & scheduler
├── config.py           # All settings & constants
├── news_fetcher.py     # CryptoPanic + fallback news sources
├── nvidia_analyzer.py  # NVIDIA NIM LLM analysis
├── mudrex_client.py    # Mudrex API wrapper
├── risk_manager.py     # Position sizing, SL/TP, daily limits
├── trade_executor.py   # Signal → order pipeline
├── dashboard.py        # Rich terminal UI
├── requirements.txt    # Python packages
├── .env.example        # API key template
├── .env                # Your actual keys (NEVER commit this)
├── trade_log.json      # Auto-generated trade history
└── bot.log             # Auto-generated debug log
```

---

## 🆘 Troubleshooting

**"Missing API keys"** → Copy `.env.example` to `.env` and fill all keys

**"Mudrex connection failed"** → Check your API secret is correct and 2FA is enabled

**"No news fetched"** → Check CryptoPanic key, or it'll use CoinGecko/Binance fallback

**"No signals generated"** → Normal during quiet markets; bot will wait for next cycle

**Dashboard not showing** → Install `rich`: `pip install rich`

---

*Built with ❤️ using NVIDIA NIM + Mudrex API + Python*
