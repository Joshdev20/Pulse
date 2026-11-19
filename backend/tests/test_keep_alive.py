"""The keep-alive hold, which is what keeps the free instance from sleeping.

GitHub throttles the 5-minute cron on a free plan down to a run every few hours,
so a single ping per run is not enough. These tests pin the behaviour that
compensates: a successful wake keeps pinging, and a failed hold tick does not
abandon the hold.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import keep_alive  # noqa: E402


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """Skip the wall-clock wait so the hold loop runs in milliseconds."""
    monkeypatch.setattr(keep_alive.time, "sleep", lambda _seconds: None)


def test_hold_seconds_reads_the_environment(monkeypatch):
    monkeypatch.setenv("PULSE_HOLD_SECONDS", "1234")
    assert keep_alive._hold_seconds() == 1234


def test_hold_seconds_is_off_by_default(monkeypatch):
    monkeypatch.delenv("PULSE_HOLD_SECONDS", raising=False)
    assert keep_alive._hold_seconds() == 0


def test_hold_seconds_ignores_a_bad_value(monkeypatch):
    """A typo in the variable must not crash the workflow."""
    monkeypatch.setenv("PULSE_HOLD_SECONDS", "an hour")
    assert keep_alive._hold_seconds() == 0


def test_hold_seconds_never_goes_negative(monkeypatch):
    monkeypatch.setenv("PULSE_HOLD_SECONDS", "-60")
    assert keep_alive._hold_seconds() == 0


def test_hold_keeps_pinging_after_a_failure(monkeypatch):
    """One failed tick must not end the hold and let the instance sleep."""
    calls = []

    def fake_ping(url):
        calls.append(url)
        # Fail the middle tick, then recover.
        return (len(calls) != 2), "ok" if len(calls) != 2 else "HTTP 502"

    monkeypatch.setattr(keep_alive, "ping", fake_ping)

    # The loop is driven by a deadline, so advance a fake clock by a second per
    # reading: the hold then runs a bounded number of ticks without real waits.
    clock = iter(range(0, 10_000))

    monkeypatch.setattr(keep_alive.time, "monotonic", lambda: float(next(clock)))

    keep_alive._hold("https://example.test/api/health", 10)

    assert len(calls) >= 3, "the hold stopped pinging after a failed tick"
    assert calls[0] == "https://example.test/api/health"


def test_a_successful_wake_holds_and_a_failed_one_does_not(monkeypatch):
    """`main` holds only after a wake, and exits non-zero when it never woke."""
    held = []
    monkeypatch.setattr(keep_alive, "_hold", lambda url, seconds: held.append(seconds))
    monkeypatch.setenv("PULSE_API_URL", "https://example.test")
    monkeypatch.setenv("PULSE_HOLD_SECONDS", "3300")

    monkeypatch.setattr(keep_alive, "ping", lambda url: (True, "ok"))
    assert keep_alive.main() == 0
    assert held == [3300], "a successful wake should hold the instance warm"

    held.clear()
    monkeypatch.setattr(keep_alive, "ping", lambda url: (False, "HTTP 502"))
    assert keep_alive.main() == 1
    assert held == [], "a failed wake should not hold"
