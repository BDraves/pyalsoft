"""Functional, managed playback API built on the low-level bindings."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from types import TracebackType
from typing import Self, cast, overload

from pyalsoft import bindings

type Vector3 = tuple[float, float, float]


class AudioError(Exception):
    """Base exception for the managed audio API."""


class PlaybackOpenError(AudioError):
    """Raised when a playback device or context cannot be opened."""


class AudioBackendError(AudioError):
    """Raised when OpenAL rejects a managed API operation."""


class PlaybackClosedError(AudioError):
    """Raised when an operation uses a closed playback session."""


class InvalidHandleError(AudioError):
    """Raised when a clip or voice is stale or belongs to another session."""


class ResourceInUseError(AudioError):
    """Raised when a resource is still referenced by another live resource."""


class SampleType(Enum):
    """PCM sample representations supported by core OpenAL."""

    UINT8 = "uint8"
    INT16 = "int16"

    @property
    def byte_width(self) -> int:
        """Number of bytes used by one channel sample."""

        return 1 if self is SampleType.UINT8 else 2


class VoiceState(Enum):
    """Observed playback state of a voice."""

    INITIAL = "initial"
    PLAYING = "playing"
    PAUSED = "paused"
    STOPPED = "stopped"


def _finite_float(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")
    converted = float(value)
    if not math.isfinite(converted):
        raise ValueError(f"{name} must be finite")
    return converted


def _vector3(name: str, value: Vector3) -> Vector3:
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        raise TypeError(f"{name} must be a three-item tuple or list")
    return cast(
        Vector3,
        tuple(
            _finite_float(f"{name}[{index}]", item) for index, item in enumerate(value)
        ),
    )


@dataclass(frozen=True, slots=True)
class PCM:
    """Immutable, interleaved PCM sample data ready to upload."""

    samples: bytes
    channels: int
    sample_rate: int
    sample_type: SampleType = SampleType.INT16

    def __post_init__(self) -> None:
        if not isinstance(self.samples, (bytes, bytearray, memoryview)):
            raise TypeError("samples must be bytes-like")
        samples = bytes(self.samples)
        if not samples:
            raise ValueError("samples cannot be empty")
        if self.channels not in (1, 2):
            raise ValueError("channels must be 1 or 2")
        if isinstance(self.sample_rate, bool) or not isinstance(self.sample_rate, int):
            raise TypeError("sample_rate must be an integer")
        if self.sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        if not isinstance(self.sample_type, SampleType):
            raise TypeError("sample_type must be a SampleType")
        frame_width = self.channels * self.sample_type.byte_width
        if len(samples) % frame_width:
            raise ValueError("samples must contain a whole number of frames")
        object.__setattr__(self, "samples", samples)

    @property
    def frame_count(self) -> int:
        """Number of sample frames in this PCM value."""

        return len(self.samples) // (self.channels * self.sample_type.byte_width)

    @property
    def duration(self) -> float:
        """Duration of this PCM value in seconds."""

        return self.frame_count / self.sample_rate


@dataclass(frozen=True, slots=True)
class VoiceConfig:
    """Desired configurable state for one playing voice."""

    position: Vector3 = (0.0, 0.0, 0.0)
    velocity: Vector3 = (0.0, 0.0, 0.0)
    direction: Vector3 = (0.0, 0.0, 0.0)
    gain: float = 1.0
    pitch: float = 1.0
    looping: bool = False
    relative: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "position", _vector3("position", self.position))
        object.__setattr__(self, "velocity", _vector3("velocity", self.velocity))
        object.__setattr__(self, "direction", _vector3("direction", self.direction))
        gain = _finite_float("gain", self.gain)
        if gain < 0.0:
            raise ValueError("gain cannot be negative")
        pitch = _finite_float("pitch", self.pitch)
        if not 0.5 <= pitch <= 2.0:
            raise ValueError("pitch must be between 0.5 and 2.0")
        if not isinstance(self.looping, bool):
            raise TypeError("looping must be a boolean")
        if not isinstance(self.relative, bool):
            raise TypeError("relative must be a boolean")
        object.__setattr__(self, "gain", gain)
        object.__setattr__(self, "pitch", pitch)


@dataclass(frozen=True, slots=True)
class Listener:
    """Desired spatial state for the playback context's listener."""

    position: Vector3 = (0.0, 0.0, 0.0)
    velocity: Vector3 = (0.0, 0.0, 0.0)
    forward: Vector3 = (0.0, 0.0, -1.0)
    up: Vector3 = (0.0, 1.0, 0.0)
    gain: float = 1.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "position", _vector3("position", self.position))
        object.__setattr__(self, "velocity", _vector3("velocity", self.velocity))
        forward = _vector3("forward", self.forward)
        up = _vector3("up", self.up)
        if forward == (0.0, 0.0, 0.0):
            raise ValueError("forward cannot be a zero vector")
        if up == (0.0, 0.0, 0.0):
            raise ValueError("up cannot be a zero vector")
        gain = _finite_float("gain", self.gain)
        if gain < 0.0:
            raise ValueError("gain cannot be negative")
        object.__setattr__(self, "forward", forward)
        object.__setattr__(self, "up", up)
        object.__setattr__(self, "gain", gain)


@dataclass(frozen=True, slots=True)
class VoiceStatus:
    """Runtime state observed from a voice."""

    state: VoiceState
    offset_seconds: float


@dataclass(frozen=True, slots=True)
class Clip:
    """Opaque identity for PCM uploaded to a playback session."""

    _owner: object = field(repr=False)
    _token: object = field(repr=False)
    _identifier: int = field(repr=False)

    def __repr__(self) -> str:
        return "Clip(<opaque>)"


@dataclass(frozen=True, slots=True)
class Voice:
    """Opaque identity for one playback instance of a clip."""

    _owner: object = field(repr=False)
    _token: object = field(repr=False)
    _identifier: int = field(repr=False)

    def __repr__(self) -> str:
        return "Voice(<opaque>)"


class Playback:
    """Opaque owner for a playback device, context, clips, and voices.

    Instances are returned by :func:`open_playback`. Use them as context
    managers or pass them to :func:`close_playback` for deterministic cleanup.
    """

    __slots__ = (
        "_clips",
        "_closed",
        "_context",
        "_device",
        "_library",
        "_previous_context",
        "_token",
        "_voice_clips",
        "_voices",
    )

    def __init__(
        self,
        library: bindings.OpenALLibrary,
        device: object,
        context: object,
        previous_context: object | None,
    ) -> None:
        self._library = library
        self._device = device
        self._context = context
        self._previous_context = previous_context
        self._token = object()
        self._clips: dict[object, int] = {}
        self._voices: dict[object, int] = {}
        self._voice_clips: dict[object, object] = {}
        self._closed = False

    def __enter__(self) -> Self:
        _activate(self)
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exception_type, traceback
        try:
            close_playback(self)
        except Exception as cleanup_error:
            if exception is None:
                raise
            exception.add_note(f"audio cleanup also failed: {cleanup_error}")


_FORMAT_BY_LAYOUT = {
    (1, SampleType.UINT8): bindings.enums.ALFormat.FORMAT_MONO8,
    (1, SampleType.INT16): bindings.enums.ALFormat.FORMAT_MONO16,
    (2, SampleType.UINT8): bindings.enums.ALFormat.FORMAT_STEREO8,
    (2, SampleType.INT16): bindings.enums.ALFormat.FORMAT_STEREO16,
}

_VOICE_STATE_BY_AL = {
    int(bindings.enums.ALSourceState.INITIAL): VoiceState.INITIAL,
    int(bindings.enums.ALSourceState.PLAYING): VoiceState.PLAYING,
    int(bindings.enums.ALSourceState.PAUSED): VoiceState.PAUSED,
    int(bindings.enums.ALSourceState.STOPPED): VoiceState.STOPPED,
}

_DEFAULT_VOICE_CONFIG = VoiceConfig()


def _require_playback(playback: Playback) -> None:
    if not isinstance(playback, Playback):
        raise TypeError("playback must be a Playback")
    if playback._closed:
        raise PlaybackClosedError("playback session is closed")


def _activate(playback: Playback) -> None:
    _require_playback(playback)
    if not playback._library.alc.make_context_current(playback._context):
        raise AudioBackendError("could not make the playback context current")


def _clear_al_errors(playback: Playback) -> None:
    for _ in range(16):
        if int(playback._library.al.get_error()) == bindings.AL_NO_ERROR:
            return
    raise AudioBackendError("OpenAL error state could not be cleared")


def _prepare_al(playback: Playback) -> None:
    _activate(playback)
    _clear_al_errors(playback)


def _check_al_error(playback: Playback, operation: str) -> None:
    code = int(playback._library.al.get_error())
    if code == bindings.AL_NO_ERROR:
        return
    try:
        name = bindings.enums.ALErrorCode(code).name
    except ValueError:
        name = f"unknown error 0x{code:04x}"
    raise AudioBackendError(f"{operation} failed: OpenAL {name}")


def _clip_identifier(playback: Playback, clip: Clip) -> int:
    _require_playback(playback)
    if not isinstance(clip, Clip) or clip._owner is not playback._token:
        raise InvalidHandleError("clip does not belong to this playback session")
    identifier = playback._clips.get(clip._token)
    if identifier is None or identifier != clip._identifier:
        raise InvalidHandleError("clip has been released")
    return identifier


def _voice_identifier(playback: Playback, voice: Voice) -> int:
    _require_playback(playback)
    if not isinstance(voice, Voice) or voice._owner is not playback._token:
        raise InvalidHandleError("voice does not belong to this playback session")
    identifier = playback._voices.get(voice._token)
    if identifier is None or identifier != voice._identifier:
        raise InvalidHandleError("voice has been released")
    return identifier


def _apply_voice_config(
    playback: Playback, identifier: int, config: VoiceConfig
) -> None:
    al = playback._library.al
    al.source3f(identifier, bindings.AL_POSITION, *config.position)
    al.source3f(identifier, bindings.AL_VELOCITY, *config.velocity)
    al.source3f(identifier, bindings.AL_DIRECTION, *config.direction)
    al.sourcef(identifier, bindings.AL_GAIN, config.gain)
    al.sourcef(identifier, bindings.AL_PITCH, config.pitch)
    al.sourcei(identifier, bindings.AL_LOOPING, int(config.looping))
    al.sourcei(identifier, bindings.AL_SOURCE_RELATIVE, int(config.relative))


def open_playback(
    device_name: str | bytes | None = None,
    *,
    library: bindings.OpenALLibrary | None = None,
) -> Playback:
    """Open a managed playback session."""

    library = library or bindings.load()
    previous_context = library.alc.get_current_context()
    device = library.alc.open_device(device_name)
    if not device:
        raise PlaybackOpenError("could not open the requested playback device")
    context: object | None = None
    try:
        context = library.alc.create_context(device, None)
        if not context:
            raise PlaybackOpenError("could not create an OpenAL context")
        if not library.alc.make_context_current(context):
            raise PlaybackOpenError("could not make the OpenAL context current")
    except Exception:
        if context is not None:
            library.alc.destroy_context(context)
        library.alc.close_device(device)
        raise
    return Playback(library, device, context, previous_context)


def close_playback(playback: Playback) -> None:
    """Release every resource and close a playback session.

    Closing an already closed session is harmless.
    """

    if not isinstance(playback, Playback):
        raise TypeError("playback must be a Playback")
    if playback._closed:
        return

    first_error: Exception | None = None

    def remember(error: Exception) -> None:
        nonlocal first_error
        if first_error is None:
            first_error = error

    try:
        if not playback._library.alc.make_context_current(playback._context):
            remember(AudioBackendError("could not activate context for cleanup"))
        else:
            try:
                _clear_al_errors(playback)
                voice_ids = tuple(playback._voices.values())
                if voice_ids:
                    playback._library.al.source_stopv(voice_ids)
                    playback._library.al.delete_sources(voice_ids)
                clip_ids = tuple(playback._clips.values())
                if clip_ids:
                    playback._library.al.delete_buffers(clip_ids)
                _check_al_error(playback, "audio cleanup")
            except Exception as error:
                remember(error)
    finally:
        try:
            if not playback._library.alc.make_context_current(
                playback._previous_context
            ):
                remember(AudioBackendError("could not restore the previous context"))
        except Exception as error:
            remember(error)
        try:
            playback._library.alc.destroy_context(playback._context)
        except Exception as error:
            remember(error)
        try:
            if not playback._library.alc.close_device(playback._device):
                remember(AudioBackendError("could not close the playback device"))
        except Exception as error:
            remember(error)
        playback._voices.clear()
        playback._voice_clips.clear()
        playback._clips.clear()
        playback._closed = True

    if first_error is not None:
        raise first_error


def upload(playback: Playback, pcm: PCM) -> Clip:
    """Upload immutable PCM data and return its opaque clip identity."""

    if not isinstance(pcm, PCM):
        raise TypeError("pcm must be a PCM value")
    _prepare_al(playback)
    identifiers = playback._library.al.gen_buffers()
    if len(identifiers) != 1:
        raise AudioBackendError("OpenAL did not create exactly one buffer")
    identifier = identifiers[0]
    try:
        _check_al_error(playback, "create clip")
        playback._library.al.buffer_data(
            identifier,
            _FORMAT_BY_LAYOUT[(pcm.channels, pcm.sample_type)],
            pcm.samples,
            pcm.sample_rate,
        )
        _check_al_error(playback, "upload clip")
    except Exception:
        _clear_al_errors(playback)
        playback._library.al.delete_buffers((identifier,))
        playback._library.al.get_error()
        raise
    token = object()
    playback._clips[token] = identifier
    return Clip(playback._token, token, identifier)


def play(
    playback: Playback,
    clip: Clip,
    config: VoiceConfig = _DEFAULT_VOICE_CONFIG,
) -> Voice:
    """Create and immediately play one voice using a clip."""

    if not isinstance(config, VoiceConfig):
        raise TypeError("config must be a VoiceConfig")
    clip_identifier = _clip_identifier(playback, clip)
    _prepare_al(playback)
    identifiers = playback._library.al.gen_sources()
    if len(identifiers) != 1:
        raise AudioBackendError("OpenAL did not create exactly one source")
    identifier = identifiers[0]
    try:
        _check_al_error(playback, "create voice")
        _apply_voice_config(playback, identifier, config)
        playback._library.al.sourcei(identifier, bindings.AL_BUFFER, clip_identifier)
        playback._library.al.source_play(identifier)
        _check_al_error(playback, "play voice")
    except Exception:
        _clear_al_errors(playback)
        playback._library.al.source_stop(identifier)
        playback._library.al.delete_sources((identifier,))
        playback._library.al.get_error()
        raise
    token = object()
    playback._voices[token] = identifier
    playback._voice_clips[token] = clip._token
    return Voice(playback._token, token, identifier)


def configure_voice(playback: Playback, voice: Voice, config: VoiceConfig) -> None:
    """Apply a complete immutable configuration to a live voice."""

    if not isinstance(config, VoiceConfig):
        raise TypeError("config must be a VoiceConfig")
    identifier = _voice_identifier(playback, voice)
    _prepare_al(playback)
    _apply_voice_config(playback, identifier, config)
    _check_al_error(playback, "configure voice")


def set_listener(playback: Playback, listener: Listener) -> None:
    """Apply an immutable listener description to the playback context."""

    if not isinstance(listener, Listener):
        raise TypeError("listener must be a Listener")
    _prepare_al(playback)
    al = playback._library.al
    al.listener3f(bindings.AL_POSITION, *listener.position)
    al.listener3f(bindings.AL_VELOCITY, *listener.velocity)
    al.listenerfv(bindings.AL_ORIENTATION, listener.forward + listener.up)
    al.listenerf(bindings.AL_GAIN, listener.gain)
    _check_al_error(playback, "configure listener")


def get_voice_status(playback: Playback, voice: Voice) -> VoiceStatus:
    """Return the current state and playback offset of a live voice."""

    identifier = _voice_identifier(playback, voice)
    _prepare_al(playback)
    raw_state = int(
        playback._library.al.get_sourcei(identifier, bindings.AL_SOURCE_STATE)
    )
    offset = playback._library.al.get_sourcef(identifier, bindings.AL_SEC_OFFSET)
    _check_al_error(playback, "query voice")
    try:
        state = _VOICE_STATE_BY_AL[raw_state]
    except KeyError as error:
        raise AudioBackendError(
            f"OpenAL returned unknown voice state 0x{raw_state:04x}"
        ) from error
    return VoiceStatus(state=state, offset_seconds=float(offset))


def _control_voice(playback: Playback, voice: Voice, operation: str) -> None:
    identifier = _voice_identifier(playback, voice)
    _prepare_al(playback)
    function = getattr(playback._library.al, f"source_{operation}")
    function(identifier)
    _check_al_error(playback, f"{operation} voice")


def pause(playback: Playback, voice: Voice) -> None:
    """Pause a live voice."""

    _control_voice(playback, voice, "pause")


def resume(playback: Playback, voice: Voice) -> None:
    """Resume a paused voice, or restart a stopped voice."""

    _control_voice(playback, voice, "play")


def stop(playback: Playback, voice: Voice) -> None:
    """Stop a live voice without releasing its identity."""

    _control_voice(playback, voice, "stop")


@overload
def release(playback: Playback, resource: Clip) -> None: ...


@overload
def release(playback: Playback, resource: Voice) -> None: ...


def release(playback: Playback, resource: Clip | Voice) -> None:
    """Release a clip or voice before its playback session closes."""

    if isinstance(resource, Voice):
        identifier = _voice_identifier(playback, resource)
        _prepare_al(playback)
        playback._library.al.source_stop(identifier)
        playback._library.al.delete_sources((identifier,))
        _check_al_error(playback, "release voice")
        del playback._voices[resource._token]
        del playback._voice_clips[resource._token]
        return
    if isinstance(resource, Clip):
        identifier = _clip_identifier(playback, resource)
        if resource._token in playback._voice_clips.values():
            raise ResourceInUseError("clip is still attached to a live voice")
        _prepare_al(playback)
        playback._library.al.delete_buffers((identifier,))
        _check_al_error(playback, "release clip")
        del playback._clips[resource._token]
        return
    raise TypeError("resource must be a Clip or Voice")


__all__ = [
    "AudioBackendError",
    "AudioError",
    "Clip",
    "InvalidHandleError",
    "Listener",
    "PCM",
    "Playback",
    "PlaybackClosedError",
    "PlaybackOpenError",
    "ResourceInUseError",
    "SampleType",
    "Vector3",
    "Voice",
    "VoiceConfig",
    "VoiceState",
    "VoiceStatus",
    "close_playback",
    "configure_voice",
    "get_voice_status",
    "open_playback",
    "pause",
    "play",
    "release",
    "resume",
    "set_listener",
    "stop",
    "upload",
]
