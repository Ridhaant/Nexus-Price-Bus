"""
nexus_price_bus.py
==================
Author  : Ridhaant Ajoy Thackur
Project : nexus-price-bus
License : MIT

Multi-source financial market data bus using ZeroMQ PUB/SUB.

Aggregates prices from:
  - NSE equity  (yfinance polling, 1-second cadence)
  - Crypto      (Binance WebSocket, real-time)
  - Custom feed (any callable that returns {symbol: price})

Publishes on three named topics over a single ZMQ PUB socket:
  tcp://127.0.0.1:28081
  ├── "equity"    — NSE symbol prices
  ├── "crypto"    — Binance USDT prices
  └── "all"       — merged snapshot

Fallback: when ZMQ is unavailable, writes atomic JSON to ./live_prices.json
so downstream consumers work without touching the socket layer at all.

Usage (publisher):
    from nexus_price_bus import PriceBus
    bus = PriceBus(equity_symbols=["RELIANCE.NS", "TCS.NS", "INFY.NS"])
    bus.start()          # non-blocking; spawns background threads

Usage (subscriber):
    from nexus_price_bus import PriceSubscriber
    sub = PriceSubscriber(topics=["equity", "crypto"])
    sub.subscribe(callback=lambda topic, data: print(topic, data))
    sub.start()
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pytz

log = logging.getLogger("nexus_price_bus")

IST = pytz.timezone("Asia/Kolkata")

# ── ZMQ detection ──────────────────────────────────────────────────────────────
try:
    import zmq
    _ZMQ_OK = True
except ImportError:
    zmq = None  # type: ignore
    _ZMQ_OK = False
    log.warning("pyzmq not installed — falling back to JSON-only IPC mode.")

# ── WebSocket detection ────────────────────────────────────────────────────────
try:
    import websocket  # websocket-client
    _WS_OK = True
except ImportError:
    websocket = None  # type: ignore
    _WS_OK = False

# ── yfinance detection ─────────────────────────────────────────────────────────
try:
    import yfinance as yf
    _YF_OK = True
except ImportError:
    yf = None  # type: ignore
    _YF_OK = False
    log.warning("yfinance not installed — equity feed disabled.")


# ══════════════════════════════════════════════════════════════════════════════
# JSON FALLBACK — atomic write so readers never see a partial file
# ══════════════════════════════════════════════════════════════════════════════

class _AtomicJsonWriter:
    """Writes a JSON snapshot atomically (write-to-temp, then os.replace)."""

    def __init__(self, path: str = "live_prices.json") -> None:
        self._path = path
        self._lock = threading.Lock()

    def write(self, payload: dict) -> None:
        tmp = self._path + ".tmp"
        with self._lock:
            try:
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, separators=(",", ":"))
                os.replace(tmp, self._path)
            except OSError as exc:
                log.warning("JSON write failed: %s", exc)

    def read(self) -> Optional[dict]:
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            return None


# ══════════════════════════════════════════════════════════════════════════════
# ZMQ PUBLISHER
# ══════════════════════════════════════════════════════════════════════════════

class _ZmqPublisher:
    """
    Thin wrapper around zmq.PUSH / PUB socket.
    Messages are multipart: [topic_bytes, json_bytes].
    """

    def __init__(self, addr: str = "tcp://127.0.0.1:28081") -> None:
        self._addr = addr
        self._ctx: Optional[object] = None
        self._sock: Optional[object] = None
        self._lock = threading.Lock()
        self._connected = False

    def _ensure_connected(self) -> bool:
        if self._connected:
            return True
        if not _ZMQ_OK:
            return False
        try:
            self._ctx = zmq.Context.instance()
            self._sock = self._ctx.socket(zmq.PUB)
            self._sock.setsockopt(zmq.SNDHWM, 1000)
            self._sock.setsockopt(zmq.LINGER, 0)
            self._sock.bind(self._addr)
            self._connected = True
            log.info("ZMQ PUB socket bound on %s", self._addr)
            return True
        except zmq.ZMQError as exc:
            log.warning("ZMQ bind failed (%s) — JSON fallback active.", exc)
            return False

    def publish(self, topic: str, data: dict) -> None:
        with self._lock:
            if not self._ensure_connected():
                return
            try:
                payload = json.dumps(data, separators=(",", ":")).encode()
                self._sock.send_multipart(
                    [topic.encode(), payload], flags=zmq.NOBLOCK
                )
            except zmq.ZMQError:
                pass  # drop if no subscribers or HWM reached

    def close(self) -> None:
        with self._lock:
            if self._sock:
                self._sock.close(linger=0)
            self._connected = False


# ══════════════════════════════════════════════════════════════════════════════
# EQUITY FEED  (yfinance)
# ══════════════════════════════════════════════════════════════════════════════

class _EquityFeed:
    """
    Polls yfinance for real-time NSE prices.

    Batches ALL symbols into a single download() call to avoid rate-limiting.
    Uses a deduplication set so only changed prices trigger a publish event.
    """

    # yfinance is called in batches of this size to stay within rate limits
    _BATCH_SIZE = 10

    def __init__(
        self,
        symbols: List[str],
        on_update: Callable[[Dict[str, float]], None],
        interval_sec: float = 1.0,
    ) -> None:
        self._symbols = symbols
        self._on_update = on_update
        self._interval = interval_sec
        self._last_prices: Dict[str, float] = {}
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def start(self) -> None:
        if not _YF_OK:
            log.warning("yfinance unavailable — equity feed will not start.")
            return
        self._thread = threading.Thread(
            target=self._loop, name="EquityFeed", daemon=True
        )
        self._thread.start()
        log.info("Equity feed started for %d symbols.", len(self._symbols))

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            prices = self._fetch_batch()
            if prices:
                changed = {
                    sym: px
                    for sym, px in prices.items()
                    if abs(px - self._last_prices.get(sym, 0.0)) > 1e-9
                }
                if changed:
                    self._last_prices.update(changed)
                    self._on_update(prices)
            self._stop.wait(self._interval)

    def _fetch_batch(self) -> Dict[str, float]:
        prices: Dict[str, float] = {}
        for i in range(0, len(self._symbols), self._BATCH_SIZE):
            batch = self._symbols[i : i + self._BATCH_SIZE]
            try:
                tickers = yf.download(
                    batch,
                    period="1d",
                    interval="1m",
                    progress=False,
                    threads=False,
                    auto_adjust=True,
                )
                if tickers.empty:
                    continue
                close = tickers["Close"]
                if hasattr(close, "iloc"):
                    row = close.iloc[-1]
                    for sym in batch:
                        try:
                            prices[sym] = float(row[sym])
                        except (KeyError, TypeError, ValueError):
                            pass
            except Exception as exc:
                log.debug("yfinance batch error: %s", exc)
        return prices


# ══════════════════════════════════════════════════════════════════════════════
# CRYPTO FEED  (Binance WebSocket)
# ══════════════════════════════════════════════════════════════════════════════

_BINANCE_WS_URL = (
    "wss://stream.binance.com:9443/stream?streams="
    "{streams}"
)

_DEFAULT_CRYPTO_SYMBOLS = ["btcusdt", "ethusdt", "bnbusdt", "solusdt", "adausdt"]


class _CryptoFeed:
    """
    Subscribes to Binance miniticker WebSocket stream for real-time crypto prices.

    Falls back to CoinGecko REST polling if the WebSocket is unavailable.
    """

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        on_update: Optional[Callable[[Dict[str, float]], None]] = None,
        ws_reconnect_delay: float = 5.0,
    ) -> None:
        self._symbols = [s.lower() for s in (symbols or _DEFAULT_CRYPTO_SYMBOLS)]
        self._on_update = on_update or (lambda d: None)
        self._reconnect_delay = ws_reconnect_delay
        self._ws: Optional[object] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._latest: Dict[str, float] = {}

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._loop, name="CryptoFeed", daemon=True
        )
        self._thread.start()
        log.info("Crypto feed started for %s.", self._symbols)

    def stop(self) -> None:
        self._stop.set()
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass

    def _loop(self) -> None:
        while not self._stop.is_set():
            if _WS_OK:
                self._run_ws()
            else:
                self._run_rest_fallback()
            if not self._stop.is_set():
                log.warning("CryptoFeed disconnected — reconnecting in %.1fs", self._reconnect_delay)
                self._stop.wait(self._reconnect_delay)

    def _run_ws(self) -> None:
        streams = "/".join(f"{s}@miniTicker" for s in self._symbols)
        url = _BINANCE_WS_URL.format(streams=streams)

        def on_message(ws, raw: str) -> None:  # noqa: ARG001
            try:
                msg = json.loads(raw)
                data = msg.get("data", msg)
                sym = data.get("s", "").upper()          # e.g. "BTCUSDT"
                price = float(data.get("c", 0) or 0)     # last close price
                if sym and price:
                    self._latest[sym] = price
                    self._on_update(dict(self._latest))
            except Exception:
                pass

        def on_error(ws, err) -> None:  # noqa: ARG001
            log.debug("Binance WS error: %s", err)

        def on_close(ws, code, msg) -> None:  # noqa: ARG001
            pass

        try:
            self._ws = websocket.WebSocketApp(
                url,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
            )
            self._ws.run_forever(ping_interval=30, ping_timeout=10)
        except Exception as exc:
            log.debug("Binance WS exception: %s", exc)

    def _run_rest_fallback(self) -> None:
        """CoinGecko REST polling — 5 s cadence, no API key required."""
        import urllib.request

        ids = {
            "btcusdt": "bitcoin",
            "ethusdt": "ethereum",
            "bnbusdt": "binancecoin",
            "solusdt": "solana",
            "adausdt": "cardano",
        }
        cg_ids = ",".join(ids.get(s, s) for s in self._symbols)
        url = (
            f"https://api.coingecko.com/api/v3/simple/price"
            f"?ids={cg_ids}&vs_currencies=usd"
        )
        while not self._stop.is_set():
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "nexus-price-bus/1.0"})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read())
                prices: Dict[str, float] = {}
                for sym in self._symbols:
                    cg_id = ids.get(sym, sym)
                    val = data.get(cg_id, {}).get("usd")
                    if val:
                        prices[sym.upper().replace("USDT", "") + "USDT"] = float(val)
                if prices:
                    self._latest.update(prices)
                    self._on_update(dict(self._latest))
            except Exception as exc:
                log.debug("CoinGecko REST error: %s", exc)
            self._stop.wait(5.0)


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC API — PriceBus
# ══════════════════════════════════════════════════════════════════════════════

class PriceBus:
    """
    High-level market data distribution bus.

    Aggregates equity and crypto feeds, publishes over ZMQ PUB (with JSON
    fallback), and exposes a snapshot via get_snapshot().

    Example
    -------
    >>> bus = PriceBus(
    ...     equity_symbols=["RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS"],
    ...     crypto_symbols=["btcusdt", "ethusdt"],
    ...     zmq_addr="tcp://127.0.0.1:28081",
    ... )
    >>> bus.start()
    >>> import time; time.sleep(5)
    >>> print(bus.get_snapshot())
    """

    def __init__(
        self,
        equity_symbols: Optional[List[str]] = None,
        crypto_symbols: Optional[List[str]] = None,
        zmq_addr: str = "tcp://127.0.0.1:28081",
        json_fallback_path: str = "live_prices.json",
        equity_poll_interval: float = 1.0,
    ) -> None:
        self._equity_symbols = equity_symbols or []
        self._crypto_symbols = crypto_symbols or _DEFAULT_CRYPTO_SYMBOLS
        self._pub = _ZmqPublisher(zmq_addr)
        self._json = _AtomicJsonWriter(json_fallback_path)
        self._snapshot: Dict[str, Dict[str, float]] = {
            "equity": {},
            "crypto": {},
        }
        self._lock = threading.Lock()
        self._equity_feed = _EquityFeed(
            self._equity_symbols,
            on_update=self._on_equity_update,
            interval_sec=equity_poll_interval,
        )
        self._crypto_feed = _CryptoFeed(
            self._crypto_symbols,
            on_update=self._on_crypto_update,
        )

    # ── Callbacks ──────────────────────────────────────────────────────────────

    def _on_equity_update(self, prices: Dict[str, float]) -> None:
        with self._lock:
            self._snapshot["equity"].update(prices)
        self._publish("equity", prices)
        self._flush_json()

    def _on_crypto_update(self, prices: Dict[str, float]) -> None:
        with self._lock:
            self._snapshot["crypto"].update(prices)
        self._publish("crypto", prices)
        self._flush_json()

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _publish(self, topic: str, data: Dict[str, float]) -> None:
        payload = {
            "topic": topic,
            "ts": datetime.now(IST).isoformat(),
            "prices": data,
        }
        self._pub.publish(topic, payload)
        self._pub.publish("all", payload)

    def _flush_json(self) -> None:
        with self._lock:
            snap = dict(self._snapshot)
        all_prices: Dict[str, float] = {}
        all_prices.update(snap.get("equity", {}))
        all_prices.update(snap.get("crypto", {}))
        self._json.write(
            {
                "ts": datetime.now(IST).isoformat(),
                "prices": all_prices,
                "equity_prices": snap.get("equity", {}),
                "crypto_prices": snap.get("crypto", {}),
            }
        )

    # ── Public interface ───────────────────────────────────────────────────────

    def start(self) -> None:
        """Start all feeds (non-blocking — runs in background threads)."""
        self._equity_feed.start()
        self._crypto_feed.start()
        log.info(
            "PriceBus started — equity: %d symbols, crypto: %d symbols",
            len(self._equity_symbols),
            len(self._crypto_symbols),
        )

    def stop(self) -> None:
        """Gracefully stop all feeds and close the ZMQ socket."""
        self._equity_feed.stop()
        self._crypto_feed.stop()
        self._pub.close()
        log.info("PriceBus stopped.")

    def get_snapshot(self) -> Dict[str, Dict[str, float]]:
        """Return the latest price snapshot for all sources."""
        with self._lock:
            return {k: dict(v) for k, v in self._snapshot.items()}


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC API — PriceSubscriber
# ══════════════════════════════════════════════════════════════════════════════

class PriceSubscriber:
    """
    ZMQ SUB-based subscriber for nexus-price-bus topics.

    Falls back to polling the JSON file if ZMQ is unavailable.

    Example
    -------
    >>> def on_price(topic: str, data: dict) -> None:
    ...     print(f"[{topic}]", data["prices"])

    >>> sub = PriceSubscriber(topics=["equity", "crypto"])
    >>> sub.subscribe(on_price)
    >>> sub.start()
    """

    def __init__(
        self,
        addr: str = "tcp://127.0.0.1:28081",
        topics: Optional[List[str]] = None,
        json_fallback_path: str = "live_prices.json",
        json_poll_interval: float = 1.0,
    ) -> None:
        self._addr = addr
        self._topics = topics or ["all"]
        self._callbacks: List[Callable[[str, dict], None]] = []
        self._json = _AtomicJsonWriter(json_fallback_path)
        self._json_interval = json_poll_interval
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def subscribe(self, callback: Callable[[str, dict], None]) -> None:
        self._callbacks.append(callback)

    def _fire(self, topic: str, data: dict) -> None:
        for cb in self._callbacks:
            try:
                cb(topic, data)
            except Exception as exc:
                log.warning("Subscriber callback error: %s", exc)

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._loop, name="PriceSubscriber", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        if _ZMQ_OK:
            self._run_zmq()
        else:
            self._run_json_poll()

    def _run_zmq(self) -> None:
        ctx = zmq.Context.instance()
        sock = ctx.socket(zmq.SUB)
        sock.setsockopt(zmq.RCVHWM, 1000)
        sock.setsockopt(zmq.LINGER, 0)
        for topic in self._topics:
            sock.setsockopt_string(zmq.SUBSCRIBE, topic)
        sock.connect(self._addr)
        poller = zmq.Poller()
        poller.register(sock, zmq.POLLIN)
        try:
            while not self._stop.is_set():
                events = dict(poller.poll(timeout=500))
                if sock in events:
                    parts = sock.recv_multipart(flags=zmq.NOBLOCK)
                    if len(parts) == 2:
                        topic = parts[0].decode()
                        try:
                            data = json.loads(parts[1])
                        except json.JSONDecodeError:
                            continue
                        self._fire(topic, data)
        finally:
            sock.close(linger=0)

    def _run_json_poll(self) -> None:
        last_ts: Optional[str] = None
        while not self._stop.is_set():
            snap = self._json.read()
            if snap and snap.get("ts") != last_ts:
                last_ts = snap["ts"]
                self._fire("all", snap)
            self._stop.wait(self._json_interval)
