"""Function-oriented managed playback API built on the low-level bindings."""

from __future__ import annotations

import atexit
import math
import wave
from collections import deque
from collections.abc import Buffer
from contextlib import suppress
from dataclasses import dataclass, field
from enum import Enum
from os import PathLike
from pathlib import Path
from threading import RLock
from types import TracebackType
from typing import Self, cast, overload

from pyalsoft import bindings

type Vector3 = tuple[float, float, float]
type AudioPath = str | PathLike[str]


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


class StreamState(Enum):
    """Managed lifecycle state of a stream."""

    INITIAL = "initial"
    PLAYING = "playing"
    PAUSED = "paused"
    FINISHED = "finished"
    STOPPED = "stopped"


class HRTFStatus(Enum):
    """Observed HRTF state for an open playback session."""

    UNAVAILABLE = "unavailable"
    DISABLED = "disabled"
    ENABLED = "enabled"
    DENIED = "denied"
    REQUIRED = "required"
    HEADPHONES_DETECTED = "headphones_detected"
    UNSUPPORTED_FORMAT = "unsupported_format"
    UNKNOWN = "unknown"


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
class PlaybackDevice:
    """A named playback device reported by the current OpenAL runtime."""

    name: str
    is_default: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.name, str):
            raise TypeError("name must be a string")
        if not self.name:
            raise ValueError("name cannot be empty")
        if not isinstance(self.is_default, bool):
            raise TypeError("is_default must be a boolean")


@dataclass(frozen=True, slots=True, kw_only=True)
class PlaybackConfig:
    """Preferences applied while creating an OpenAL playback context."""

    hrtf: bool | None = None

    def __post_init__(self) -> None:
        if self.hrtf is not None and not isinstance(self.hrtf, bool):
            raise TypeError("hrtf must be a boolean or None")


@dataclass(frozen=True, slots=True)
class PlaybackInfo:
    """Observed properties of an open playback session."""

    device_name: str
    renderer: str
    version: str
    hrtf_status: HRTFStatus
    hrtf_name: str | None


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


@dataclass(frozen=True, slots=True, kw_only=True)
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


@dataclass(frozen=True, slots=True, kw_only=True)
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
class StreamStatus:
    """Runtime state and queue accounting for a stream."""

    state: StreamState
    input_finished: bool
    queued_chunks: int
    queued_seconds: float
    underrun_count: int


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


@dataclass(frozen=True, slots=True)
class Stream:
    """Opaque identity for one managed streaming source."""

    _owner: object = field(repr=False)
    _token: object = field(repr=False)
    _identifier: int = field(repr=False)

    def __repr__(self) -> str:
        return "Stream(<opaque>)"


@dataclass(frozen=True, slots=True)
class _StreamChunk:
    buffer: int
    frame_count: int
    duration: float


@dataclass(slots=True)
class _StreamRecord:
    identifier: int
    buffers: tuple[int, ...]
    free_buffers: deque[int]
    queued_chunks: deque[_StreamChunk]
    channels: int
    sample_rate: int
    sample_type: SampleType
    state: StreamState = StreamState.INITIAL
    input_finished: bool = False
    underrun_count: int = 0
    underrun_active: bool = False


class Playback:
    """Opaque owner for a playback device, context, clips, voices, and streams.

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
        "_streams",
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
        self._streams: dict[object, _StreamRecord] = {}
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
_DEFAULT_PLAYBACK_CONFIG = PlaybackConfig()

_HRTF_STATUS_BY_ALC = {
    bindings.ALC_HRTF_DISABLED_SOFT: HRTFStatus.DISABLED,
    bindings.ALC_HRTF_ENABLED_SOFT: HRTFStatus.ENABLED,
    bindings.ALC_HRTF_DENIED_SOFT: HRTFStatus.DENIED,
    bindings.ALC_HRTF_REQUIRED_SOFT: HRTFStatus.REQUIRED,
    bindings.ALC_HRTF_HEADPHONES_DETECTED_SOFT: HRTFStatus.HEADPHONES_DETECTED,
    bindings.ALC_HRTF_UNSUPPORTED_FORMAT_SOFT: HRTFStatus.UNSUPPORTED_FORMAT,
}


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


def _clear_alc_errors(library: bindings.OpenALLibrary, device: object | None) -> None:
    for _ in range(16):
        if int(library.alc.get_error(device)) == bindings.ALC_NO_ERROR:
            return
    raise AudioBackendError("OpenAL ALC error state could not be cleared")


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


def _check_alc_error(
    library: bindings.OpenALLibrary,
    device: object | None,
    operation: str,
) -> None:
    code = int(library.alc.get_error(device))
    if code == bindings.ALC_NO_ERROR:
        return
    try:
        name = bindings.enums.ALCContextErrorCode(code).name
    except ValueError:
        name = f"unknown error 0x{code:04x}"
    raise AudioBackendError(f"{operation} failed: OpenAL ALC {name}")


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


def _stream_record(playback: Playback, stream: Stream) -> _StreamRecord:
    _require_playback(playback)
    if not isinstance(stream, Stream) or stream._owner is not playback._token:
        raise InvalidHandleError("stream does not belong to this playback session")
    record = playback._streams.get(stream._token)
    if record is None or record.identifier != stream._identifier:
        raise InvalidHandleError("stream has been released")
    return record


def _validate_stream_layout(
    channels: int,
    sample_rate: int,
    sample_type: SampleType,
    buffer_count: int,
) -> None:
    if channels not in (1, 2):
        raise ValueError("channels must be 1 or 2")
    if isinstance(sample_rate, bool) or not isinstance(sample_rate, int):
        raise TypeError("sample_rate must be an integer")
    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    if not isinstance(sample_type, SampleType):
        raise TypeError("sample_type must be a SampleType")
    if isinstance(buffer_count, bool) or not isinstance(buffer_count, int):
        raise TypeError("buffer_count must be an integer")
    if buffer_count <= 0:
        raise ValueError("buffer_count must be positive")


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


def _load_playback_library(
    library: bindings.OpenALLibrary | None,
) -> bindings.OpenALLibrary:
    if library is not None:
        return library
    try:
        return bindings.load()
    except bindings.LibraryNotFoundError as error:
        raise PlaybackOpenError("could not load an OpenAL library") from error


def list_playback_devices(
    *, library: bindings.OpenALLibrary | None = None
) -> tuple[PlaybackDevice, ...]:
    """Return playback devices known to the selected OpenAL runtime."""

    library = _load_playback_library(library)
    _clear_alc_errors(library, None)
    enumerate_all = library.alc.is_extension_present(None, "ALC_ENUMERATE_ALL_EXT")
    if enumerate_all:
        devices_selector = bindings.ALC_ALL_DEVICES_SPECIFIER
        default_selector = bindings.ALC_DEFAULT_ALL_DEVICES_SPECIFIER
    else:
        devices_selector = bindings.ALC_DEVICE_SPECIFIER
        default_selector = bindings.ALC_DEFAULT_DEVICE_SPECIFIER

    names = library.alc.get_strings(None, devices_selector)
    default_name = library.alc.get_string(None, default_selector)
    _check_alc_error(library, None, "enumerate playback devices")
    return tuple(
        PlaybackDevice(name, is_default=name == default_name)
        for name in dict.fromkeys(names)
    )


def open_playback(
    device_name: PlaybackDevice | str | bytes | None = None,
    *,
    config: PlaybackConfig = _DEFAULT_PLAYBACK_CONFIG,
    library: bindings.OpenALLibrary | None = None,
) -> Playback:
    """Open a managed playback session."""

    if not isinstance(config, PlaybackConfig):
        raise TypeError("config must be a PlaybackConfig")
    if isinstance(device_name, PlaybackDevice):
        device_name = device_name.name
    elif device_name is not None and not isinstance(device_name, (str, bytes)):
        raise TypeError("device_name must be a PlaybackDevice, str, bytes, or None")

    library = _load_playback_library(library)
    previous_context = library.alc.get_current_context()
    device = library.alc.open_device(device_name)
    if not device:
        raise PlaybackOpenError("could not open the requested playback device")
    context: object | None = None
    try:
        attributes: tuple[int, ...] | None = None
        if config.hrtf is not None and library.alc.is_extension_present(
            device, "ALC_SOFT_HRTF"
        ):
            attributes = (bindings.ALC_HRTF_SOFT, int(config.hrtf))
        context = library.alc.create_context(device, attributes)
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


def get_playback_info(playback: Playback) -> PlaybackInfo:
    """Return observed device, renderer, version, and HRTF information."""

    _prepare_al(playback)
    library = playback._library
    _clear_alc_errors(library, playback._device)
    device_name = library.alc.get_string(
        playback._device, bindings.ALC_DEVICE_SPECIFIER
    )
    if library.alc.is_extension_present(playback._device, "ALC_SOFT_HRTF"):
        native_status = library.alc.get_integerv(
            playback._device, bindings.ALC_HRTF_STATUS_SOFT, 1
        )[0]
        hrtf_status = _HRTF_STATUS_BY_ALC.get(native_status, HRTFStatus.UNKNOWN)
        hrtf_name = library.alc.get_string(
            playback._device, bindings.ALC_HRTF_SPECIFIER_SOFT
        )
        if not hrtf_name:
            hrtf_name = None
    else:
        hrtf_status = HRTFStatus.UNAVAILABLE
        hrtf_name = None
    _check_alc_error(library, playback._device, "query playback information")

    renderer = library.al.get_string(bindings.AL_RENDERER)
    version = library.al.get_string(bindings.AL_VERSION)
    _check_al_error(playback, "query playback information")
    if device_name is None or renderer is None or version is None:
        raise AudioBackendError("OpenAL returned incomplete playback information")

    return PlaybackInfo(
        device_name=device_name,
        renderer=renderer,
        version=version,
        hrtf_status=hrtf_status,
        hrtf_name=hrtf_name,
    )


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
                source_ids = tuple(playback._voices.values()) + tuple(
                    record.identifier for record in playback._streams.values()
                )
                if source_ids:
                    playback._library.al.source_stopv(source_ids)
                    playback._library.al.delete_sources(source_ids)
                buffer_ids = tuple(playback._clips.values()) + tuple(
                    identifier
                    for record in playback._streams.values()
                    for identifier in record.buffers
                )
                if buffer_ids:
                    playback._library.al.delete_buffers(buffer_ids)
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
        playback._streams.clear()
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


def _play_voice(
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


def open_stream(
    playback: Playback,
    *,
    channels: int,
    sample_rate: int,
    sample_type: SampleType = SampleType.INT16,
    buffer_count: int = 4,
    config: VoiceConfig = _DEFAULT_VOICE_CONFIG,
) -> Stream:
    """Create an unstarted source with a bounded pool of streaming buffers."""

    _validate_stream_layout(channels, sample_rate, sample_type, buffer_count)
    if not isinstance(config, VoiceConfig):
        raise TypeError("config must be a VoiceConfig")
    if config.looping:
        raise ValueError("streaming voices cannot loop")

    _prepare_al(playback)
    source_ids: tuple[int, ...] = ()
    buffer_ids: tuple[int, ...] = ()
    try:
        source_ids = playback._library.al.gen_sources()
        if len(source_ids) != 1:
            raise AudioBackendError("OpenAL did not create exactly one source")
        _check_al_error(playback, "create stream source")
        buffer_ids = playback._library.al.gen_buffers(buffer_count)
        if len(buffer_ids) != buffer_count:
            raise AudioBackendError(
                f"OpenAL did not create exactly {buffer_count} stream buffers"
            )
        _check_al_error(playback, "create stream buffers")
        _apply_voice_config(playback, source_ids[0], config)
        _check_al_error(playback, "configure stream")
    except Exception:
        _clear_al_errors(playback)
        if source_ids:
            playback._library.al.source_stopv(source_ids)
            playback._library.al.delete_sources(source_ids)
        if buffer_ids:
            playback._library.al.delete_buffers(buffer_ids)
        playback._library.al.get_error()
        raise

    token = object()
    identifier = source_ids[0]
    playback._streams[token] = _StreamRecord(
        identifier=identifier,
        buffers=buffer_ids,
        free_buffers=deque(buffer_ids),
        queued_chunks=deque(),
        channels=channels,
        sample_rate=sample_rate,
        sample_type=sample_type,
    )
    return Stream(playback._token, token, identifier)


def _copy_stream_samples(samples: Buffer) -> bytes:
    try:
        view = memoryview(samples)
    except TypeError as error:
        raise TypeError("samples must be bytes-like") from error
    try:
        return view.tobytes()
    finally:
        view.release()


def try_write_stream(
    playback: Playback,
    stream: Stream,
    samples: Buffer,
) -> bool:
    """Queue one complete PCM chunk, or report bounded-buffer backpressure."""

    record = _stream_record(playback, stream)
    data = _copy_stream_samples(samples)
    if not data:
        raise ValueError("samples cannot be empty")
    frame_width = record.channels * record.sample_type.byte_width
    if len(data) % frame_width:
        raise ValueError("samples must contain a whole number of frames")
    if record.state in (StreamState.FINISHED, StreamState.STOPPED):
        raise InvalidVoiceStateError(
            f"cannot write to a stream in the {record.state.value} state"
        )
    if record.input_finished:
        raise InvalidVoiceStateError("cannot write to a stream after end-of-input")
    if not record.free_buffers:
        return False

    _prepare_al(playback)
    buffer = record.free_buffers[0]
    playback._library.al.buffer_data(
        buffer,
        _FORMAT_BY_LAYOUT[(record.channels, record.sample_type)],
        data,
        record.sample_rate,
    )
    _check_al_error(playback, "upload stream chunk")
    playback._library.al.source_queue_buffers(record.identifier, (buffer,))
    _check_al_error(playback, "queue stream chunk")

    record.free_buffers.popleft()
    frame_count = len(data) // frame_width
    record.queued_chunks.append(
        _StreamChunk(
            buffer=buffer,
            frame_count=frame_count,
            duration=frame_count / record.sample_rate,
        )
    )
    record.underrun_active = False

    if record.state is StreamState.PLAYING:
        native_state = _get_voice_state(
            playback, record.identifier, "query stream after write"
        )
        if native_state is VoiceState.STOPPED:
            playback._library.al.source_play(record.identifier)
            _check_al_error(playback, "restart stream after write")
            processed_frames = sum(
                chunk.frame_count for chunk in tuple(record.queued_chunks)[:-1]
            )
            if processed_frames:
                playback._library.al.sourcei(
                    record.identifier, bindings.AL_SAMPLE_OFFSET, processed_frames
                )
                _check_al_error(playback, "skip processed stream chunks")
    return True


def start_stream(playback: Playback, stream: Stream) -> None:
    """Start a primed stream for the first and only time."""

    record = _stream_record(playback, stream)
    if record.state is not StreamState.INITIAL:
        raise InvalidVoiceStateError(
            f"cannot start a stream in the {record.state.value} state"
        )
    if not record.queued_chunks:
        raise InvalidVoiceStateError("cannot start a stream without a queued chunk")
    _prepare_al(playback)
    playback._library.al.source_play(record.identifier)
    _check_al_error(playback, "start stream")
    record.state = StreamState.PLAYING


def _stream_status(record: _StreamRecord, offset_seconds: float = 0.0) -> StreamStatus:
    queued_seconds = sum(chunk.duration for chunk in record.queued_chunks)
    if record.queued_chunks:
        current_duration = record.queued_chunks[0].duration
        queued_seconds -= min(max(offset_seconds, 0.0), current_duration)
    return StreamStatus(
        state=record.state,
        input_finished=record.input_finished,
        queued_chunks=len(record.queued_chunks),
        queued_seconds=max(queued_seconds, 0.0),
        underrun_count=record.underrun_count,
    )


def update_stream(playback: Playback, stream: Stream) -> StreamStatus:
    """Reclaim processed chunks, recover underruns, and return stream status."""

    record = _stream_record(playback, stream)
    if record.state in (StreamState.FINISHED, StreamState.STOPPED):
        return _stream_status(record)

    _prepare_al(playback)
    processed = int(
        playback._library.al.get_sourcei(
            record.identifier, bindings.AL_BUFFERS_PROCESSED
        )
    )
    _check_al_error(playback, "query processed stream buffers")
    if not 0 <= processed <= len(record.queued_chunks):
        raise AudioBackendError("OpenAL returned an invalid processed buffer count")
    if processed:
        returned = playback._library.al.source_unqueue_buffers(
            record.identifier, processed
        )
        _check_al_error(playback, "reclaim stream buffers")
        expected = tuple(
            chunk.buffer for chunk in tuple(record.queued_chunks)[:processed]
        )
        if returned != expected:
            raise AudioBackendError("OpenAL returned unexpected stream buffers")
        for _ in range(processed):
            chunk = record.queued_chunks.popleft()
            record.free_buffers.append(chunk.buffer)

    if not record.queued_chunks and record.input_finished:
        record.state = StreamState.FINISHED
        record.underrun_active = False
    elif record.state is StreamState.PLAYING:
        if not record.queued_chunks:
            if not record.underrun_active:
                record.underrun_count += 1
                record.underrun_active = True
        else:
            native_state = _get_voice_state(
                playback, record.identifier, "query stream playback state"
            )
            if native_state is VoiceState.STOPPED:
                playback._library.al.source_play(record.identifier)
                _check_al_error(playback, "restart stream after underrun")

    offset = 0.0
    if record.queued_chunks:
        offset = float(
            playback._library.al.get_sourcef(record.identifier, bindings.AL_SEC_OFFSET)
        )
        _check_al_error(playback, "query stream offset")
        if not math.isfinite(offset):
            raise AudioBackendError("OpenAL returned a non-finite stream offset")
    return _stream_status(record, offset)


def finish_stream(playback: Playback, stream: Stream) -> None:
    """Declare end-of-input and allow already queued chunks to drain."""

    record = _stream_record(playback, stream)
    if record.state is StreamState.STOPPED:
        raise InvalidVoiceStateError("cannot finish a stream in the stopped state")
    if record.state is StreamState.FINISHED or record.input_finished:
        return
    record.input_finished = True
    record.underrun_active = False
    if not record.queued_chunks:
        record.state = StreamState.FINISHED


def set_voice_config(
    playback: Playback, voice: Voice | Stream, config: VoiceConfig
) -> None:
    """Apply a complete immutable configuration to a live voice or stream."""

    if not isinstance(config, VoiceConfig):
        raise TypeError("config must be a VoiceConfig")
    if isinstance(voice, Stream):
        identifier = _stream_record(playback, voice).identifier
        if config.looping:
            raise ValueError("streaming voices cannot loop")
    else:
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
    state = _get_voice_state(playback, identifier, "query voice state")
    offset = playback._library.al.get_sourcef(identifier, bindings.AL_SEC_OFFSET)
    _check_al_error(playback, "query voice")
    return VoiceStatus(state=state, offset_seconds=float(offset))


def _get_voice_state(playback: Playback, identifier: int, operation: str) -> VoiceState:
    raw_state = int(
        playback._library.al.get_sourcei(identifier, bindings.AL_SOURCE_STATE)
    )
    _check_al_error(playback, operation)
    try:
        return _VOICE_STATE_BY_AL[raw_state]
    except KeyError as error:
        raise AudioBackendError(
            f"OpenAL returned unknown voice state 0x{raw_state:04x}"
        ) from error


def _control_voice(playback: Playback, voice: Voice, operation: str) -> None:
    identifier = _voice_identifier(playback, voice)
    _prepare_al(playback)
    function = getattr(playback._library.al, f"source_{operation}")
    function(identifier)
    _check_al_error(playback, f"{operation} voice")


def pause(playback: Playback, voice: Voice | Stream) -> None:
    """Pause a live voice or a logically playing stream."""

    if isinstance(voice, Stream):
        record = _stream_record(playback, voice)
        if record.state is not StreamState.PLAYING:
            return
        _prepare_al(playback)
        playback._library.al.source_pause(record.identifier)
        _check_al_error(playback, "pause stream")
        record.state = StreamState.PAUSED
        record.underrun_active = False
        return
    _control_voice(playback, voice, "pause")


def resume(playback: Playback, voice: Voice | Stream) -> None:
    """Resume a paused voice or stream."""

    if isinstance(voice, Stream):
        record = _stream_record(playback, voice)
        if record.state is not StreamState.PAUSED:
            raise InvalidVoiceStateError(
                f"cannot resume a stream in the {record.state.value} state"
            )
        _prepare_al(playback)
        playback._library.al.source_play(record.identifier)
        _check_al_error(playback, "resume stream")
        record.state = StreamState.PLAYING
        return
    identifier = _voice_identifier(playback, voice)
    _prepare_al(playback)
    state = _get_voice_state(playback, identifier, "query voice before resume")
    if state is not VoiceState.PAUSED:
        raise InvalidVoiceStateError(
            f"cannot resume a voice in the {state.value} state"
        )
    playback._library.al.source_play(identifier)
    _check_al_error(playback, "resume voice")


def stop(playback: Playback, voice: Voice | Stream) -> None:
    """Stop a live voice or discard a stream's queued audio."""

    if isinstance(voice, Stream):
        record = _stream_record(playback, voice)
        if record.state in (StreamState.FINISHED, StreamState.STOPPED):
            return
        _prepare_al(playback)
        playback._library.al.source_stop(record.identifier)
        _check_al_error(playback, "stop stream")
        queued_count = len(record.queued_chunks)
        if queued_count:
            returned = playback._library.al.source_unqueue_buffers(
                record.identifier, queued_count
            )
            _check_al_error(playback, "discard stopped stream buffers")
            expected = tuple(chunk.buffer for chunk in record.queued_chunks)
            if returned != expected:
                raise AudioBackendError("OpenAL returned unexpected stream buffers")
            for chunk in record.queued_chunks:
                record.free_buffers.append(chunk.buffer)
            record.queued_chunks.clear()
        record.state = StreamState.STOPPED
        record.underrun_active = False
        return
    _control_voice(playback, voice, "stop")


def release_finished(playback: Playback) -> int:
    """Release all terminal voices and streams and return the count.

    OpenAL reports both naturally completed and explicitly stopped voices as
    stopped. Streams are collected only after their managed state becomes
    ``FINISHED`` or ``STOPPED``; this function never updates active streams.
    """

    _prepare_al(playback)
    stopped_tokens: list[object] = []
    stopped_identifiers: list[int] = []
    for token, identifier in tuple(playback._voices.items()):
        state = _get_voice_state(playback, identifier, "query finished voice")
        if state is VoiceState.STOPPED:
            stopped_tokens.append(token)
            stopped_identifiers.append(identifier)

    stream_tokens = [
        token
        for token, record in playback._streams.items()
        if record.state in (StreamState.FINISHED, StreamState.STOPPED)
    ]
    stream_identifiers = [
        playback._streams[token].identifier for token in stream_tokens
    ]
    stream_buffers = [
        identifier
        for token in stream_tokens
        for identifier in playback._streams[token].buffers
    ]

    if not stopped_identifiers and not stream_identifiers:
        return 0

    playback._library.al.delete_sources(stopped_identifiers + stream_identifiers)
    if stream_buffers:
        playback._library.al.delete_buffers(stream_buffers)
    _check_al_error(playback, "release finished voices and streams")
    for token in stopped_tokens:
        del playback._voices[token]
        del playback._voice_clips[token]
    for token in stream_tokens:
        del playback._streams[token]
    return len(stopped_tokens) + len(stream_tokens)


@overload
def release(playback: Playback, resource: Clip) -> None: ...


@overload
def release(playback: Playback, resource: Voice) -> None: ...


@overload
def release(playback: Playback, resource: Stream) -> None: ...


def release(playback: Playback, resource: Clip | Voice | Stream) -> None:
    """Release a clip, voice, or stream before its playback session closes."""

    if isinstance(resource, Stream):
        record = _stream_record(playback, resource)
        _prepare_al(playback)
        playback._library.al.source_stop(record.identifier)
        playback._library.al.delete_sources((record.identifier,))
        playback._library.al.delete_buffers(record.buffers)
        _check_al_error(playback, "release stream")
        del playback._streams[resource._token]
        return
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
    raise TypeError("resource must be a Clip, Voice, or Stream")


@dataclass(slots=True)
class _SoundRecord:
    token: object
    voice: Voice
    final_status: VoiceStatus | None = None


@dataclass(frozen=True, slots=True, eq=False)
class PlayingSound:
    """One playback instance returned by :func:`play`.

    The default playback runtime owns the native resources, so discarding this
    object does not stop the sound. Its methods are convenient delegates to the
    function-oriented managed API.
    """

    _runtime: _DefaultRuntime
    _record: _SoundRecord

    @property
    def status(self) -> VoiceStatus:
        """Current playback state and offset."""

        return self._runtime.status(self._record)

    @property
    def state(self) -> VoiceState:
        """Current playback state."""

        return self.status.state

    @property
    def playing(self) -> bool:
        """Whether the sound is currently playing."""

        return self.state is VoiceState.PLAYING

    def pause(self) -> None:
        """Pause the sound if it is currently playing."""

        self._runtime.pause(self._record)

    def resume(self) -> None:
        """Resume the sound if it is paused."""

        self._runtime.resume(self._record)

    def stop(self) -> None:
        """Stop the sound and release its playback voice."""

        self._runtime.stop(self._record)

    def set_config(self, config: VoiceConfig) -> None:
        """Apply a complete immutable voice configuration."""

        self._runtime.set_config(self._record, config)

    def __repr__(self) -> str:
        return "PlayingSound(<opaque>)"


def _read_wave(path: Path) -> PCM:
    try:
        with wave.open(str(path), "rb") as source:
            if source.getcomptype() != "NONE":
                raise AudioFileError(f"unsupported compressed WAV file: {path}")
            channels = source.getnchannels()
            sample_rate = source.getframerate()
            sample_width = source.getsampwidth()
            samples = source.readframes(source.getnframes())
    except (EOFError, wave.Error) as error:
        raise AudioFileError(f"could not read WAV file {path}: {error}") from error

    try:
        sample_type = {
            1: SampleType.UINT8,
            2: SampleType.INT16,
        }[sample_width]
    except KeyError as error:
        raise AudioFileError(
            f"unsupported {sample_width * 8}-bit WAV file: {path}"
        ) from error

    try:
        return PCM(
            samples=samples,
            channels=channels,
            sample_rate=sample_rate,
            sample_type=sample_type,
        )
    except (TypeError, ValueError) as error:
        raise AudioFileError(f"unsupported WAV file {path}: {error}") from error


class _DefaultRuntime:
    """Own the implicit session, cached clips, and active playback voices."""

    __slots__ = ("_active", "_clips", "_closed", "_lock", "_playback")

    def __init__(self) -> None:
        self._playback: Playback | None = None
        self._clips: dict[Path, Clip] = {}
        self._active: dict[object, _SoundRecord] = {}
        self._closed = False
        self._lock = RLock()

    def _require_open(self) -> None:
        if self._closed:
            raise InvalidVoiceStateError("the sound's playback runtime is closed")

    def _ensure_playback(self) -> Playback:
        self._require_open()
        if self._playback is None:
            self._playback = open_playback()
        return self._playback

    def _opened_playback(self) -> Playback:
        self._require_open()
        if self._playback is None:
            raise InvalidVoiceStateError("the sound was not started")
        return self._playback

    def _finalize(self, record: _SoundRecord, status: VoiceStatus) -> None:
        release(self._opened_playback(), record.voice)
        record.final_status = status
        del self._active[record.token]

    def _status(self, record: _SoundRecord) -> VoiceStatus:
        if record.final_status is not None:
            return record.final_status
        self._require_open()
        status = get_voice_status(self._opened_playback(), record.voice)
        if status.state is VoiceState.STOPPED:
            self._finalize(record, status)
        return status

    def _reap_finished(self) -> None:
        for record in tuple(self._active.values()):
            self._status(record)

    def play(self, path: AudioPath, config: VoiceConfig) -> PlayingSound:
        if not isinstance(config, VoiceConfig):
            raise TypeError("config must be a VoiceConfig")
        normalized = Path(path).expanduser().resolve()
        with self._lock:
            self._require_open()
            self._reap_finished()
            clip = self._clips.get(normalized)
            if clip is None:
                pcm = _read_wave(normalized)
                clip = upload(self._ensure_playback(), pcm)
                self._clips[normalized] = clip
            voice = _play_voice(self._opened_playback(), clip, config)
            token = object()
            record = _SoundRecord(token, voice)
            self._active[token] = record
            return PlayingSound(self, record)

    def status(self, record: _SoundRecord) -> VoiceStatus:
        with self._lock:
            return self._status(record)

    def pause(self, record: _SoundRecord) -> None:
        with self._lock:
            status = self._status(record)
            if status.state is VoiceState.PLAYING:
                pause(self._opened_playback(), record.voice)

    def resume(self, record: _SoundRecord) -> None:
        with self._lock:
            status = self._status(record)
            if status.state is not VoiceState.PAUSED:
                raise InvalidVoiceStateError(
                    f"cannot resume a sound in the {status.state.value} state"
                )
            resume(self._opened_playback(), record.voice)

    def stop(self, record: _SoundRecord) -> None:
        with self._lock:
            if record.final_status is not None:
                return
            status = self._status(record)
            if status.state is VoiceState.STOPPED:
                return
            stop(self._opened_playback(), record.voice)
            self._finalize(
                record,
                VoiceStatus(
                    state=VoiceState.STOPPED,
                    offset_seconds=status.offset_seconds,
                ),
            )

    def set_config(self, record: _SoundRecord, config: VoiceConfig) -> None:
        if not isinstance(config, VoiceConfig):
            raise TypeError("config must be a VoiceConfig")
        with self._lock:
            status = self._status(record)
            if status.state is VoiceState.STOPPED:
                raise InvalidVoiceStateError("cannot configure a stopped sound")
            set_voice_config(self._opened_playback(), record.voice, config)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            statuses: dict[object, VoiceStatus] = {}
            for token, record in self._active.items():
                try:
                    status = get_voice_status(self._opened_playback(), record.voice)
                    statuses[token] = VoiceStatus(
                        state=VoiceState.STOPPED,
                        offset_seconds=status.offset_seconds,
                    )
                except Exception:
                    statuses[token] = VoiceStatus(
                        state=VoiceState.STOPPED,
                        offset_seconds=0.0,
                    )
            try:
                if self._playback is not None:
                    close_playback(self._playback)
            finally:
                for token, record in self._active.items():
                    record.final_status = statuses[token]
                self._active.clear()
                self._clips.clear()
                self._playback = None
                self._closed = True


_default_runtime: _DefaultRuntime | None = None
_default_lock = RLock()


def _get_default_runtime() -> _DefaultRuntime:
    global _default_runtime
    with _default_lock:
        if _default_runtime is None:
            _default_runtime = _DefaultRuntime()
        return _default_runtime


@overload
def play(
    playback: Playback,
    clip: Clip,
    config: VoiceConfig = _DEFAULT_VOICE_CONFIG,
) -> Voice: ...


@overload
def play(
    playback: AudioPath,
    /,
    *,
    config: VoiceConfig = _DEFAULT_VOICE_CONFIG,
) -> PlayingSound: ...


def play(
    playback: Playback | AudioPath,
    clip: Clip | None = None,
    config: VoiceConfig = _DEFAULT_VOICE_CONFIG,
) -> Voice | PlayingSound:
    """Play an explicit clip or a WAV file through the default runtime.

    ``play(playback, clip, config)`` preserves the explicit managed API.
    ``play(path, config=config)`` starts asynchronous, fire-and-forget playback
    and returns an optional control handle.
    """

    if isinstance(playback, Playback):
        if clip is None:
            raise TypeError("clip must be provided with an explicit Playback")
        return _play_voice(playback, clip, config)
    if clip is not None:
        raise TypeError("clip is only valid with an explicit Playback")
    if not isinstance(playback, (str, PathLike)):
        raise TypeError("sound must be a path to a WAV file")
    return _get_default_runtime().play(playback, config)


def shutdown() -> None:
    """Close and forget the default playback runtime, if it was opened."""

    global _default_runtime
    with _default_lock:
        runtime, _default_runtime = _default_runtime, None
        if runtime is not None:
            runtime.close()


def _shutdown_at_exit() -> None:
    with suppress(Exception):
        shutdown()


atexit.register(_shutdown_at_exit)


__all__ = [
    "AudioBackendError",
    "AudioError",
    "AudioFileError",
    "Clip",
    "HRTFStatus",
    "InvalidHandleError",
    "InvalidVoiceStateError",
    "Listener",
    "PCM",
    "Playback",
    "PlaybackConfig",
    "PlaybackClosedError",
    "PlaybackDevice",
    "PlaybackInfo",
    "PlaybackOpenError",
    "PlayingSound",
    "ResourceInUseError",
    "SampleType",
    "Stream",
    "StreamState",
    "StreamStatus",
    "Vector3",
    "Voice",
    "VoiceConfig",
    "VoiceState",
    "VoiceStatus",
    "close_playback",
    "finish_stream",
    "get_playback_info",
    "get_voice_status",
    "list_playback_devices",
    "open_playback",
    "open_stream",
    "pause",
    "play",
    "release",
    "release_finished",
    "resume",
    "set_listener",
    "set_voice_config",
    "shutdown",
    "start_stream",
    "stop",
    "try_write_stream",
    "update_stream",
    "upload",
]
