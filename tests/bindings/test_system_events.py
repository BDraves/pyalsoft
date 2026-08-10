"""Tests for library-owned ALC system events."""

from __future__ import annotations

import threading

import pytest

from pyalsoft import bindings
from tests._support.alc_backend import _library


def test_system_event_registration_is_library_owned_and_safe() -> None:
    library, fake = _library()
    messages: list[tuple[int, int, object | None, str]] = []

    def receive(
        event_type: int,
        device_type: int,
        device: object | None,
        message: str,
    ) -> None:
        messages.append((event_type, device_type, device, message))
        raise RuntimeError("system event failed")

    registration = library.register_system_event_callback(
        receive,
        event_types=(bindings.ALC_EVENT_TYPE_DEVICE_ADDED_SOFT,),
    )
    native_callback = fake.alc.system_event_callback
    assert native_callback is not None
    native_callback(1, 2, None, 6, b"device", None)

    assert messages == [(1, 2, None, "device")]
    assert len(registration.errors) == 1
    registration.close()

    assert fake.alc.system_event_callback is None
    assert fake.alc.system_event_controls == [
        ((bindings.ALC_EVENT_TYPE_DEVICE_ADDED_SOFT,), True),
        ((bindings.ALC_EVENT_TYPE_DEVICE_ADDED_SOFT,), False),
    ]
    with pytest.raises(BaseExceptionGroup, match="OpenAL callback failed"):
        registration.raise_if_failed()


def test_system_event_registration_rolls_back_failed_enable() -> None:
    library, fake = _library()
    fake.alc.system_event_control_result = False

    with pytest.raises(bindings.CallbackControlError, match="could not enable"):
        library.register_system_event_callback(
            lambda _event, _type, _device, _message: None,
            event_types=(bindings.ALC_EVENT_TYPE_DEVICE_REMOVED_SOFT,),
        )

    assert fake.alc.system_event_callback is None
    assert fake.alc.system_event_controls == [
        ((bindings.ALC_EVENT_TYPE_DEVICE_REMOVED_SOFT,), True),
        ((bindings.ALC_EVENT_TYPE_DEVICE_REMOVED_SOFT,), False),
    ]


def test_system_event_callback_can_be_cleared_without_saved_registration() -> None:
    library, fake = _library()
    registration = library.register_system_event_callback(
        lambda _event, _type, _device, _message: None,
        event_types=(bindings.ALC_EVENT_TYPE_DEVICE_ADDED_SOFT,),
    )

    library.clear_system_event_callback()

    assert registration.closed
    assert fake.alc.system_event_callback is None
    library.clear_system_event_callback()


def test_system_event_close_waits_for_an_inflight_callback() -> None:
    library, fake = _library()
    entered = threading.Event()
    release = threading.Event()

    def receive(
        _event: int,
        _type: int,
        _device: object | None,
        _message: str,
    ) -> None:
        entered.set()
        assert release.wait(5)

    registration = library.register_system_event_callback(receive)
    native_callback = fake.alc.system_event_callback
    assert native_callback is not None
    callback_thread = threading.Thread(
        target=native_callback,
        args=(1, 2, None, 0, None, None),
    )
    callback_thread.start()
    assert entered.wait(2)

    close_thread = threading.Thread(target=registration.close)
    close_thread.start()
    assert fake.alc.system_event_removed.wait(2)
    assert close_thread.is_alive()
    assert registration._callback is native_callback

    release.set()
    callback_thread.join(2)
    close_thread.join(2)
    assert not callback_thread.is_alive()
    assert not close_thread.is_alive()
    assert registration.closed
