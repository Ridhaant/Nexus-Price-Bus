"""
example_publisher.py
====================
Author  : Ridhaant Ajoy Thackur
Project : nexus-price-bus

Start the full price bus (equity + crypto) and print live prices.
Press Ctrl+C to stop.
"""

import time
import logging
from nexus_price_bus import PriceBus, PriceSubscriber

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

# ── Publisher side ──────────────────────────────────────────────────────────
bus = PriceBus(
    equity_symbols=["RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "WIPRO.NS"],
    crypto_symbols=["btcusdt", "ethusdt", "solusdt"],
    zmq_addr="tcp://127.0.0.1:28081",
    json_fallback_path="live_prices.json",
)

# ── Subscriber side (runs in same process for demo) ─────────────────────────
def on_price_update(topic: str, data: dict) -> None:
    ts = data.get("ts", "?")
    prices = data.get("prices", {})
    print(f"[{ts}] [{topic.upper():<6}] {prices}")

sub = PriceSubscriber(addr="tcp://127.0.0.1:28081", topics=["equity", "crypto"])
sub.subscribe(on_price_update)

print("Starting PriceBus... press Ctrl+C to stop.")
bus.start()
sub.start()

try:
    while True:
        snap = bus.get_snapshot()
        n_equity = len(snap.get("equity", {}))
        n_crypto = len(snap.get("crypto", {}))
        print(f"[Snapshot] equity={n_equity} symbols, crypto={n_crypto} symbols")
        time.sleep(10)
except KeyboardInterrupt:
    print("\nStopping...")
    bus.stop()
    sub.stop()
    print("Done.")
