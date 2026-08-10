"""Lifetime-safe native callback registrations and retained storage."""

from __future__ import annotations

import ctypes
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, ExitStack, contextmanager

from pyalsoft.bindings._alc.errors import CallbackControlError, HandleClosedError
from pyalsoft.bindings._generated import types as _types
from pyalsoft.bindings._library import _pointer_address

type EventCallback = Callable[[int, int, int, str], None]
type DebugCallback = Callable[[int, int, int, int, str], None]
type SystemEventCallback = Callable[[int, int, object | None, str], None]
type BufferCallback = Callable[[memoryview], int]
type FoldbackCallback = Callable[[int, int], None]


def _message_text(message: bytes | None, length: int) -> str:
    if not message:
        return ""
    encoded = message[: max(0, length)].rstrip(b"\0")
    return encoded.decode("utf-8", errors="replace")


def _retained_byte_buffer(data: object) -> tuple[object, int, tuple[object, ...]]:
    if isinstance(data, bytes):
        backing = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        return backing, len(data), (data, backing)
    if isinstance(data, bytearray):
        if not data:
            backing = (ctypes.c_ubyte * 0)()
        else:
            backing = (ctypes.c_ubyte * len(data)).from_buffer(data)
        return backing, len(data), (data, backing)
    if isinstance(data, memoryview):
        try:
            view = data.cast("B")
        except (TypeError, ValueError) as error:
            raise TypeError("data must be a contiguous byte buffer") from error
        array_type = ctypes.c_ubyte * view.nbytes
        if not view.nbytes:
            backing = array_type()
        elif view.readonly:
            backing = array_type.from_buffer_copy(view)
        else:
            backing = array_type.from_buffer(view)
        return backing, view.nbytes, (data, view, backing)
    raise TypeError("data must be bytes, bytearray, or memoryview")


def _retained_float_buffer(
    memory: object,
) -> tuple[object, int, tuple[object, ...]]:
    if isinstance(memory, ctypes.Array):
        element_type = getattr(memory, "_type_", None)
        if element_type is not _types.ALfloat:
            raise TypeError("ctypes foldback memory must be an ALfloat array")
        if not len(memory):
            raise ValueError("foldback memory cannot be empty")
        return memory, len(memory), (memory,)
    if isinstance(memory, (bytearray, memoryview)):
        source = memoryview(memory)
        try:
            view = source.cast("B")
        except (TypeError, ValueError) as error:
            raise TypeError("foldback memory must be contiguous") from error
        if view.readonly:
            raise TypeError("foldback memory must be writable")
        item_size = ctypes.sizeof(_types.ALfloat)
        if view.nbytes % item_size:
            raise ValueError("foldback memory size must be a multiple of ALfloat")
        if not view.nbytes:
            raise ValueError("foldback memory cannot be empty")
        backing = (_types.ALfloat * (view.nbytes // item_size)).from_buffer(view)
        return backing, len(backing), (memory, source, view, backing)
    if isinstance(memory, Sequence) and not isinstance(memory, (str, bytes)):
        if len(memory) == 0:
            raise ValueError("foldback memory cannot be empty")
        try:
            backing = (_types.ALfloat * len(memory))(*memory)
        except (TypeError, ValueError) as error:
            raise TypeError("foldback memory must contain real numbers") from error
        return backing, len(backing), (memory, backing)
    raise TypeError(
        "foldback memory must be an ALfloat array, writable buffer, or sequence"
    )


def _callback_buffer(address_value: object, size: int) -> memoryview:
    address = _pointer_address(address_value)
    if address is None:
        raise ValueError("OpenAL supplied a null callback sample buffer")
    array = (ctypes.c_ubyte * size).from_address(address)
    return memoryview(array).cast("B")


class CallbackRegistration:
    """Own a native callback and unregister it deterministically.

    Native audio callbacks must never allow a Python exception to cross the C
    boundary. Exceptions are retained and can be observed through :attr:`errors`
    or re-raised in the registering thread with :meth:`raise_if_failed`.
    """

    def __init__(
        self,
        callback: object,
        close: Callable[[CallbackRegistration], None],
        errors: list[BaseException],
        *,
        resources: Sequence[object] = (),
        owner_locks: Sequence[AbstractContextManager[object]] = (),
    ) -> None:
        self._callback = callback
        self._close = close
        self._errors = errors
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._closed = False
        self._closing = False
        self._closing_thread: int | None = None
        self._callback_threads: dict[int, int] = {}
        self._resources = tuple(resources)
        self._owner_locks = tuple(owner_locks)

    @contextmanager
    def _serialized(self) -> Iterator[None]:
        with ExitStack() as stack:
            for lock in self._owner_locks:
                stack.enter_context(lock)
            yield

    def _finish_close_locked(self) -> None:
        self._closed = True
        self._closing = False
        self._closing_thread = None
        self._callback = None
        self._resources = ()
        self._condition.notify_all()

    @property
    def closed(self) -> bool:
        """Whether the callback has been unregistered."""

        with self._lock:
            return self._closed

    @property
    def errors(self) -> tuple[BaseException, ...]:
        """Exceptions raised by the Python callback, in arrival order."""

        with self._lock:
            return tuple(self._errors)

    def raise_if_failed(self) -> None:
        """Raise and clear exceptions retained from native callback threads."""

        with self._lock:
            errors = tuple(self._errors)
            self._errors.clear()
        if errors:
            raise BaseExceptionGroup("OpenAL callback failed", errors)

    def _record_error(self, error: BaseException) -> None:
        with self._lock:
            self._errors.append(error)

    def _begin_callback(self) -> None:
        with self._condition:
            thread = threading.get_ident()
            self._callback_threads[thread] = self._callback_threads.get(thread, 0) + 1

    def _end_callback(self) -> None:
        with self._condition:
            thread = threading.get_ident()
            remaining = self._callback_threads[thread] - 1
            if remaining:
                self._callback_threads[thread] = remaining
            else:
                self._callback_threads.pop(thread)
            self._condition.notify_all()

    def close(self) -> None:
        """Unregister the callback. Calling this more than once is harmless."""

        thread = threading.get_ident()
        while True:
            initiated = False
            with self._serialized():
                with self._condition:
                    if thread in self._callback_threads:
                        raise CallbackControlError(
                            "callback registration cannot be closed from its callback"
                        )
                    if self._closed:
                        return
                    if self._closing_thread == thread:
                        raise CallbackControlError(
                            "callback registration close cannot be re-entered"
                        )
                    if not self._closing:
                        self._closing = True
                        self._closing_thread = thread
                        initiated = True

                if initiated:
                    try:
                        self._close(self)
                    except BaseException:
                        with self._condition:
                            self._closing = False
                            self._closing_thread = None
                            self._condition.notify_all()
                        raise

            with self._condition:
                if initiated:
                    while self._callback_threads:
                        self._condition.wait()
                    self._finish_close_locked()
                    return
                while self._closing:
                    self._condition.wait()

    def _owner_closed(self) -> None:
        """Finalize after the native owner has destroyed callback state."""

        thread = threading.get_ident()
        while True:
            initiated = False
            with self._serialized(), self._condition:
                if self._closed:
                    return
                if not self._closing:
                    self._closing = True
                    self._closing_thread = thread
                    initiated = True
            with self._condition:
                if initiated:
                    while self._callback_threads:
                        self._condition.wait()
                    self._finish_close_locked()
                    return
                while self._closing:
                    self._condition.wait()

    def __enter__(self) -> CallbackRegistration:
        if self.closed:
            raise HandleClosedError("callback registration is closed")
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object | None,
    ) -> None:
        self.close()


class FoldbackRegistration(CallbackRegistration):
    """Own an active foldback request and its writable sample storage."""

    def __init__(
        self,
        callback: object,
        close: Callable[[CallbackRegistration], None],
        errors: list[BaseException],
        memory: object,
        *,
        resources: Sequence[object] = (),
        owner_locks: Sequence[AbstractContextManager[object]] = (),
    ) -> None:
        super().__init__(
            callback,
            close,
            errors,
            resources=(memory, *resources),
            owner_locks=owner_locks,
        )
        self.memory = memory
        self._stop_requested = False
        self._stop_received = False

    @property
    def stopping(self) -> bool:
        """Whether native foldback stop has been requested."""

        with self._lock:
            return self._stop_requested and not self._closed

    def _native_stopped(self) -> None:
        with self._condition:
            self._stop_received = True
            self._condition.notify_all()

    def close(self) -> None:
        """Request foldback stop and wait for the native STOP event."""

        with self._serialized():
            with self._condition:
                thread = threading.get_ident()
                if self._closed:
                    return
                if thread in self._callback_threads:
                    raise CallbackControlError(
                        "foldback cannot be closed from its native callback"
                    )
                if self._closing:
                    if self._closing_thread == thread:
                        raise CallbackControlError(
                            "foldback close cannot be re-entered"
                        )
                    while not self._closed:
                        self._condition.wait()
                    return
                self._closing = True
                self._closing_thread = thread
                stop_received = self._stop_received

            try:
                if not stop_received:
                    self._close(self)
            except BaseException:
                with self._condition:
                    self._closing = False
                    self._closing_thread = None
                    self._condition.notify_all()
                raise

            with self._condition:
                if not stop_received:
                    self._stop_requested = True
                while not self._stop_received or self._callback_threads:
                    self._condition.wait()
                self._finish_close_locked()
