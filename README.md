# nexus-price-bus

**Author:** Ridhaant Ajoy Thackur  
**License:** MIT  
**Python:** 3.9+

> A production-grade, multi-source financial market data bus using ZeroMQ PUB/SUB.  
> Aggregates NSE equity prices (yfinance), live crypto prices (Binance WebSocket),  
> and any custom feed onto a single named-topic bus — with atomic JSON fallback.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        PriceBus                             │
│                                                             │
│  ┌─────────────┐   ┌─────────────────┐                     │
│  │ EquityFeed  │   │   CryptoFeed    │                     │
│  │ (yfinance)  │   │ (Binance WS /   │                     │
│  │  batched    │   │  CoinGecko REST)│                     │
│  └──────┬──────┘   └────────┬────────┘                     │
│         │                   │                               │
│         └─────────┬─────────┘                               │
│                   ▼                                         │
│          ┌──────────────────┐                               │
│          │  _ZmqPublisher   │  tcp://127.0.0.1:28081        │
│          │  topic "equity"  │  ──────────────────────►      │
│          │  topic "crypto"  │  PriceSubscriber (any process)│
│          │  topic "all"     │                               │
│          └────────┬─────────┘                               │
│                   │ also writes                             │
│          ┌────────▼─────────┐                               │
│          │ _AtomicJsonWriter│  ./live_prices.json           │
│          │  (atomic write)  │  ◄── JSON fallback consumers  │
│          └──────────────────┘                               │
└─────────────────────────────────────────────────────────────┘
```

### Key design decisions

| Concern | Solution |
|---|---|
| **Rate limiting** | yfinance calls are batched (10 symbols/call) with deduplication — only changed prices fire publish events |
| **WebSocket drops** | Binance WS auto-reconnects with configurable delay; falls back to CoinGecko REST |
| **ZMQ unavailable** | Graceful degradation to atomic JSON writes — downstream code needs no change |
| **Concurrency** | Each feed runs in a daemon thread; shared state protected by `threading.Lock` |
| **Partial file reads** | JSON written via write-to-temp + `os.replace` (atomic on Linux & Windows) |

---

## Installation

```bash
pip install pyzmq yfinance websocket-client pytz
# optional (needed for equity feed)
pip install yfinance
```

Or install all at once:

```bash
pip install -r requirements.txt
```

---

## Quickstart

### Publisher

```python
from src.nexus_price_bus import PriceBus

bus = PriceBus(
    equity_symbols=["RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS"],
    crypto_symbols=["btcusdt", "ethusdt", "solusdt"],
)
bus.start()  # non-blocking

import time
time.sleep(60)
bus.stop()
```

### Subscriber (separate process)

```python
from src.nexus_price_bus import PriceSubscriber

def on_price(topic: str, data: dict) -> None:
    print(f"[{topic}]", data["prices"])

sub = PriceSubscriber(topics=["equity", "crypto"])
sub.subscribe(on_price)
sub.start()

import time; time.sleep(60)
sub.stop()
```

### JSON-only mode (no ZMQ)

```python
from src.nexus_price_bus import _AtomicJsonWriter

reader = _AtomicJsonWriter("live_prices.json")
snap = reader.read()
if snap:
    print(snap["equity_prices"])
```

---

## Configuration

All settings can be overridden via environment variables:

| Variable | Default | Description |
|---|---|---|
| `ZMQ_PUB_ADDR` | `tcp://127.0.0.1:28081` | ZMQ PUB bind address |
| `EQUITY_POLL_INTERVAL` | `1.0` | yfinance poll interval (seconds) |
| `CRYPTO_WS_RECONNECT_DELAY` | `5.0` | Binance WS reconnect backoff |
| `JSON_FALLBACK_PATH` | `live_prices.json` | Fallback JSON file path |

---

## Topics

| Topic | Payload |
|---|---|
| `equity` | `{"topic": "equity", "ts": "...", "prices": {"RELIANCE.NS": 2450.5, ...}}` |
| `crypto` | `{"topic": "crypto", "ts": "...", "prices": {"BTCUSDT": 67432.1, ...}}` |
| `all` | Merged snapshot of the latest update event |

---

## Running the example

```bash
python examples/example_publisher.py
```

## Running tests

```bash
pytest tests/ -v
```

---

## Project context

This library extracts and generalises the IPC layer from [AlgoStack](https://github.com/ridhaant/algostack) — a 30,000-line equity/crypto/commodity algorithmic trading system. The original `ipc_bus.py` and `price_service.py` modules are production-tested across 16 concurrent processes.

---

## License

MIT © 2025 Ridhaant Ajoy Thackur
