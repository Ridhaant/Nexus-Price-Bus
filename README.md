<div align="center">

<img src="https://readme-typing-svg.demolab.com?font=Fira+Code&size=24&duration=2500&pause=800&color=00D4FF&center=true&vCenter=true&width=800&lines=Nexus+Price+Bus;ZMQ+PUB%2FSUB+Multi-Source+Financial+Data+Bus;16+Concurrent+Subscribers+%7C+Zero+Data+Loss;NSE+%2B+MCX+%2B+Binance+%E2%86%92+One+Bus" alt="nexus-price-bus" />

<br/>

![ZMQ](https://img.shields.io/badge/Messaging-ZeroMQ-DF0000?style=for-the-badge&logo=zeromq&logoColor=white)
![Subscribers](https://img.shields.io/badge/Subscribers-16%20Concurrent-00D4FF?style=for-the-badge)
![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge)

<br/>

<img src="https://skillicons.dev/icons?i=python,linux,redis,docker,git&theme=dark" />

<br/><br/>

![ZeroMQ](https://img.shields.io/badge/ZeroMQ-DF0000?style=flat-square&logo=zeromq&logoColor=white)
![Redis](https://img.shields.io/badge/Redis_7-DC382D?style=flat-square&logo=redis&logoColor=white)
![Binance](https://img.shields.io/badge/Binance_WS-F0B90B?style=flat-square&logo=binance&logoColor=black)
![yfinance](https://img.shields.io/badge/yfinance-7B1FA2?style=flat-square)
![Atomic](https://img.shields.io/badge/Atomic_Writes-00FF41?style=flat-square)

*Sole-authored by **[Ridhaant Ajoy Thackur](https://github.com/Ridhaant)** · Extracted from [AlgoStack](https://github.com/Ridhaant/AlgoStack)*

</div>

---

## ⚡ What Is nexus-price-bus?

A production-tested ZeroMQ PUB/SUB multi-source financial price bus that normalises ticks from **NSE equity (yfinance), MCX commodities (TradingView WS), and Binance crypto (WebSocket)** — distributing to 16 concurrent subscribers with **zero data loss** via dual-transport (ZMQ + atomic JSON).

---

## 📊 Specifications

| Metric | Value | Status |
|:---|:---|:---:|
| **Concurrent subscribers** | 16 processes | 🟢 LIVE |
| **ZMQ endpoint** | `tcp://127.0.0.1:28081` | 🟢 LIVE |
| **Topics** | `equity`, `crypto`, `commodity`, `all` | 🟢 LIVE |
| **SNDHWM** | 1000 (backpressure) | ✅ PROD |
| **LINGER** | 0 (no socket hang on close) | ✅ PROD |
| **Dedup threshold** | abs delta < 1e-9 | ✅ PROD |
| **Fallback** | Atomic JSON (write-to-.tmp + os.replace) | ✅ PROD |

---

## 🏗️ Architecture

```mermaid
graph LR
    subgraph "📡 Data Sources"
        YF["yfinance<br/>38 NSE equities<br/>10-symbol batches"]
        BN["Binance WS<br/>5 crypto pairs<br/>1s + CoinGecko fallback"]
        TV["TradingView WS<br/>5 MCX commodities<br/>4-tier fallback"]
    end
    subgraph "🔷 Nexus Price Bus"
        PB["PriceBus Publisher<br/>Non-blocking daemon threads<br/>Dedup: abs Δ < 1e-9"]
        ZMQ["ZMQ PUB/SUB<br/>SNDHWM=1000 · LINGER=0"]
        JSON["Atomic JSON Fallback<br/>os.replace — zero corruption"]
    end
    subgraph "📥 Subscribers (16)"
        E["3 Trading Engines"]
        S["9 Sweep Scanners"]
        D["Dashboard + Alerts"]
    end
    YF & BN & TV --> PB --> ZMQ & JSON --> E & S & D

    style PB fill:#0d1117,stroke:#00D4FF,stroke-width:2px,color:#00D4FF
    style ZMQ fill:#0d1117,stroke:#DF0000,stroke-width:2px,color:#DF0000
```

### Key Classes

| Class | Role |
|:---|:---|
| `PriceBus` | Non-blocking publisher with daemon threads |
| `PriceSubscriber` | ZMQ SUB + JSON poll fallback (handles ZMQ-unavailable environments) |

---

## 📂 Files to Upload

When publishing this standalone repository, upload these exact files from the AlgoStack codebase:
- `ipc_bus.py` — ZMQ PUB/SUB and Redis abstraction layer
- `price_service.py` — Multi-source feed aggregator (yfinance, TradingView WS, Binance)
- `requirements.txt` — (Must include `pyzmq`, `redis`, `yfinance`, `websockets`)
- This `README.md`

---

## 🔗 Proven in Production

Extracted from [AlgoStack](https://github.com/Ridhaant/AlgoStack) v10.7's `ipc_bus.py` — battle-tested across **16 concurrent processes** on a live trading system. Handles NSE, MCX, and Binance feeds simultaneously with autonomous reconnection and deduplication.

---

## 📦 Related

[![AlgoStack](https://img.shields.io/badge/AlgoStack-Parent%20Platform-00D4FF?style=for-the-badge)](https://github.com/Ridhaant/AlgoStack)
[![vectorsweep](https://img.shields.io/badge/vectorsweep-GPU%20Sweeps-76B900?style=for-the-badge)](https://github.com/Ridhaant/VectorSweep)
[![sentitrade](https://img.shields.io/badge/sentitrade-NLP%20Signals-3fb950?style=for-the-badge)](https://github.com/Ridhaant/SentiTrade)
[![SentinelVault](https://img.shields.io/badge/SentinelVault-Security-FF6B35?style=for-the-badge)](https://github.com/Ridhaant/SentinelVault)

---

<div align="center">

© 2026 Ridhaant Ajoy Thackur · MIT License

</div>
