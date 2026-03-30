"""
tests/test_nexus_price_bus.py
Author : Ridhaant Ajoy Thackur
"""

import json
import os
import tempfile
import threading
import time

import pytest
from src.nexus_price_bus import _AtomicJsonWriter, PriceBus


class TestAtomicJsonWriter:
    def test_write_and_read_roundtrip(self, tmp_path):
        path = str(tmp_path / "test.json")
        writer = _AtomicJsonWriter(path)
        payload = {"ts": "2025-01-01T00:00:00", "prices": {"RELIANCE.NS": 2450.5}}
        writer.write(payload)
        result = writer.read()
        assert result == payload

    def test_read_missing_returns_none(self, tmp_path):
        path = str(tmp_path / "nonexistent.json")
        writer = _AtomicJsonWriter(path)
        assert writer.read() is None

    def test_concurrent_writes(self, tmp_path):
        path = str(tmp_path / "concurrent.json")
        writer = _AtomicJsonWriter(path)
        errors = []

        def write_worker(i):
            try:
                writer.write({"i": i})
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=write_worker, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        result = writer.read()
        assert "i" in result


class TestPriceBus:
    def test_get_snapshot_empty(self, tmp_path):
        bus = PriceBus(
            equity_symbols=[],
            crypto_symbols=[],
            json_fallback_path=str(tmp_path / "prices.json"),
        )
        snap = bus.get_snapshot()
        assert "equity" in snap
        assert "crypto" in snap

    def test_start_stop_no_crash(self, tmp_path):
        bus = PriceBus(
            equity_symbols=["RELIANCE.NS"],
            crypto_symbols=["btcusdt"],
            json_fallback_path=str(tmp_path / "prices.json"),
        )
        bus.start()
        time.sleep(0.2)
        bus.stop()  # must not raise
