"""Tests for context-owned native callbacks."""

from __future__ import annotations

import ctypes
import threading
from collections.abc import Callable
from typing import Any, cast

import pytest

from pyalsoft import bindings
from pyalsoft.bindings._generated import types as native_types
from tests._support.alc_backend import _library


def test_event_registration_retains_callback_and_captures_errors() -> None:
    library, fake = _library()
    messages: list[str] = []
    with (
        bindings.open_device(library=library) as device,
        device.create_context() as context,
    ):

        def receive(
            _event_type: int,
            _object_id: int,
            _parameter: int,
            message: str,
        ) -> None:
            messages.append(message)
            raise ValueError("callback failed")

        registration = context.register_event_callback(
            receive,
            event_types=(bindings.AL_EVENT_TYPE_BUFFER_COMPLETED_SOFT,),
        )
        native_callback = fake.al.event_callback
        assert native_callback is not None
        native_callback(1, 2, 3, 5, b"hello", None)

        assert messages == ["hello"]
        assert len(registration.errors) == 1
        with pytest.raises(BaseExceptionGroup, match="OpenAL callback failed"):
            registration.raise_if_failed()
        assert not registration.errors
        registration.close()

    assert fake.al.event_callback is None
    assert fake.al.event_controls == [
        ((bindings.AL_EVENT_TYPE_BUFFER_COMPLETED_SOFT,), True),
        ((bindings.AL_EVENT_TYPE_BUFFER_COMPLETED_SOFT,), False),
    ]


def test_callback_registration_rejects_self_close() -> None:
    library, fake = _library()
    registrations: list[bindings.CallbackRegistration] = []

    def receive(
        _event: int,
        _type: int,
        _device: object | None,
        _message: str,
    ) -> None:
        registrations[0].close()

    registration = library.register_system_event_callback(receive)
    registrations.append(registration)
    native_callback = fake.alc.system_event_callback
    assert native_callback is not None

    native_callback(1, 2, None, 0, None, None)

    assert not registration.closed
    assert len(registration.errors) == 1
    assert isinstance(registration.errors[0], bindings.CallbackControlError)
    registration.close()


def test_buffer_callback_owns_trampoline_and_contains_failures() -> None:
    library, fake = _library()
    with (
        bindings.open_device(library=library) as device,
        device.create_context() as context,
    ):
        calls = 0

        def fill(view: memoryview) -> int:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("render failed")
            view[:] = b"tone"
            return len(view)

        registration = context.register_buffer_callback(
            7,
            bindings.AL_FORMAT_MONO8,
            48_000,
            fill,
        )
        native_callback = fake.al.buffer_callbacks[7]
        output = ctypes.create_string_buffer(4)

        assert native_callback(None, ctypes.addressof(output), 4) == 4
        assert output.raw == b"tone"
        assert native_callback(None, ctypes.addressof(output), 4) == 0
        assert len(registration.errors) == 1

        registration.close()
        assert 7 not in fake.al.buffer_callbacks

    assert fake.al.buffer_resets == [(7, bindings.AL_FORMAT_MONO8, 48_000)]


def test_buffer_callback_close_does_not_overwrite_external_replacement() -> None:
    library, fake = _library()
    with (
        bindings.open_device(library=library) as device,
        device.create_context() as context,
    ):
        registration = context.register_buffer_callback(
            3,
            bindings.AL_FORMAT_MONO8,
            8_000,
            lambda view: len(view),
        )
        replacement = native_types.ALBUFFERCALLBACKTYPESOFT(
            lambda _user, _samples, _size: 0
        )
        fake.al.buffer_callbacks[3] = replacement

        registration.close()

        assert fake.al.buffer_callbacks[3] is replacement
        assert fake.al.buffer_resets == []


def test_context_close_finalizes_attached_buffer_callback_after_destroy() -> None:
    library, fake = _library()
    device = bindings.open_device(library=library)
    context = device.create_context()
    registration = context.register_buffer_callback(
        5,
        bindings.AL_FORMAT_MONO8,
        8_000,
        lambda view: len(view),
    )

    context.close()

    assert registration.closed
    assert fake.al.buffer_resets == []
    assert fake.alc.destroyed == [fake.alc.context]
    device.close()


def test_buffer_callback_requires_a_buffer_from_the_same_library() -> None:
    library, _fake = _library()
    other_library, _other_fake = _library()
    with (
        bindings.open_device(library=library) as device,
        device.create_context() as context,
        bindings.open_device(library=other_library) as other_device,
        other_device.create_context() as other_context,
    ):
        buffer = context.buffer(3)
        registration = context.register_buffer_callback(
            buffer,
            bindings.AL_FORMAT_MONO8,
            8_000,
            lambda view: len(view),
        )
        registration.close()

        with pytest.raises(TypeError, match="integer or Buffer"):
            context.register_buffer_callback(
                cast(Any, context.source(4)),
                bindings.AL_FORMAT_MONO8,
                8_000,
                lambda view: len(view),
            )
        with pytest.raises(ValueError, match="different OpenAL context"):
            context.set_static_buffer_data(
                other_context.buffer(5),
                bindings.AL_FORMAT_MONO8,
                b"samples",
                8_000,
            )


def test_buffer_callback_registration_is_serialized_across_threads() -> None:
    library, fake = _library()
    device = bindings.open_device(library=library)
    context = device.create_context()
    entered = threading.Event()
    release = threading.Event()
    call_lock = threading.Lock()
    native_calls = 0
    original = fake.al.buffer_callback_soft

    def blocking_install(
        buffer: int,
        format: int,
        frequency: int,
        callback: Callable[..., int],
        user_pointer: object | None,
    ) -> None:
        nonlocal native_calls
        with call_lock:
            native_calls += 1
            call_number = native_calls
        if call_number == 1:
            entered.set()
            assert release.wait(2)
        original(buffer, format, frequency, callback, user_pointer)

    fake.al.buffer_callback_soft = blocking_install  # type: ignore[assignment]
    registrations: dict[str, bindings.CallbackRegistration] = {}
    failures: list[BaseException] = []

    def install(name: str) -> None:
        try:
            registrations[name] = context.register_buffer_callback(
                6,
                bindings.AL_FORMAT_MONO8,
                8_000,
                lambda view: len(view),
            )
        except BaseException as error:
            failures.append(error)

    first = threading.Thread(target=install, args=("first",))
    second = threading.Thread(target=install, args=("second",))
    first.start()
    assert entered.wait(2)
    second.start()
    release.set()
    first.join(2)
    second.join(2)

    assert not first.is_alive() and not second.is_alive()
    assert failures == []
    assert registrations["first"].closed
    assert not registrations["second"].closed
    assert context._buffer_callbacks[6] is registrations["second"]
    assert fake.al.buffer_callbacks[6] is registrations["second"]._callback
    context.close()
    device.close()


def test_buffer_callback_failed_verification_is_rolled_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library, fake = _library()
    original = fake.al.get_buffer_ptr_soft
    query_count = 0

    def fail_first_query(buffer: int, parameter: int) -> object | None:
        nonlocal query_count
        query_count += 1
        if query_count == 1:
            return None
        return original(buffer, parameter)

    monkeypatch.setattr(fake.al, "get_buffer_ptr_soft", fail_first_query)
    with (
        bindings.open_device(library=library) as device,
        device.create_context() as context,
    ):
        with pytest.raises(bindings.CallbackControlError, match="did not install"):
            context.register_buffer_callback(
                8,
                bindings.AL_FORMAT_MONO8,
                8_000,
                lambda view: len(view),
            )

        assert 8 not in fake.al.buffer_callbacks
        assert 8 not in context._buffer_callbacks


def test_foldback_registration_retains_memory_until_stopped() -> None:
    library, fake = _library()
    memory = bytearray(16)
    events: list[tuple[int, int]] = []
    with (
        bindings.open_device(library=library) as device,
        device.create_context() as context,
    ):
        registration = context.start_foldback(
            bindings.AL_FOLDBACK_MODE_MONO,
            4,
            1,
            memory,
            lambda event, index: events.append((event, index)),
        )
        native_callback = fake.al.foldback_callback
        assert native_callback is not None
        native_callback(bindings.AL_FOLDBACK_EVENT_BLOCK, 4)
        assert events == [(bindings.AL_FOLDBACK_EVENT_BLOCK, 4)]
        assert ctypes.sizeof(cast(Any, registration.memory)) == len(memory)

    assert events[-1] == (bindings.AL_FOLDBACK_EVENT_STOP, 0)
    assert registration.closed
    assert ctypes.sizeof(cast(Any, registration.memory)) == len(memory)
    assert fake.al.foldback_callback is None
    assert [call[0] for call in fake.al.foldback_calls] == ["start", "stop"]


def test_foldback_close_waits_for_native_stop_event() -> None:
    library, fake = _library()
    fake.al.foldback_auto_stop = False
    device = bindings.open_device(library=library)
    context = device.create_context()
    registration = context.start_foldback(
        bindings.AL_FOLDBACK_MODE_STEREO,
        2,
        2,
        bytearray(2 * 2 * 2 * ctypes.sizeof(native_types.ALfloat)),
        lambda _event, _index: None,
    )
    native_callback = fake.al.foldback_callback
    assert native_callback is not None
    close_thread = threading.Thread(target=registration.close)
    close_thread.start()
    assert fake.al.foldback_stop_requested.wait(2)

    assert close_thread.is_alive()
    assert registration.stopping
    assert registration._callback is not None
    native_callback(bindings.AL_FOLDBACK_EVENT_STOP, 0)
    close_thread.join(2)

    assert not close_thread.is_alive()
    assert registration.closed
    context.close()
    device.close()


def test_foldback_stop_event_is_finalized_by_the_owner() -> None:
    library, fake = _library()
    device = bindings.open_device(library=library)
    context = device.create_context()
    registration = context.start_foldback(
        bindings.AL_FOLDBACK_MODE_MONO,
        2,
        2,
        (native_types.ALfloat * 4)(),
        lambda _event, _index: None,
    )
    native_callback = fake.al.foldback_callback
    assert native_callback is not None

    native_callback(bindings.AL_FOLDBACK_EVENT_STOP, 0)

    assert not registration.closed
    assert registration._callback is native_callback
    registration.close()
    assert registration.closed
    assert [call[0] for call in fake.al.foldback_calls] == ["start"]
    context.close()
    device.close()


@pytest.mark.parametrize(
    ("mode", "count", "length", "float_count", "message"),
    [
        (bindings.AL_FOLDBACK_MODE_MONO, 1, 1, 1, "at least two"),
        (bindings.AL_FOLDBACK_MODE_MONO, 2, 2, 3, "at least 4"),
        (bindings.AL_FOLDBACK_MODE_STEREO, 2, 2, 7, "at least 8"),
        (0xDEAD, 2, 2, 8, "MONO or STEREO"),
    ],
)
def test_foldback_validates_mode_count_and_storage_capacity(
    mode: int,
    count: int,
    length: int,
    float_count: int,
    message: str,
) -> None:
    library, _fake = _library()
    memory = (native_types.ALfloat * float_count)()
    with (
        bindings.open_device(library=library) as device,
        device.create_context() as context,
        pytest.raises(ValueError, match=message),
    ):
        context.start_foldback(
            mode,
            count,
            length,
            memory,
            lambda _event, _index: None,
        )


def test_foldback_rejects_a_preexisting_al_error_before_start() -> None:
    library, fake = _library()
    fake.al.error = bindings.AL_INVALID_OPERATION
    with (
        bindings.open_device(library=library) as device,
        device.create_context() as context,
        pytest.raises(bindings.NativeCallError, match="pre-existing"),
    ):
        context.start_foldback(
            bindings.AL_FOLDBACK_MODE_MONO,
            2,
            2,
            (native_types.ALfloat * 4)(),
            lambda _event, _index: None,
        )

    assert fake.al.foldback_calls == []


def test_static_buffer_data_retains_exact_native_backing() -> None:
    library, fake = _library()
    data = bytearray(b"static audio")
    with bindings.open_device(library=library) as device:
        context = device.create_context()
        context.set_static_buffer_data(
            9,
            bindings.AL_FORMAT_MONO8,
            data,
            22_050,
        )

        assert fake.al.static_calls == [
            (9, bindings.AL_FORMAT_MONO8, b"static audio", 22_050)
        ]
        assert 9 in context._static_buffers
        context.close()
        assert not context._static_buffers


def test_static_buffer_data_does_not_retain_a_failed_update() -> None:
    library, fake = _library()
    fake.al.static_error = bindings.AL_INVALID_VALUE
    with bindings.open_device(library=library) as device:
        context = device.create_context()
        with pytest.raises(bindings.NativeCallError, match="static buffer update"):
            context.set_static_buffer_data(
                9,
                bindings.AL_FORMAT_MONO8,
                b"static audio",
                22_050,
            )

        assert 9 not in context._static_buffers
        context.close()
