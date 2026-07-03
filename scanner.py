"""
TREND-PULLBACK Crypto Scanner (Binance Spot)
==============================================
Strategy: EMA50 > EMA200 trend + pullback to EMA50 + RSI 40-45 + green candle
          + rising volume.

Ye script EK BAAR chalta hai (single pass) — saare coins/timeframes check
karta hai, agar signal milta hai to Telegram par alert bhejta hai, phir
band ho jaata hai. Isko har 5 minute pe cron/GitHub Actions se dobara
chalaya jaata hai (24/7 automation ke liye).

Author: Claude (Anthropic) for beginner-friendly automated trading scanner
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

SYMBOLS = [
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "BNB/USDT",
    "XRP/USDT",
    "ADA/USDT",
    "DOGE/USDT",
]

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
    requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"})

def send_status_report(signals_found):
    text = f"🤖 <b>Scan Complete</b> ({datetime.now(timezone.utc).strftime('%H:%M')} UTC)\n\n"
    if signals_found:
        text += "\n".join(signals_found)
    else:
        text += "❌ No signals found this round."
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"})

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
    exchange = ccxt.bybit({"enableRateLimit": True})
    active_timeframes = [tf for tf in TIMEFRAMES if tf in get_active_timeframes()]
    signals_list = []

    for symbol in SYMBOLS:
        for tf in active_timeframes:
            try:
                df = add_indicators(fetch_candles(exchange, symbol, tf))
                signal = check_signal(df)
                if signal:
                    signals_list.append(f"✅ {symbol} ({tf}) - Entry: {signal['entry']:.6f}")
                    send_telegram_alert(symbol, tf, signal)
            except Exception as e:
                print(f"Error {symbol} {tf}: {e}")
            time.sleep(0.3)
    
    send_status_report(signals_list)

if __name__ == "__main__":
    run_scan()
