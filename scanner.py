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

# Telegram credentials - environment variables se aayenge (safe way).
# Local testing ke liye niche fallback values daal sakte ho, but production
# me hamesha env variable / GitHub Secret use karo, code me hardcode mat karo.
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "PASTE_YOUR_BOT_TOKEN_HERE")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "PASTE_YOUR_CHAT_ID_HERE")

# Jo coins scan karne hain (high volume Binance Spot pairs)
SYMBOLS = [
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "BNB/USDT",
    "XRP/USDT",
    "ADA/USDT",
    "DOGE/USDT",
]

# Timeframes jo check karne hain
TIMEFRAMES = ["5m", "15m"]

# Indicator settings
EMA_FAST = 50
EMA_SLOW = 200
RSI_LEN = 14
RSI_MIN = 40
RSI_MAX = 45

# Pullback tolerance - "low EMA50 ko touch ya bahut close ho" define karne ke liye
# low, EMA50 se kitna upar ya niche allow hai (percentage me)
PULLBACK_TOLERANCE_UP = 0.002    # EMA50 se 0.2% upar tak allowed
PULLBACK_TOLERANCE_DOWN = 0.005  # EMA50 se 0.5% niche tak allowed

# Swing low / SL / TP settings
SWING_LOOKBACK = 20     # Stop loss ke liye last kitni candles me swing low dhundna hai
SL_BUFFER = 0.01        # Swing low se 1% aur niche SL
TP1_PCT = 0.015         # Entry se 1.5% upar TP1
TP2_PCT = 0.02          # Entry se 2% upar TP2

CANDLE_LIMIT = 300      # Kitni candles fetch karni hain (EMA200 ke liye 200+ chahiye)


# ======================================================================
# ========================= HELPER FUNCTIONS ===========================
# ======================================================================

def fetch_candles(exchange, symbol, timeframe):
    """Binance se OHLCV candles fetch karta hai aur DataFrame banata hai."""
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=CANDLE_LIMIT)
    df = pd.DataFrame(
        ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"]
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)

    # IMPORTANT: Sabse last candle abhi "forming" ho rahi hoti hai (complete
    # nahi hui). Humein sirf COMPLETED candles chahiye, isliye last row hata do.
    df = df.iloc[:-1].reset_index(drop=True)
    return df


def calculate_ema(series, length):
    """EMA (Exponential Moving Average) - pandas ke built-in ewm se."""
    return series.ewm(span=length, adjust=False).mean()


def calculate_rsi(series, length=14):
    """
    RSI (Relative Strength Index) - Wilder's smoothing method use karke,
    jo standard TradingView/Binance RSI jaisa hi result deta hai.
    """
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    # Jab avg_loss 0 ho (sirf upar hi gaya price), RSI = 100 hota hai
    rsi = rsi.where(avg_loss != 0, 100)
    return rsi


def add_indicators(df):
    """EMA50, EMA200 aur RSI14 calculate karke DataFrame me add karta hai."""
    df["ema50"] = calculate_ema(df["close"], EMA_FAST)
    df["ema200"] = calculate_ema(df["close"], EMA_SLOW)
    df["rsi"] = calculate_rsi(df["close"], RSI_LEN)
    return df


def check_signal(df):
    """
    5-Step entry logic check karta hai LAST COMPLETED candle par.
    Agar sab conditions match hoti hain to signal dict return karta hai,
    warna None return karta hai.
    """
    if len(df) < EMA_SLOW + 5:
        return None  # itni candles hi nahi hain abhi

    last = df.iloc[-1]   # last completed candle
    prev = df.iloc[-2]   # usse pehli candle

    if pd.isna(last["ema200"]) or pd.isna(last["ema50"]) or pd.isna(last["rsi"]):
        return None

    # ---- STEP 1: Trend Check ----
    # Close aur EMA50 dono STRICTLY EMA200 ke upar hone chahiye
    trend_ok = (last["close"] > last["ema200"]) and (last["ema50"] > last["ema200"])
    if not trend_ok:
        return None

    # ---- STEP 2: Pullback Check ----
    # Candle ka LOW, EMA50 ko touch kare ya bahut close ho
    upper_bound = last["ema50"] * (1 + PULLBACK_TOLERANCE_UP)
    lower_bound = last["ema50"] * (1 - PULLBACK_TOLERANCE_DOWN)
    pullback_ok = lower_bound <= last["low"] <= upper_bound
    if not pullback_ok:
        return None

    # ---- STEP 3: RSI Check ----
    rsi_ok = RSI_MIN <= last["rsi"] <= RSI_MAX
    if not rsi_ok:
        return None

    # ---- STEP 4: Candle Confirmation (Bullish/Green) ----
    bullish_ok = last["close"] > last["open"]
    if not bullish_ok:
        return None

    # ---- STEP 5: Volume Confirmation ----
    # Is green candle ka volume, pichli candle se zyada hona chahiye
    volume_ok = last["volume"] > prev["volume"]
    if not volume_ok:
        return None

    prev_was_red = prev["close"] < prev["open"]  # "ideally red" - sirf info ke liye

    # ---- Swing Low nikaalna (Stop Loss ke liye) ----
    swing_window = df.iloc[-(SWING_LOOKBACK + 1):-1]
    swing_low = swing_window["low"].min()

    entry_price = float(last["close"])
    stop_loss = float(swing_low * (1 - SL_BUFFER))
    tp1 = float(entry_price * (1 + TP1_PCT))
    tp2 = float(entry_price * (1 + TP2_PCT))

    return {
        "entry": entry_price,
        "sl": stop_loss,
        "tp1": tp1,
        "tp2": tp2,
        "rsi": float(last["rsi"]),
        "ema50": float(last["ema50"]),
        "ema200": float(last["ema200"]),
        "candle_time": last["timestamp"],
        "prev_was_red": bool(prev_was_red),
    }


def send_telegram_alert(symbol, timeframe, signal):
    """Telegram bot ke through formatted alert message bhejta hai."""
    prev_color_note = (
        "✅ Pichli candle RED thi (ideal setup)"
        if signal["prev_was_red"]
        else "⚠️ Pichli candle GREEN thi (thoda kam ideal, baaki sab conditions match hui)"
    )

    message = (
        "🚨 <b>TREND-PULLBACK SIGNAL</b> 🚨\n\n"
        f"<b>Coin:</b> {symbol}\n"
        f"<b>Timeframe:</b> {timeframe}\n"
        f"<b>Candle Time (UTC):</b> {signal['candle_time']}\n\n"
        f"📈 <b>Entry:</b> {signal['entry']:.6f}\n"
        f"🛑 <b>Stop Loss:</b> {signal['sl']:.6f}\n"
        f"🎯 <b>TP1 (+1.5%):</b> {signal['tp1']:.6f}\n"
        f"🎯 <b>TP2 (+2%):</b> {signal['tp2']:.6f}\n\n"
        f"RSI: {signal['rsi']:.2f}\n"
        f"EMA50: {signal['ema50']:.6f}\n"
        f"EMA200: {signal['ema200']:.6f}\n\n"
        f"{prev_color_note}\n\n"
        "⚠️ Ye sirf ek automated alert hai, apna risk management khud karo."
    )

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        r = requests.post(url, data=payload, timeout=15)
        if r.status_code != 200:
            print("Telegram error:", r.text)
        else:
            print(f"Telegram alert bhej diya: {symbol} {timeframe}")
    except Exception as e:
        print("Telegram send failed:", e)


def get_active_timeframes():
    """
    Duplicate alerts avoid karne ke liye:
    Script har 5 min pe chalti hai (cron), isliye 5m timeframe hamesha check
    hoga. Lekin 15m candle sirf har 15 min pe close hoti hai, isliye 15m
    timeframe sirf tab check karo jab current UTC minute 15 se divide ho
    (0, 15, 30, 45). Isse same 15m candle par baar baar alert nahi aayega.
    """
    now = datetime.now(timezone.utc)
    active = ["5m"]
    if now.minute % 15 == 0:
        active.append("15m")
    return active


# ======================================================================
# ============================ MAIN RUN ================================
# ======================================================================

def run_scan():
    exchange = ccxt.binance({"enableRateLimit": True})
    active_timeframes = [tf for tf in TIMEFRAMES if tf in get_active_timeframes()]

    print(f"[{datetime.now(timezone.utc)}] Scan shuru... Timeframes: {active_timeframes}")

    for symbol in SYMBOLS:
        for tf in active_timeframes:
            try:
                df = fetch_candles(exchange, symbol, tf)
                df = add_indicators(df)
                signal = check_signal(df)
                if signal:
                    print(f"✅ SIGNAL MILA: {symbol} {tf}")
                    send_telegram_alert(symbol, tf, signal)
                else:
                    print(f"❌ No signal: {symbol} {tf}")
            except Exception as e:
                print(f"⚠️ Error {symbol} {tf}: {e}")
            time.sleep(0.3)  # Binance rate-limit ke liye chhota pause

    print(f"[{datetime.now(timezone.utc)}] Scan complete.")


if __name__ == "__main__":
    run_scan()
