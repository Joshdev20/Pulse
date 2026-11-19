#!/usr/bin/env python3
"""Keep the Render free-tier backend awake so media keeps loading.

A free web service sleeps after ~15 minutes idle and then takes 30-60s to wake.
During that window every request stalls, and a media download that times out
surfaces in the UI as a broken image or a video that never plays — the failure
looks like lost media rather than a sleeping host. Pinging the health endpoint
on a schedule keeps the instance warm so those requests never hit a cold start.

Run it on a schedule (the GitHub Actions workflow in
`.github/workflows/keep-alive.yml`) or by hand:

    PULSE_API_URL=https://pulse-api-y16m.onrender.com python scripts/keep_alive.py

Only the health endpoint is touched. It is unauthenticated and does no database
work, so a ping costs a millisecond and never writes anything.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

# A cold Render instance can take up to a minute to answer, so the timeout has
# to outlast a wake-up. A shorter one would report the very outage it is meant
# to prevent.
REQUEST_TIMEOUT_SECONDS = 90

# One retry covers a transient 502, which the free tier emits while an instance
# is being replaced.
ATTEMPTS = 2


def ping(url: str) -> tuple[bool, str]:
    """Hit the health endpoint once, returning whether it answered as healthy."""
    request = urllib.request.Request(url, headers={"User-Agent": "pulse-keep-alive/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as error:
        return False, f"HTTP {error.code}"
    except Exception as error:  # noqa: BLE001 - a network failure is the thing being reported
        return False, f"{type(error).__name__}: {error}"

    if status != 200:
        return False, f"HTTP {status}"
    try:
        payload = json.loads(raw)
    except ValueError:
        return False, f"non-JSON body ({len(raw)} bytes)"
    if payload.get("status") != "ok":
        return False, f"unhealthy payload: {payload}"
    # Surface the cache backend, since a deploy that fails to connect to Redis
    # silently serves from memory and is otherwise invisible from out here.
    return True, f"ok (cache={payload.get('cache')})"


def main() -> int:
    base = os.environ.get("PULSE_API_URL", "").strip().rstrip("/")
    if not base:
        print("PULSE_API_URL is not set; nothing to ping.", file=sys.stderr)
        return 2
    url = f"{base}/api/health"

    last = "no attempt made"
    for attempt in range(1, ATTEMPTS + 1):
        ok, detail = ping(url)
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        print(f"{stamp}  {url}  attempt {attempt}/{ATTEMPTS}  {detail}", flush=True)
        if ok:
            hold = _hold_seconds()
            if hold > 0:
                _hold(url, hold)
            return 0
        last = detail

    print(f"health check failed after {ATTEMPTS} attempts: {last}", file=sys.stderr)
    return 1


def _hold_seconds() -> int:
    """How long this run should keep pinging after the first success."""
    try:
        return max(0, int(os.environ.get("PULSE_HOLD_SECONDS", "0")))
    except ValueError:
        return 0


def _hold(url: str, seconds: int) -> None:
    """Keep the instance warm for ``seconds`` after a successful wake.

    The scheduled workflow asks GitHub for a run every 5 minutes, but GitHub
    throttles a cron that frequent on a free plan and in practice delivers a run
    every 4-9 hours. A single ping therefore left the free instance asleep for
    most of the day, and every visit in between paid the 30-60s cold start.

    Holding the ping inside one run covers the gap the schedule cannot: each
    run stays alive for its budget, so the instance is never idle long enough to
    sleep between the runs that do arrive.
    """
    interval = 60
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        time.sleep(min(interval, max(0.0, deadline - time.monotonic())))
        _ok, detail = ping(url)
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        # A failed tick is not a failed run: the next one may well succeed, and
        # exiting early would let the instance sleep until the next scheduled
        # run, which may be hours away.
        print(f"{stamp}  {url}  holding  {detail}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
