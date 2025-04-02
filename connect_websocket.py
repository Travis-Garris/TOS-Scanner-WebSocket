import websocket
import json
import threading
import time
import numpy as np
from collections import defaultdict, deque

API_KEY = "j8buZA_wTOJyoBAE7xYGm1SI4DtjHhrt"
WS_URL = "wss://delayed.polygon.io/stocks"

# Constants
RANGE_PERCENT = 0.005
RSI_LENGTH = 14
STOCH_LENGTH = 5
AVG_LENGTH = 8
OVERBOUGHT = 92
OVERSOLD = 8
MAX_BARS = 100
symbols = ["AAPL", "MSFT", "GOOGL"]

range_bars = {}
completed_bars = defaultdict(lambda: deque(maxlen=MAX_BARS))
sve_values = {}
sve_history = defaultdict(lambda: deque(maxlen=2))

def run_scan(filters, min_price=0, max_price=10000):
    results = []
    for symbol, sve in sve_values.items():
        price = range_bars.get(symbol, {}).get("last_price")
        if not price or not (min_price <= price <= max_price):
            continue
        for f in filters:
            if evaluate_sve_condition(symbol, sve, f):
                results.append({
                    "symbol": symbol,
                    "price": round(price, 2),
                    "sve": round(sve, 2),
                    "condition": f["condition"]
                })
                break
    return results

def evaluate_sve_condition(symbol, current_sve, f):
    prev_values = list(sve_history[symbol])
    if len(prev_values) < 2:
        return False
    prev_sve = prev_values[-2]
    cond = f["condition"]
    ob, os = f["overbought"], f["oversold"]
    return (
        (cond == "crosses_above_oversold" and prev_sve < os and current_sve >= os) or
        (cond == "crosses_below_overbought" and prev_sve > ob and current_sve <= ob) or
        (cond == "rising" and current_sve > prev_sve) or
        (cond == "falling" and current_sve < prev_sve) or
        (cond == "greater_than_overbought" and current_sve > ob) or
        (cond == "less_than_oversold" and current_sve < os)
    )

def compute_rsi(prices, length):
    deltas = np.diff(prices)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gains[-length:])
    avg_loss = np.mean(losses[-length:])
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1 + rs))

def compute_sve_stoch_rsi(symbol):
    prices = list(completed_bars[symbol])
    if len(prices) < RSI_LENGTH + STOCH_LENGTH + AVG_LENGTH:
        return None
    rsi_series = [compute_rsi(prices[i:i+RSI_LENGTH+1], RSI_LENGTH)
                  for i in range(len(prices) - RSI_LENGTH)]
    if len(rsi_series) < STOCH_LENGTH + AVG_LENGTH:
        return None
    hi_rsi = max(rsi_series[-STOCH_LENGTH:])
    lo_rsi = min(rsi_series[-STOCH_LENGTH:])
    rsi_minus_low = np.array(rsi_series[-AVG_LENGTH:]) - lo_rsi
    denominator = 0.1 + np.mean([hi_rsi - lo_rsi] * AVG_LENGTH)
    stoch_rsi = (np.mean(rsi_minus_low) / denominator) * 100
    sve_history[symbol].append(stoch_rsi)
    sve_values[symbol] = stoch_rsi
    return stoch_rsi

def simulate_range_bar(symbol, close_price):
    bar = range_bars.get(symbol, {
        "start_price": close_price,
        "high": close_price,
        "low": close_price,
        "last_price": close_price
    })

    bar["high"] = max(bar["high"], close_price)
    bar["low"] = min(bar["low"], close_price)
    bar["last_price"] = close_price

    range_val = bar["start_price"] * RANGE_PERCENT
    if abs(close_price - bar["start_price"]) >= range_val:
        completed_bars[symbol].append(close_price)
        sve = compute_sve_stoch_rsi(symbol)
        if sve is not None:
            print(f"[{symbol}] Range bar complete at ${close_price:.2f} → SVE: {sve:.2f}")
        range_bars[symbol] = {
            "start_price": close_price,
            "high": close_price,
            "low": close_price,
            "last_price": close_price
        }
    else:
        range_bars[symbol] = bar

def on_message(ws, message):
    print(message)
    try:
        events = json.loads(message)
        for event in events:
            if event.get("ev") == "A":
                symbol = event["sym"]
                close = event["c"]
                simulate_range_bar(symbol, close)
    except Exception as e:
        print("Error in on_message:", e)

def on_open(ws):
    print("WebSocket opened")
    ws.send(json.dumps({
        "action": "auth",
        "params": API_KEY
    }))
    # subs = ",".join([f"A.{sym}" for sym in symbols])
    ws.send(json.dumps({
        "action": "subscribe",
        "params": "A.*"
    }))

def on_error(ws, error):
    print("WebSocket error:", error)

def on_close(ws, *args):
    print("WebSocket closed")

def start_websocket():
    ws = websocket.WebSocketApp(
        WS_URL,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close
    )
    ws.run_forever()

# ✅ Launch WebSocket in background thread
def run_ws_thread():
    ws_thread = threading.Thread(target=start_websocket, daemon=True)
    ws_thread.start()
    print("WebSocket thread started.")

# Run this in your main script or Flask app startup
if __name__ == "__main__":
    run_ws_thread()

    # Simulate main app doing other work
    time.sleep(10)  # wait for some bars

    filters = [
        {
            "range_percent": 0.005,
            "rsi_length": RSI_LENGTH,
            "stoch_length": STOCH_LENGTH,
            "avg_length": AVG_LENGTH,
            "overbought": OVERBOUGHT,
            "oversold": OVERSOLD,
            "condition": "crosses_above_oversold"
        }
    ]

    while True:
        time.sleep(10)
        results = run_scan(filters, min_price=10, max_price=300)
        print("\nSCAN RESULTS:")
        for r in results:
            print(f"{r['symbol']:6} | ${r['price']:>6} | SVE: {r['sve']:>5} | {r['condition']}")