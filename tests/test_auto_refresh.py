"""Tests for v2.1-T4: auto-refresh background thread."""
import threading
import time

import pytest

from marktradar import server


# ── helpers ───────────────────────────────────────────────────────────────────

class _StopLoop(BaseException):
    """Break out of the loop — BaseException bypasses except Exception guards."""


# ── _auto_refresh_loop unit tests ─────────────────────────────────────────────

def test_loop_calls_refresh_with_correct_args(monkeypatch):
    """One cycle: refresh is called with all three sources and the requested limit."""
    calls = []

    def _fake_refresh(conn, sources, limit):
        calls.append({"sources": sources, "limit": limit})
        raise _StopLoop

    monkeypatch.setattr(server.hashtags, "refresh", _fake_refresh)
    monkeypatch.setattr(server.db, "connect", lambda: object())
    monkeypatch.setattr(time, "sleep", lambda s: None)

    with pytest.raises(_StopLoop):
        server._auto_refresh_loop(interval=1, limit=7, initial_delay=0)

    assert len(calls) == 1
    assert set(calls[0]["sources"]) == {"mastodon", "bluesky", "news"}
    assert calls[0]["limit"] == 7


def test_loop_continues_after_refresh_exception(monkeypatch):
    """If refresh raises a regular Exception, the loop logs a warning and continues."""
    attempt = [0]
    stop_at = 3

    def _fake_refresh(conn, sources, limit):
        attempt[0] += 1
        if attempt[0] < stop_at:
            raise RuntimeError("simulated network error")
        raise _StopLoop  # BaseException — escapes the except Exception guard

    monkeypatch.setattr(server.hashtags, "refresh", _fake_refresh)
    monkeypatch.setattr(server.db, "connect", lambda: object())
    monkeypatch.setattr(time, "sleep", lambda s: None)

    with pytest.raises(_StopLoop):
        server._auto_refresh_loop(interval=1, limit=5, initial_delay=0)

    assert attempt[0] == stop_at, "loop must survive RuntimeError failures and keep cycling"


def test_loop_logs_summary_on_success(monkeypatch, caplog):
    """A successful cycle logs INFO lines for BOTH stages (news + hashtags)."""
    import logging

    call_count = [0]

    def _fake_refresh(conn, sources, limit):
        call_count[0] += 1
        if call_count[0] == 1:
            return {"added": 42, "errors": [], "tags": 3, "per_source": {}}
        raise _StopLoop

    monkeypatch.setattr(server.ingest, "refresh",
                        lambda c, **k: {"new": 5, "embedded": 5, "errors": [], "sources": 2})
    monkeypatch.setattr(server.hashtags, "refresh", _fake_refresh)
    monkeypatch.setattr(server.db, "connect", lambda: object())
    monkeypatch.setattr(time, "sleep", lambda s: None)

    with caplog.at_level(logging.INFO, logger="marktradar.server"):
        with pytest.raises(_StopLoop):
            server._auto_refresh_loop(interval=1, limit=5, initial_delay=0)

    assert any("auto-refresh news: new=5" in r.message for r in caplog.records)
    assert any("auto-refresh hashtags: added=42" in r.message for r in caplog.records)


def test_loop_logs_warning_on_cycle_exception(monkeypatch, caplog):
    """A failing hashtag stage emits a WARNING — not a crash, news stage unberührt."""
    import logging

    attempt = [0]

    def _fake_refresh(conn, sources, limit):
        attempt[0] += 1
        if attempt[0] == 1:
            raise ConnectionError("timeout")
        raise _StopLoop

    monkeypatch.setattr(server.ingest, "refresh",
                        lambda c, **k: {"new": 0, "embedded": 0, "errors": [], "sources": 0})
    monkeypatch.setattr(server.hashtags, "refresh", _fake_refresh)
    monkeypatch.setattr(server.db, "connect", lambda: object())
    monkeypatch.setattr(time, "sleep", lambda s: None)

    with caplog.at_level(logging.WARNING, logger="marktradar.server"):
        with pytest.raises(_StopLoop):
            server._auto_refresh_loop(interval=1, limit=5, initial_delay=0)

    warning_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("auto-refresh hashtags failed" in m for m in warning_msgs)


# ── _start_auto_refresh gating tests ─────────────────────────────────────────

def test_thread_does_not_start_when_flag_unset(monkeypatch):
    """No thread must be created when PFLEGE_AUTO_REFRESH is absent or 0."""
    monkeypatch.delenv("PFLEGE_AUTO_REFRESH", raising=False)

    before = {t.name for t in threading.enumerate()}
    server._start_auto_refresh()
    after = {t.name for t in threading.enumerate()}

    assert "pflege-auto-refresh" not in after - before


def test_thread_does_not_start_when_flag_is_zero(monkeypatch):
    monkeypatch.setenv("PFLEGE_AUTO_REFRESH", "0")

    before = {t.name for t in threading.enumerate()}
    server._start_auto_refresh()
    after = {t.name for t in threading.enumerate()}

    assert "pflege-auto-refresh" not in after - before


def test_thread_starts_when_flag_is_one(monkeypatch):
    """Thread must be spawned (daemon) when PFLEGE_AUTO_REFRESH=1."""
    monkeypatch.setenv("PFLEGE_AUTO_REFRESH", "1")
    monkeypatch.setenv("PFLEGE_REFRESH_INTERVAL", "9999")
    monkeypatch.setenv("PFLEGE_REFRESH_INITIAL_DELAY", "9999")

    # Stub refresh so if the thread ever fires it does nothing harmful
    monkeypatch.setattr(server.hashtags, "refresh",
                        lambda *a, **kw: {"added": 0, "errors": [], "tags": 0})
    monkeypatch.setattr(server.db, "connect", lambda: object())

    server._start_auto_refresh()

    threads = {t.name: t for t in threading.enumerate()}
    t = threads.get("pflege-auto-refresh")
    assert t is not None, "daemon thread was not started"
    assert t.daemon is True


def test_thread_starts_when_flag_is_true_string(monkeypatch):
    """PFLEGE_AUTO_REFRESH=true (lowercase) also enables the thread."""
    monkeypatch.setenv("PFLEGE_AUTO_REFRESH", "true")
    monkeypatch.setenv("PFLEGE_REFRESH_INTERVAL", "9999")
    monkeypatch.setenv("PFLEGE_REFRESH_INITIAL_DELAY", "9999")

    monkeypatch.setattr(server.hashtags, "refresh",
                        lambda *a, **kw: {"added": 0, "errors": [], "tags": 0})
    monkeypatch.setattr(server.db, "connect", lambda: object())

    server._start_auto_refresh()

    threads = {t.name for t in threading.enumerate()}
    assert "pflege-auto-refresh" in threads


def test_env_vars_are_forwarded_to_loop(monkeypatch):
    """PFLEGE_REFRESH_INTERVAL and PFLEGE_REFRESH_LIMIT are passed correctly to the loop."""
    captured = {}

    def _fake_loop(interval, limit, initial_delay):
        captured["interval"] = interval
        captured["limit"] = limit
        captured["initial_delay"] = initial_delay
        # return immediately — no sleeping

    monkeypatch.setenv("PFLEGE_AUTO_REFRESH", "1")
    monkeypatch.setenv("PFLEGE_REFRESH_INTERVAL", "300")
    monkeypatch.setenv("PFLEGE_REFRESH_LIMIT", "25")
    monkeypatch.setenv("PFLEGE_REFRESH_INITIAL_DELAY", "10")
    monkeypatch.setattr(server, "_auto_refresh_loop", _fake_loop)

    server._start_auto_refresh()

    # Give the daemon thread a short moment to call the (instant) fake loop
    time.sleep(0.2)

    assert captured.get("interval") == 300
    assert captured.get("limit") == 25
    assert captured.get("initial_delay") == 10
