"""
mudrex_client.py — Complete Mudrex Futures API wrapper.
Handles all API calls with retry logic, error handling, and INR support.
"""
import time
import requests
from loguru import logger
from config import (
    MUDREX_API_SECRET,
    MUDREX_BASE_URL,
    MUDREX_TRADE_CURRENCY,
    LEVERAGE,
    SYMBOL_TO_PAIR,
)

HEADERS = {
    "Content-Type": "application/json",
    "X-Authentication": MUDREX_API_SECRET,
}

MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds


def _make_request(method: str, endpoint: str, payload: dict = None, params: dict = None) -> dict | None:
    """Generic request with retry logic."""
    url = f"{MUDREX_BASE_URL}{endpoint}"
    if MUDREX_TRADE_CURRENCY == "INR" and payload is not None:
        payload["trade_currency"] = "INR"

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.request(
                method=method,
                url=url,
                headers=HEADERS,
                json=payload,
                params=params,
                timeout=4,
            )
            response.raise_for_status()
            data = response.json()
            logger.debug(f"[Mudrex] {method} {endpoint} → {response.status_code}")
            return data

        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response else None
            logger.error(f"[Mudrex] HTTP {status} on {method} {endpoint}: {e}")
            if status in (400, 401, 403, 404):
                # Don't retry 4xx errors
                return None
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY * attempt)

        except requests.exceptions.RequestException as e:
            logger.error(f"[Mudrex] Network error (attempt {attempt}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY * attempt)

    return None


# ─────────────────────────────────────────────
#  WALLET & ACCOUNT
# ─────────────────────────────────────────────

def get_wallet_balance() -> dict:
    """
    Get INR/USDT futures wallet balance.
    Returns: {"available": float, "total": float, "currency": str}
    """
    params = {}
    if MUDREX_TRADE_CURRENCY == "INR":
        params["trade_currency"] = "INR"

    data = _make_request("GET", "/wallet/funds", params=params)
    if not data:
        return {"available": 0.0, "total": 0.0, "currency": MUDREX_TRADE_CURRENCY}

    # Parse Mudrex response structure
    try:
        # Mudrex returns numeric values as strings
        futures_data = data.get("futures", data)
        available = float(futures_data.get("available_balance", 0))
        total = float(futures_data.get("total_balance", available))
        return {"available": available, "total": total, "currency": MUDREX_TRADE_CURRENCY}
    except (KeyError, ValueError, TypeError) as e:
        logger.error(f"[Mudrex] Failed to parse wallet balance: {e} | raw: {data}")
        return {"available": 0.0, "total": 0.0, "currency": MUDREX_TRADE_CURRENCY}


def get_open_positions() -> list[dict]:
    """
    Get all open futures positions.
    Returns list of position dicts.
    """
    params = {}
    if MUDREX_TRADE_CURRENCY == "INR":
        params["trade_currency"] = "INR"

    data = _make_request("GET", "/position", params=params)
    if not data:
        return []

    positions = data if isinstance(data, list) else data.get("positions", [])
    parsed = []
    for pos in positions:
        try:
            size = float(pos.get("position_size", 0))
            if abs(size) < 1e-10:
                continue  # Skip empty positions

            parsed.append({
                "symbol": pos.get("asset_id", pos.get("symbol", "")),
                "side": "LONG" if size > 0 else "SHORT",
                "size": abs(size),
                "entry_price": float(pos.get("entry_price", 0)),
                "mark_price": float(pos.get("mark_price", 0)),
                "unrealized_pnl": float(pos.get("unrealized_pnl", 0)),
                "leverage": int(pos.get("leverage", LEVERAGE)),
                "liquidation_price": float(pos.get("liquidation_price", 0)),
            })
        except (KeyError, ValueError, TypeError) as e:
            logger.warning(f"[Mudrex] Could not parse position: {pos} | {e}")

    return parsed


def get_current_price(symbol: str) -> float | None:
    """
    Get current market price for a symbol.
    symbol: e.g., "BTC/INR" or "BTC"
    """
    # Normalize symbol format for Mudrex (may need adjustment based on actual API)
    asset_id = symbol.replace("/INR", "/INR").replace("/USDT", "/USDT")
    params = {"asset_id": asset_id}
    if MUDREX_TRADE_CURRENCY == "INR":
        params["trade_currency"] = "INR"

    data = _make_request("GET", "/price/ticker", params=params)
    if not data:
        return None

    try:
        price = float(data.get("price", data.get("last_price", 0)))
        return price if price > 0 else None
    except (KeyError, ValueError, TypeError) as e:
        logger.error(f"[Mudrex] Failed to parse price for {symbol}: {e}")
        return None


def get_kline_data(symbol: str, interval: str = "5m", limit: int = 50) -> list[dict]:
    """
    Get historical OHLCV kline data.
    interval: "1m", "5m", "15m", "1h", "4h", "1d"
    """
    params = {
        "asset_id": symbol,
        "interval": interval,
        "limit": limit,
    }
    if MUDREX_TRADE_CURRENCY == "INR":
        params["trade_currency"] = "INR"

    data = _make_request("GET", "/price/kline", params=params)
    if not data:
        return []

    klines = data if isinstance(data, list) else data.get("klines", [])
    return klines


# ─────────────────────────────────────────────
#  LEVERAGE & MARGIN
# ─────────────────────────────────────────────

def set_leverage(symbol: str, leverage: int = LEVERAGE) -> bool:
    """Set leverage for a trading pair. Returns True on success."""
    payload = {
        "leverage": leverage,
        "margin_type": "CROSSED",  # Cross margin (safer for most users)
    }
    data = _make_request("POST", f"/futures/{symbol}/leverage", payload=payload)
    if data is not None:
        logger.info(f"[Mudrex] Set leverage {leverage}x for {symbol}")
        return True
    return False


# ─────────────────────────────────────────────
#  ORDER PLACEMENT
# ─────────────────────────────────────────────

def place_market_order(symbol: str, side: str, quantity: float) -> dict | None:
    """
    Place a market order.
    side: "BUY" or "SELL"
    quantity: amount in base currency (e.g., BTC amount)
    Returns order response or None on failure.
    """
    payload = {
        "side": side.upper(),
        "type": "MARKET",
        "quantity": str(quantity),
    }
    data = _make_request("POST", f"/futures/{symbol}/order", payload=payload)
    if data:
        logger.info(
            f"[Mudrex] ✅ MARKET {side} {quantity} {symbol} → "
            f"Order ID: {data.get('order_id', 'N/A')}"
        )
    return data


def place_limit_order(symbol: str, side: str, quantity: float, price: float) -> dict | None:
    """
    Place a limit order.
    """
    payload = {
        "side": side.upper(),
        "type": "LIMIT",
        "quantity": str(quantity),
        "price": str(price),
    }
    data = _make_request("POST", f"/futures/{symbol}/order", payload=payload)
    if data:
        logger.info(
            f"[Mudrex] 📋 LIMIT {side} {quantity} {symbol} @ {price} → "
            f"Order ID: {data.get('order_id', 'N/A')}"
        )
    return data


def place_stop_loss_order(symbol: str, side: str, quantity: float, stop_price: float) -> dict | None:
    """
    Place a stop-loss order.
    side: "SELL" for long positions, "BUY" for short positions
    """
    payload = {
        "side": side.upper(),
        "type": "STOP_MARKET",
        "quantity": str(quantity),
        "stop_price": str(stop_price),
        "reduce_only": True,
    }
    data = _make_request("POST", f"/futures/{symbol}/order", payload=payload)
    if data:
        logger.info(f"[Mudrex] 🛑 Stop Loss set @ {stop_price} for {symbol}")
    return data


def place_take_profit_order(symbol: str, side: str, quantity: float, take_price: float) -> dict | None:
    """
    Place a take-profit order.
    """
    payload = {
        "side": side.upper(),
        "type": "TAKE_PROFIT_MARKET",
        "quantity": str(quantity),
        "stop_price": str(take_price),
        "reduce_only": True,
    }
    data = _make_request("POST", f"/futures/{symbol}/order", payload=payload)
    if data:
        logger.info(f"[Mudrex] 🎯 Take Profit set @ {take_price} for {symbol}")
    return data


def cancel_all_orders(symbol: str) -> bool:
    """Cancel all open orders for a symbol."""
    data = _make_request("DELETE", f"/futures/{symbol}/orders/all")
    return data is not None


def close_position(symbol: str, quantity: float, current_side: str) -> dict | None:
    """
    Close an open position by placing a market order in opposite direction.
    current_side: "LONG" or "SHORT"
    """
    close_side = "SELL" if current_side == "LONG" else "BUY"
    payload = {
        "side": close_side,
        "type": "MARKET",
        "quantity": str(quantity),
        "reduce_only": True,
    }
    data = _make_request("POST", f"/futures/{symbol}/order", payload=payload)
    if data:
        logger.info(f"[Mudrex] 🔴 Closed {current_side} position for {symbol}")
    return data


if __name__ == "__main__":
    # Test connection
    print("Testing Mudrex API connection...")
    balance = get_wallet_balance()
    print(f"Wallet Balance: {balance['available']} {balance['currency']}")

    positions = get_open_positions()
    print(f"Open Positions: {len(positions)}")
    for p in positions:
        print(f"  {p['symbol']} {p['side']} x{p['size']} @ {p['entry_price']} | PnL: {p['unrealized_pnl']}")
