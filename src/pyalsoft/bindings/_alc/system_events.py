"""Library-global ALC system-event callback ownership."""

from __future__ import annotations

import threading
from collections.abc import Sequence
from contextlib import suppress

from pyalsoft.bindings._alc.callbacks import (
    CallbackRegistration,
    SystemEventCallback,
    _message_text,
)
from pyalsoft.bindings._alc.errors import CallbackControlError
from pyalsoft.bindings._alc.native import _integer_value
from pyalsoft.bindings._generated import types as _types
from pyalsoft.bindings._library import OpenALLibrary, _pointer_address

_SYSTEM_EVENT_LOCK = threading.RLock()
_SYSTEM_EVENT_CALLBACKS: dict[tuple[str, int], CallbackRegistration] = {}


def _system_event_key(library: OpenALLibrary) -> tuple[str, int]:
    native_library = getattr(library, "native_library", None)
    native_handle = getattr(native_library, "_handle", None)
    if isinstance(native_handle, int):
        return ("native", native_handle)
    return ("library", id(library))


def _register_system_event_callback(
    library: OpenALLibrary,
    callback: SystemEventCallback,
    *,
    event_types: Sequence[int] = (),
) -> CallbackRegistration:
    """Implement library-owned ``ALC_SOFT_system_events`` registration."""

    if not callable(callback):
        raise TypeError("callback must be callable")
    enabled_types = tuple(
        _integer_value(item, label="event type") for item in event_types
    )
    event_key = _system_event_key(library)
    owner_locks = (_SYSTEM_EVENT_LOCK, library._context_lock)

    with _SYSTEM_EVENT_LOCK, library._context_lock:
        library.extensions["ALC_SOFT_system_events"].require()
        previous = _SYSTEM_EVENT_CALLBACKS.get(event_key)
        if previous is not None:
            previous.close()
        errors: list[BaseException] = []

        def receive(
            event_type: int,
            device_type: int,
            device: object | None,
            length: int,
            message: bytes | None,
            _user_parameter: object | None,
        ) -> None:
            registration._begin_callback()
            try:
                callback_device = None if _pointer_address(device) is None else device
                callback(
                    int(event_type),
                    int(device_type),
                    callback_device,
                    _message_text(message, int(length)),
                )
            except BaseException as error:
                registration._record_error(error)
            finally:
                registration._end_callback()

        native_callback = _types.ALCEVENTPROCTYPESOFT(receive)

        def unregister(registration: CallbackRegistration) -> None:
            with _SYSTEM_EVENT_LOCK:
                if _SYSTEM_EVENT_CALLBACKS.get(event_key) is not registration:
                    return
                if enabled_types and not library.alc.event_control_soft(
                    enabled_types,
                    False,
                ):
                    registration._record_error(
                        CallbackControlError(
                            "OpenAL could not disable one or more system event types"
                        )
                    )
                library.alc.event_callback_soft(
                    _types.ALCEVENTPROCTYPESOFT(),
                    None,
                )
                _SYSTEM_EVENT_CALLBACKS.pop(event_key, None)
                if library._system_event_callback is registration:
                    library._system_event_callback = None

        registration = CallbackRegistration(
            native_callback,
            unregister,
            errors,
            owner_locks=owner_locks,
        )
        try:
            library.alc.event_callback_soft(native_callback, None)
            if enabled_types and not library.alc.event_control_soft(
                enabled_types,
                True,
            ):
                raise CallbackControlError(
                    "OpenAL could not enable one or more system event types"
                )
        except BaseException:
            if enabled_types:
                with suppress(BaseException):
                    library.alc.event_control_soft(enabled_types, False)
            with suppress(BaseException):
                library.alc.event_callback_soft(
                    _types.ALCEVENTPROCTYPESOFT(),
                    None,
                )
            raise
        library._system_event_callback = registration
        _SYSTEM_EVENT_CALLBACKS[event_key] = registration
        return registration


def _clear_system_event_callback(library: OpenALLibrary) -> None:
    """Clear the global system-event callback for a loaded native library."""

    event_key = _system_event_key(library)
    with _SYSTEM_EVENT_LOCK, library._context_lock:
        registration = _SYSTEM_EVENT_CALLBACKS.get(event_key)
        if registration is not None:
            registration.close()
        elif library._system_event_callback is not None:
            library._system_event_callback = None
