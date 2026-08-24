"""Managed playback-device state and queued system event delivery."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from enum import Enum
from threading import Condition, RLock
from types import TracebackType
from typing import Self

from pyalsoft import bindings
from pyalsoft._managed.errors import ResourceInUseError


class DeviceEventType(Enum):
    """Kind of playback or capture device-list change."""

    DEFAULT_CHANGED = "default_changed"
    ADDED = "added"
    REMOVED = "removed"


class DeviceKind(Enum):
    """Device family affected by a managed system event."""

    PLAYBACK = "playback"
    CAPTURE = "capture"


@dataclass(frozen=True, slots=True)
class DeviceEvent:
    """One queued device-list notification from OpenAL Soft.

    Unknown future native event or device values are preserved as integers.

    Attributes:
        type: Device-list change kind.
        device_kind: Playback or capture device family.
        name: Implementation-provided device name.
    """

    type: DeviceEventType | int
    device_kind: DeviceKind | int
    name: str


_EVENT_TYPES = {
    bindings.ALC_EVENT_TYPE_DEFAULT_DEVICE_CHANGED_SOFT: DeviceEventType.DEFAULT_CHANGED,
    bindings.ALC_EVENT_TYPE_DEVICE_ADDED_SOFT: DeviceEventType.ADDED,
    bindings.ALC_EVENT_TYPE_DEVICE_REMOVED_SOFT: DeviceEventType.REMOVED,
}
_DEVICE_KINDS = {
    bindings.ALC_PLAYBACK_DEVICE_SOFT: DeviceKind.PLAYBACK,
    bindings.ALC_CAPTURE_DEVICE_SOFT: DeviceKind.CAPTURE,
}
_NATIVE_EVENT_TYPES = tuple(_EVENT_TYPES)
_subscriptions_lock = RLock()
_subscriptions: dict[bindings.OpenALLibrary, DeviceEventSubscription] = {}


class DeviceEventSubscription:
    """Bounded queue of device events delivered outside native callbacks.

    Use [`subscribe_device_events`][pyalsoft.subscribe_device_events] instead of
    constructing this class directly. Only one managed or low-level system-event
    registration can own a loaded native library at a time.
    """

    __slots__ = (
        "_closed",
        "_condition",
        "_events",
        "_library",
        "_max_events",
        "_registration",
        "_dropped_count",
    )

    def __init__(self, library: bindings.OpenALLibrary, max_events: int) -> None:
        self._library = library
        self._max_events = max_events
        self._events: deque[DeviceEvent] = deque()
        self._condition = Condition(RLock())
        self._closed = False
        self._dropped_count = 0

        def receive(
            event_type: int,
            device_type: int,
            _device: object | None,
            message: str,
        ) -> None:
            event = DeviceEvent(
                _EVENT_TYPES.get(event_type, event_type),
                _DEVICE_KINDS.get(device_type, device_type),
                message,
            )
            with self._condition:
                if self._closed:
                    return
                if len(self._events) == self._max_events:
                    self._events.popleft()
                    self._dropped_count += 1
                self._events.append(event)
                self._condition.notify()

        self._registration = library.register_system_event_callback(
            receive,
            event_types=_NATIVE_EVENT_TYPES,
        )

    @property
    def closed(self) -> bool:
        """Whether the subscription has been closed."""

        with self._condition:
            return self._closed

    @property
    def dropped_count(self) -> int:
        """Number of oldest queued events discarded because the queue was full."""

        with self._condition:
            return self._dropped_count

    def next(self, timeout: float | None = None) -> DeviceEvent | None:
        """Return the next event, or ``None`` after timeout or closure."""

        if timeout is not None:
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
                raise TypeError("timeout must be a number or None")
            timeout = float(timeout)
            if not math.isfinite(timeout):
                raise ValueError("timeout must be finite")
            if timeout < 0.0:
                raise ValueError("timeout must be non-negative")
        with self._condition:
            if not self._events and not self._closed:
                self._condition.wait_for(
                    lambda: bool(self._events) or self._closed,
                    timeout,
                )
            return self._events.popleft() if self._events else None

    def close(self) -> None:
        """Stop native delivery and wake threads waiting for events."""

        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._condition.notify_all()
        try:
            self._registration.close()
        finally:
            with _subscriptions_lock:
                if _subscriptions.get(self._library) is self:
                    del _subscriptions[self._library]

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exception_type, exception, traceback
        self.close()

    def __repr__(self) -> str:
        return "DeviceEventSubscription(<opaque>)"


def subscribe_device_events(
    *,
    max_events: int = 256,
    library: bindings.OpenALLibrary | None = None,
) -> DeviceEventSubscription:
    """Subscribe to a bounded queue of playback and capture device changes.

    Native callbacks only enqueue immutable values. Application code receives
    them later by calling [`DeviceEventSubscription.next`][pyalsoft.DeviceEventSubscription.next].
    This avoids running application work on OpenAL's system callback thread.
    """

    if isinstance(max_events, bool) or not isinstance(max_events, int):
        raise TypeError("max_events must be an integer")
    if max_events <= 0:
        raise ValueError("max_events must be positive")
    selected = bindings.load() if library is None else library
    with _subscriptions_lock:
        if selected in _subscriptions or selected._system_event_callback is not None:
            raise ResourceInUseError(
                "the selected OpenAL library already has a system-event subscription"
            )
        subscription = DeviceEventSubscription(selected, max_events)
        _subscriptions[selected] = subscription
        return subscription
