"""Exceptions raised by owned ALC handles and callbacks."""

from pyalsoft.bindings._library import OpenALError


class ALCHandleError(OpenALError):
    """Base exception for owned ALC device and context handles."""


class DeviceOpenError(ALCHandleError):
    """Raised when an ALC device cannot be opened."""


class DeviceCloseError(ALCHandleError):
    """Raised when an ALC device refuses to close."""


class ContextCreateError(ALCHandleError):
    """Raised when an ALC context cannot be created."""


class ContextActivationError(ALCHandleError):
    """Raised when an ALC context cannot be made current or restored."""


class HandleClosedError(ALCHandleError):
    """Raised when an operation requires an open device or context."""


class NativeCallError(ALCHandleError):
    """Raised when a lifetime-sensitive native operation reports an error."""


class CallbackControlError(NativeCallError):
    """Raised when native callback state cannot be enabled or removed safely."""
