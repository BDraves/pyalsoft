"""Shared timeout and polling support for blocking managed operations."""

from __future__ import annotations

import time

from pyalsoft._managed._values import _finite_float


class _Waiter:
    __slots__ = ("_deadline", "_poll_interval")

    def __init__(self, timeout: float | None, poll_interval: float) -> None:
        if timeout is None:
            self._deadline: float | None = None
        else:
            validated_timeout = _finite_float("timeout", timeout)
            if validated_timeout < 0.0:
                raise ValueError("timeout must be non-negative")
            self._deadline = time.monotonic() + validated_timeout

        validated_interval = _finite_float("poll_interval", poll_interval)
        if validated_interval <= 0.0:
            raise ValueError("poll_interval must be positive")
        self._poll_interval = validated_interval

    def pause(self) -> bool:
        """Sleep until the next poll, or return false when time has expired."""

        delay = self._poll_interval
        if self._deadline is not None:
            remaining = self._deadline - time.monotonic()
            if remaining <= 0.0:
                return False
            delay = min(delay, remaining)
        time.sleep(delay)
        return True
