"""
TREND-PULLBACK Crypto Scanner (MEXC Spot)
==============================================
Strategy: EMA50 > EMA200 trend + pullback to EMA50 + RSI 40-45 + green candle
          + rising volume.

Note: Switched to MEXC exchange. Dynamic Top Volume Coins added.
"""

import os
import time
from datetime import datetime, timezone

import ccxt
import pandas as pd
import requests

# ======================================================================
# ============================ CONFIG =================================
# ======================================================================

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "PASTE_YOUR_BOT_TOKEN_HERE")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "PASTE_YOUR_CHAT_ID_HERE")

# ⚡ Nayi Setting: Kitne top coins scan karne hain? (GitHub ke liye 150 best hai, 1000 API limit hit karega)
MAX_COINS = 150  

TIMEFRAMES = ["5m", "15m"]

EMA_FAST = 50
EMA_SLOW = 200
RSI_LEN = 14
RSI_MIN = 40
RSI_MAX = 45

PULLBACK_TOLERANCE_UP = 0.002
PULLBACK_TOLERANCE_DOWN = 0.005

SWING_LOOKBACK = 20
SL_BUFFER = 0.01
TP1_PCT = 0.015
TP2_PCT = 0.02

CANDLE_LIMIT = 300

# ======================================================================
# ========================= HELPER FUNCTIONS ===========================
# ======================================================================

def get_top_symbols(exchange, limit=MAX_COINS):
    """MEXC se sab se zyada 24h volume walay USDT spot pairs fetch karega"""
    print(f"Fetching Top {limit} symbols by 24h Volume...")
    tickers = exchange.fetch_tickers()
    usdt_pairs = []
    
    for symbol, ticker in tickers.items():
        # Sirf Spot USDT pairs lein, margin/futures nahi
        if symbol.endswith('/USDT') and ':' not in symbol:
            vol = ticker.get('quoteVolume', 0)
            if vol is not None and vol > 0:
                usdt_pairs.append({'symbol': symbol, 'volume': vol})
                
    # Volume ke hisaab se descending order mein sort karein
    usdt_pairs.sort(key=lambda x: x['volume'], reverse=True)
    
    # Top N coins filter karein
    top_symbols = [pair['symbol'] for pair in usdt_pairs[:limit]]
    return top_symbols

def fetch_candles(exchange, symbol, timeframe):
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=CANDLE_LIMIT)
    df = pd.DataFrame(
        ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"]
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.iloc[:-1].reset_index(drop=True)
    return df

def calculate_ema(series, length):
    return series.ewm(span=length, adjust=False).mean()

def calculate_rsi(series, length=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.where(avg_loss != 0, 100)
    return rsi

def add_indicators(df):
    df["ema50"] = calculate_ema(df["close"], EMA_FAST)
    df["ema200"] = calculate_ema(df["close"], EMA_SLOW)
    df["rsi"] = calculate_rsi(df["close"], RSI_LEN)
    return df

def check_signal(df):
    if len(df) < EMA_SLOW + 5:
        return None
    last = df.iloc[-1]
    prev = df.iloc[-2]
    if pd.isna(last["ema200"]) or pd.isna(last["ema50"]) or pd.isna(last["rsi"]):
        return None
    trend_ok = (last["close"] > last["ema200"]) and (last["ema50"] > last["ema200"])
    if not trend_ok:
        return None
    upper_bound = last["ema50"] * (1 + PULLBACK_TOLERANCE_UP)
    lower_bound = last["ema50"] * (1 - PULLBACK_TOLERANCE_DOWN)
    pullback_ok = lower_bound <= last["low"] <= upper_bound
    if not pullback_ok:
        return None
    rsi_ok = RSI_MIN <= last["rsi"] <= RSI_MAX
    if not rsi_ok:
        return None
    bullish_ok = last["close"] > last["open"]
    if not bullish_ok:
        return None
    volume_ok = last["volume"] > prev["volume"]
    if not volume_ok:
        return None
    prev_was_red = prev["close"] < prev["open"]
    swing_window = df.iloc[-(SWING_LOOKBACK + 1):-1]
    swing_low = swing_window["low"].min()
    return {
        "entry": float(last["close"]),
        "sl": float(swing_low * (1 - SL_BUFFER)),
        "tp1": float(last["close"] * (1 + TP1_PCT)),
        "tp2": float(last["close"] * (1 + TP2_PCT)),
        "rsi": float(last["rsi"]),
        "ema50": float(last["ema50"]),
        "ema200": float(last["ema200"]),
        "candle_time": last["timestamp"],
        "prev_was_red": bool(prev_was_red),
    }

def send_telegram_alert(symbol, timeframe, signal):
    message = (
        f"🚨 <b>SIGNAL: {symbol}</b> 🚨\n"
        f"Timeframe: {timeframe}\n"
        f"Entry: {signal['entry']:.6f}\n"
        f"Stop Loss: {signal['sl']:.6f}\n"
        f"TP1: {signal['tp1']:.6f}"
    )
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"})
    except Exception as e:
        print(f"Telegram Alert Error: {e}")

def send_status_report(signals_found, error_msg=None, coins_scanned=0):
    text = f"🤖 <b>Scanner Health Check</b> ({datetime.now(timezone.utc).strftime('%H:%M')} UTC)\n"
    text += f"🔍 Scanned: Top {coins_scanned} Coins\n\n"
    
    if error_msg:
        text += f"⚠️ <b>SCANNER ERROR:</b>\n{error_msg}\n\nGitHub Actions check karein!"
    elif signals_found:
        text += "✅ <b>Signals Found:</b>\n" + "\n".join(signals_found)
    else:
        text += "❌ No signals found.\n(Scanner is active and running perfectly)"
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"})
    except Exception as e:
        print(f"Telegram Report Error: {e}")

def get_active_timeframes():
    now = datetime.now(timezone.utc)
    active = ["5m"]
    if now.minute % 15 == 0:
        active.append("15m")
    return active

# ======================================================================
# ============================ MAIN RUN ================================
# ======================================================================

def run_scan():
    try:
        exchange = ccxt.mexc({"enableRateLimit": True})
        print(f"DEBUG: Exchange initialized as {exchange.id}")
        
        # Dynamically fetch top symbols instead of hardcoded list
        symbols_to_scan = get_top_symbols(exchange, limit=MAX_COINS)
        active_timeframes = [tf for tf in TIMEFRAMES if tf in get_active_timeframes()]
        signals_list = []

        for symbol in symbols_to_scan:
            for tf in active_timeframes:
                try:
                    df = add_indicators(fetch_candles(exchange, symbol, tf))
                    signal = check_signal(df)
                    if signal:
                        signals_list.append(f"✅ {symbol} ({tf}) - Entry: {signal['entry']:.6f}")
                        send_telegram_alert(symbol, tf, signal)
                except Exception as e:
                    # Choti errors ko ignore karega taake baki coins scan hote rahein
                    pass
                
                # API Limit bachane ke liye chhota sa pause
                time.sleep(0.1) 
        
        send_status_report(signals_list, coins_scanned=len(symbols_to_scan))

    except Exception as main_e:
        print(f"CRITICAL ERROR: {main_e}")
        send_status_report(None, error_msg=str(main_e), coins_scanned=0)

if __name__ == "__main__":
    run_scan()
