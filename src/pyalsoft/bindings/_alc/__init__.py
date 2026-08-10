"""Owned ALC device and context handles over the generated command API."""

from pyalsoft.bindings._alc.callbacks import (
    BufferCallback,
    CallbackRegistration,
    DebugCallback,
    EventCallback,
    FoldbackCallback,
    FoldbackRegistration,
    SystemEventCallback,
)
from pyalsoft.bindings._alc.context import Context
from pyalsoft.bindings._alc.devices import (
    CaptureDevice,
    Device,
    LoopbackDevice,
    PlaybackDevice,
    open_capture_device,
    open_device,
    open_loopback_device,
)
from pyalsoft.bindings._alc.errors import (
    ALCHandleError,
    CallbackControlError,
    ContextActivationError,
    ContextCreateError,
    DeviceCloseError,
    DeviceOpenError,
    HandleClosedError,
    NativeCallError,
)
from pyalsoft.bindings._alc.system_events import (
    _clear_system_event_callback as _clear_system_event_callback,
)
from pyalsoft.bindings._alc.system_events import (
    _register_system_event_callback as _register_system_event_callback,
)

__all__ = [
    "ALCHandleError",
    "BufferCallback",
    "CallbackControlError",
    "CallbackRegistration",
    "CaptureDevice",
    "Context",
    "ContextActivationError",
    "ContextCreateError",
    "DebugCallback",
    "Device",
    "DeviceCloseError",
    "DeviceOpenError",
    "EventCallback",
    "FoldbackCallback",
    "FoldbackRegistration",
    "HandleClosedError",
    "LoopbackDevice",
    "NativeCallError",
    "PlaybackDevice",
    "SystemEventCallback",
    "open_capture_device",
    "open_device",
    "open_loopback_device",
]
