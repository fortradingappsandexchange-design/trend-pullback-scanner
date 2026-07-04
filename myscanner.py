"""
TREND-PULLBACK Crypto Scanner (MEXC Spot - 24/7 Continuous Loop Version)
==============================================
"""

import os
import time
from datetime import datetime, timezone, timedelta
import ccxt
import pandas as pd
import requests

# ======================================================================
# ============================ CONFIG =================================
# ======================================================================

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

MAX_COINS = 1000
TIMEFRAMES = ["15m"]

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

# RUN_DURATION: 5 Ghante 45 Minute (Takriban 20700 seconds) baad script exit karegi
RUN_DURATION = 20700 

# ======================================================================
# ========================= HELPER FUNCTIONS ===========================
# ======================================================================

def get_top_symbols(exchange, limit=MAX_COINS):
    print(f"Fetching Top {limit} symbols by 24h Volume...")
    try:
        tickers = exchange.fetch_tickers()
        usdt_pairs = []
        for symbol, ticker in tickers.items():
            if symbol.endswith('/USDT') and ':' not in symbol:
                vol = ticker.get('quoteVolume', 0)
                if vol is not None and vol > 0:
                    usdt_pairs.append({'symbol': symbol, 'volume': vol})
        usdt_pairs.sort(key=lambda x: x['volume'], reverse=True)
        return [pair['symbol'] for pair in usdt_pairs[:limit]]
    except Exception as e:
        print(f"Error fetching tickers: {e}")
        return ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT"]

def fetch_candles(exchange, symbol, timeframe):
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=CANDLE_LIMIT)
    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
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
    return rsi.where(avg_loss != 0, 100)

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
        
    swing_window = df.iloc[-(SWING_LOOKBACK + 1):-1]
    swing_low = swing_window["low"].min()
    
    return {
        "entry": float(last["close"]),
        "sl": float(swing_low * (1 - SL_BUFFER)),
        "tp1": float(last["close"] * (1 + TP1_PCT)),
        "tp2": float(last["close"] * (1 + TP2_PCT))
    }

def send_telegram_message(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram configuration missing!")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"Telegram error: {e}")

def send_telegram_alert(symbol, timeframe, signal):
    message = (
        f"🚨 *SIGNAL: {symbol}* 🚨\n"
        f"Timeframe: {timeframe}\n"
        f"Entry: {signal['entry']:.6f}\n"
        f"Stop Loss: {signal['sl']:.6f}\n"
        f"TP1: {signal['tp1']:.6f}"
    )
    send_telegram_message(message)

def send_status_report(signals_found, error_msg=None, coins_scanned=0):
    text = f"🤖 *15m Scanner Health Check* ({datetime.now(timezone.utc).strftime('%H:%M')} UTC)\n"
    text += f"🔍 Scanned: Top {coins_scanned} Coins\n\n"
    
    if error_msg:
        text += f"⚠️ *SCANNER ERROR:*\n{error_msg}\n\nGitHub Actions check karein!"
    elif signals_found:
        text += "✅ *Signals Found:*\n" + "\n".join(signals_found)
    else:
        text += "❌ No signals found in this 15m window.\n(Scanner active)"
    
    send_telegram_message(text)

def wait_until_next_15m_candle():
    """Agli 15-minute ki candle close (00, 15, 30, 45) hone tak precise wait karta hai"""
    now = datetime.now(timezone.utc)
    minutes_to_add = 15 - (now.minute % 15)
    target_time = now.replace(second=0, microsecond=0) + timedelta(minutes=minutes_to_add)
    
    wait_seconds = (target_time - datetime.now(timezone.utc)).total_seconds()
    if wait_seconds > 0:
        print(f"Waiting {wait_seconds:.2f} seconds until next candle close ({target_time.strftime('%H:%M')} UTC)...")
        time.sleep(wait_seconds)

# ======================================================================
# ============================ MAIN RUN ================================
# ======================================================================

def run_scan():
    script_start_time = time.time()
    exchange = ccxt.mexc({"enableRateLimit": True})
    
    print("Scanner Loop Started successfully!")
    
    while time.time() - script_start_time < RUN_DURATION:
        # Agli candle close hone ka wait karo
        wait_until_next_15m_candle()
        
        print(f"Starting Scan at {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC...")
        try:
            symbols_to_scan = get_top_symbols(exchange, limit=MAX_COINS)
            signals_list = []

            for symbol in symbols_to_scan:
                try:
                    df = add_indicators(fetch_candles(exchange, symbol, "15m"))
                    signal = check_signal(df)
                    if signal:
                        signals_list.append(f"✅ {symbol} (15m) - Entry: {signal['entry']:.6f}")
                        send_telegram_alert(symbol, "15m", signal)
                except Exception:
                    pass
                time.sleep(0.1) # Rate limit se bachne ke liye chota pause
            
            send_status_report(signals_list, coins_scanned=len(symbols_to_scan))

        except Exception as main_e:
            print(f"CRITICAL ERROR in loop: {main_e}")
            send_status_report(None, error_msg=str(main_e), coins_scanned=0)
            time.sleep(30) # Error ki surat mein thoda ruk jao

    print("5.75 Hours complete. Exiting cleanly to allow the next GitHub workflow to take over.")

if __name__ == "__main__":
    run_scan()
