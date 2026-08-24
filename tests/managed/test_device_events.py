"""Tests for queued managed device-system events."""

from __future__ import annotations

import pytest

from pyalsoft import (
    DeviceEvent,
    DeviceEventType,
    DeviceKind,
    ResourceInUseError,
    bindings,
    subscribe_device_events,
)
from tests._support.alc_backend import _library


def test_device_events_are_queued_bounded_and_closed() -> None:
    library, fake = _library()
    subscription = subscribe_device_events(max_events=2, library=library)
    callback = fake.alc.system_event_callback
    assert callback is not None

    callback(
        bindings.ALC_EVENT_TYPE_DEVICE_ADDED_SOFT,
        bindings.ALC_PLAYBACK_DEVICE_SOFT,
        None,
        8,
        b"Speakers",
        None,
    )
    callback(
        bindings.ALC_EVENT_TYPE_DEVICE_REMOVED_SOFT,
        bindings.ALC_CAPTURE_DEVICE_SOFT,
        None,
        3,
        b"Mic",
        None,
    )
    callback(99, 100, None, 6, b"Future", None)

    assert subscription.dropped_count == 1
    assert subscription.next(timeout=0.0) == DeviceEvent(
        DeviceEventType.REMOVED,
        DeviceKind.CAPTURE,
        "Mic",
    )
    assert subscription.next(timeout=0.0) == DeviceEvent(99, 100, "Future")
    assert subscription.next(timeout=0.0) is None

    subscription.close()
    assert subscription.closed
    assert fake.alc.system_event_callback is None


def test_device_events_reject_multiple_owners() -> None:
    library, _fake = _library()
    with (
        subscribe_device_events(library=library),
        pytest.raises(ResourceInUseError, match="already has"),
    ):
        subscribe_device_events(library=library)
