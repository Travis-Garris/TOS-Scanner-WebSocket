from flask import Flask, request, render_template
from connect_websocket import run_scan  # assuming you have scanner logic in scanner.py
import websocket
import json
import threading
import time
import numpy as np
from collections import defaultdict, deque

app = Flask(__name__)
API_KEY = "j8buZA_wTOJyoBAE7xYGm1SI4DtjHhrt"
WS_URL = "wss://delayed.polygon.io/stocks"

# Constants
RANGE_PERCENT = 0.5
RSI_LENGTH = 14
STOCH_LENGTH = 5
AVG_LENGTH = 8
OVERBOUGHT = 80
OVERSOLD = 20
MAX_BARS = 100
conditions = [
    "rising",
    "falling",
    "crosses_above_oversold",
    "crosses_below_overbought",
    "greater_than_overbought",
    "less_than_oversold"
]

range_bars = {}
completed_bars = defaultdict(lambda: deque(maxlen=MAX_BARS))
sve_values = {}
sve_history = defaultdict(lambda: deque(maxlen=2))

def run_scan(filters, min_price=0, max_price=10000):
    results = []
    print("Run scan", sve_values)
    for symbol, sve in sve_values.items():
        print("Symbol, SVE", symbol, sve)
        price = range_bars.get(symbol, {}).get("last_price")
        price_open = completed_bars[symbol][0] if len(completed_bars[symbol]) > 0 else price
        percent_change = ((price - price_open) / price_open) * 100 if price_open else 0
        print("Price", price)
        if not price or not (min_price <= price <= max_price):
            continue
        print("Passed run scan", symbol, sve)
        for idx, f in enumerate(filters, start=1):
            if f["enabled"] and evaluate_sve_condition(symbol, sve, f):
                results.append({
                    "symbol": symbol,
                    "price": round(price, 2),
                    "sve": round(sve, 2),
                    "percent_change": round(percent_change, 2),
                    "condition": f["condition"],
                    "config": f"Config {idx}"  # New: shows which config matched
                })
                break  # OR logic — match on first config
    return results

def evaluate_sve_condition(symbol, current_sve, f):
    prev_values = list(sve_history[symbol])
    if len(prev_values) < 2:
        return False
    prev_sve = prev_values[-2]
    cond = f["condition"]
    ob, os = f["overbought"], f["oversold"]
    print("Cond", cond, current_sve, prev_sve)
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
    # if len(prices) < RSI_LENGTH + STOCH_LENGTH + AVG_LENGTH:
    if len(prices) < 10:
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

    range_val = bar["start_price"] * RANGE_PERCENT / 100
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

# Websocket
def on_message(ws, message):
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

def run_ws_thread():
    ws_thread = threading.Thread(target=start_websocket, daemon=True)
    ws_thread.start()
    print("WebSocket thread started.")

@app.route("/", methods=["GET", "POST"])
def index():
    # Use hardcoded filters for now
    min_price = 0.1
    max_price = 900
    results = []
    config_data = {}

    if request.method == "POST":
        try:
            min_price = float(request.form.get("min_price", 0.1))
            max_price = float(request.form.get("max_price", 999))
        except ValueError:
            pass

        filters = []

        for i in range(1, 4):
            enabled = request.form.get(f"enable_{i}") == "on"
            condition = request.form.get(f"condition_{i}")
            range_percent = float(request.form.get(f"range_{i}", 0.5))
            rsi_length = int(request.form.get(f"rsi_{i}", 14))
            stoch_length = int(request.form.get(f"stoch_{i}", 5))
            avg_length = int(request.form.get(f"avg_{i}", 8))
            overbought = float(request.form.get(f"overbought_{i}", 80))
            oversold = float(request.form.get(f"oversold_{i}", 20))

            config_data[i] = {
                "enabled": enabled,
                "condition": condition,
                "range_percent": range_percent,
                "rsi_length": rsi_length,
                "stoch_length": stoch_length,
                "avg_length": avg_length,
                "overbought": overbought,
                "oversold": oversold
            }

            filters.append({
                "enabled": enabled,
                "range_percent": range_percent,
                "rsi_length": rsi_length,
                "stoch_length": stoch_length,
                "avg_length": avg_length,
                "overbought": overbought,
                "oversold": oversold,
                "condition": condition
            })

        results = run_scan(filters, min_price, max_price)
    else:
        # default prefill values for initial page load
        config_data = {
            1: {"enabled": True, "condition": "rising", "range_percent": 0.5, "rsi_length": 14, "stoch_length": 5, "avg_length": 8, "overbought": 80, "oversold": 20},
            2: {"enabled": False, "condition": "rising", "range_percent": 0, "rsi_length": 0, "stoch_length": 0, "avg_length": 0, "overbought": 0, "oversold": 0},
            3: {"enabled": False, "condition": "rising", "range_percent": 0, "rsi_length": 0, "stoch_length": 0, "avg_length": 0, "overbought": 0, "oversold": 0}
        }
    return render_template(
        "index.html",
        results=results,
        conditions=conditions,
        min_price=min_price,
        max_price=max_price,
        config_data=config_data
    )

if __name__ == "__main__":
    run_ws_thread()
    time.sleep(10)
    app.run(debug=True, use_reloader=True)
