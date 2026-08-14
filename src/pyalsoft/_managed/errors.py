"""Exceptions raised by the managed audio APIs."""


class AudioError(Exception):
    """Base exception for the managed audio API."""


class AudioFileError(AudioError):
    """Raised when a file cannot be decoded by the convenience API."""


class PlaybackOpenError(AudioError):
    """Raised when a playback device or context cannot be opened."""


class AudioBackendError(AudioError):
    """Raised when OpenAL rejects a managed API operation."""


class PlaybackClosedError(AudioError):
    """Raised when an operation uses a closed playback session."""


class InvalidHandleError(AudioError):
    """Raised when a resource is stale or belongs to another session."""


class ResourceInUseError(AudioError):
    """Raised when a resource is still referenced by another live resource."""


class InvalidVoiceStateError(AudioError):
    """Raised when an operation is not valid for a voice's current state."""
