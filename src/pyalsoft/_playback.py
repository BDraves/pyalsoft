"""Function-oriented managed playback API built on the low-level bindings."""

from __future__ import annotations

import atexit
import math
import wave
from collections import OrderedDict, deque
from collections.abc import Buffer, Callable, Iterator
from contextlib import contextmanager, nullcontext, suppress
from dataclasses import dataclass, field, replace
from enum import Enum
from functools import wraps
from os import PathLike
from pathlib import Path
from threading import RLock
from types import TracebackType
from typing import Concatenate, Self, cast, overload

from pyalsoft import bindings
from pyalsoft.bindings._library import _pointer_address

type Vector3 = tuple[float, float, float]
type AudioPath = str | PathLike[str]

_FLOAT32_MAX = float.fromhex("0x1.fffffep+127")
_DEFAULT_SOUND_CACHE_LIMIT = 64 * 1024 * 1024


class _UnsetType:
    __slots__ = ()

    def __repr__(self) -> str:
        return "<omitted>"


_UNSET = _UnsetType()


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


class SoundEndReason(Enum):
    """Why a convenience playback handle entered its terminal state."""

    FINISHED = "finished"
    STOPPED = "stopped"
    SHUTDOWN = "shutdown"
    DEVICE_LOST = "device_lost"


class DistanceModel(Enum):
    """Distance-attenuation model used by a playback context."""

    NONE = "none"
    INVERSE = "inverse"
    INVERSE_CLAMPED = "inverse_clamped"
    LINEAR = "linear"
    LINEAR_CLAMPED = "linear_clamped"
    EXPONENT = "exponent"
    EXPONENT_CLAMPED = "exponent_clamped"


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


def _bounded_float(name: str, value: float, minimum: float, maximum: float) -> float:
    converted = _finite_float(name, value)
    if not minimum <= converted <= maximum:
        raise ValueError(f"{name} must be between {minimum:g} and {maximum:g}")
    return converted


def _sound_offset(value: float, duration_seconds: float) -> float:
    offset_seconds = _finite_float("offset_seconds", value)
    if not 0.0 <= offset_seconds < duration_seconds:
        raise ValueError(
            "offset_seconds must be at least 0.0 and less than the "
            f"sound duration ({duration_seconds:g} seconds)"
        )
    return offset_seconds


def _frame_offset(value: int, frame_count: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("offset_frames must be an integer")
    if not 0 <= value < frame_count:
        raise ValueError(
            "offset_frames must be at least 0 and less than the "
            f"sound frame count ({frame_count})"
        )
    return value


def _validate_offsets(
    info: SoundInfo,
    offset_seconds: float,
    offset_frames: int | None,
) -> tuple[float, int | None]:
    if offset_frames is None:
        return _sound_offset(offset_seconds, info.duration_seconds), None
    if offset_seconds != 0.0:
        raise ValueError("offset_seconds and offset_frames cannot both be set")
    return 0.0, _frame_offset(offset_frames, info.frame_count)


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
class SoundInfo:
    """Format and length information for immutable PCM audio."""

    channels: int
    sample_rate: int
    sample_type: SampleType
    frame_count: int

    def __post_init__(self) -> None:
        if isinstance(self.channels, bool) or not isinstance(self.channels, int):
            raise TypeError("channels must be an integer")
        if self.channels not in (1, 2):
            raise ValueError("channels must be 1 or 2")
        if isinstance(self.sample_rate, bool) or not isinstance(self.sample_rate, int):
            raise TypeError("sample_rate must be an integer")
        if self.sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        if not isinstance(self.sample_type, SampleType):
            raise TypeError("sample_type must be a SampleType")
        if isinstance(self.frame_count, bool) or not isinstance(self.frame_count, int):
            raise TypeError("frame_count must be an integer")
        if self.frame_count <= 0:
            raise ValueError("frame_count must be positive")

    @property
    def duration_seconds(self) -> float:
        """Duration in source-audio seconds."""

        return self.frame_count / self.sample_rate

    @property
    def sample_width_bytes(self) -> int:
        """Number of bytes used by one channel sample."""

        return self.sample_type.byte_width

    @property
    def bit_depth(self) -> int:
        """Number of bits used by one channel sample."""

        return self.sample_width_bytes * 8

    @property
    def frame_width_bytes(self) -> int:
        """Number of bytes used by one interleaved sample frame."""

        return self.channels * self.sample_width_bytes

    @property
    def byte_count(self) -> int:
        """Total number of PCM data bytes."""

        return self.frame_count * self.frame_width_bytes


@dataclass(frozen=True, slots=True)
class SoundCacheInfo:
    """Observed state of the implicit file-clip cache."""

    max_bytes: int | None
    current_bytes: int
    clip_count: int
    active_clip_count: int
    pending_eviction_count: int


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
        if isinstance(self.channels, bool) or not isinstance(self.channels, int):
            raise TypeError("channels must be an integer")
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

    @property
    def info(self) -> SoundInfo:
        """Format and length information for this PCM value."""

        return SoundInfo(
            channels=self.channels,
            sample_rate=self.sample_rate,
            sample_type=self.sample_type,
            frame_count=self.frame_count,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class Reverb:
    """Immutable standard EFX reverb parameters."""

    density: float = 1.0
    diffusion: float = 1.0
    gain: float = 0.32
    high_frequency_gain: float = 0.89
    decay_time: float = 1.49
    high_frequency_decay_ratio: float = 0.83
    reflections_gain: float = 0.05
    reflections_delay: float = 0.007
    late_reverb_gain: float = 1.26
    late_reverb_delay: float = 0.011
    air_absorption_high_frequency_gain: float = 0.994
    room_rolloff_factor: float = 0.0
    high_frequency_decay_limit: bool = True

    def __post_init__(self) -> None:
        ranges = (
            ("density", self.density, 0.0, 1.0),
            ("diffusion", self.diffusion, 0.0, 1.0),
            ("gain", self.gain, 0.0, 1.0),
            ("high_frequency_gain", self.high_frequency_gain, 0.0, 1.0),
            ("decay_time", self.decay_time, 0.1, 20.0),
            (
                "high_frequency_decay_ratio",
                self.high_frequency_decay_ratio,
                0.1,
                2.0,
            ),
            ("reflections_gain", self.reflections_gain, 0.0, 3.16),
            ("reflections_delay", self.reflections_delay, 0.0, 0.3),
            ("late_reverb_gain", self.late_reverb_gain, 0.0, 10.0),
            ("late_reverb_delay", self.late_reverb_delay, 0.0, 0.1),
            (
                "air_absorption_high_frequency_gain",
                self.air_absorption_high_frequency_gain,
                0.892,
                1.0,
            ),
            ("room_rolloff_factor", self.room_rolloff_factor, 0.0, 10.0),
        )
        for name, value, minimum, maximum in ranges:
            object.__setattr__(
                self,
                name,
                _bounded_float(name, value, minimum, maximum),
            )
        if not isinstance(self.high_frequency_decay_limit, bool):
            raise TypeError("high_frequency_decay_limit must be a boolean")


@dataclass(frozen=True, slots=True, kw_only=True)
class LowPassFilter:
    """An EFX filter that attenuates the high-frequency signal."""

    gain: float = 1.0
    high_frequency_gain: float = 1.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "gain", _bounded_float("gain", self.gain, 0.0, 1.0))
        object.__setattr__(
            self,
            "high_frequency_gain",
            _bounded_float("high_frequency_gain", self.high_frequency_gain, 0.0, 1.0),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class HighPassFilter:
    """An EFX filter that attenuates the low-frequency signal."""

    gain: float = 1.0
    low_frequency_gain: float = 1.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "gain", _bounded_float("gain", self.gain, 0.0, 1.0))
        object.__setattr__(
            self,
            "low_frequency_gain",
            _bounded_float("low_frequency_gain", self.low_frequency_gain, 0.0, 1.0),
        )


type Filter = LowPassFilter | HighPassFilter
_OMITTED_FILTER = cast(Filter | None, _UNSET)


@dataclass(frozen=True, slots=True, kw_only=True)
class EffectSend:
    """One auxiliary effect route, with an optional filter on its wet signal."""

    effect: Reverb
    filter: Filter | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.effect, Reverb):
            raise TypeError("effect must be a Reverb")
        if self.filter is not None and not isinstance(
            self.filter, (LowPassFilter, HighPassFilter)
        ):
            raise TypeError("filter must be a LowPassFilter, HighPassFilter, or None")


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
    min_gain: float = 0.0
    max_gain: float = 1.0
    reference_distance: float = 1.0
    max_distance: float = _FLOAT32_MAX
    rolloff_factor: float = 1.0
    cone_inner_angle: float = 360.0
    cone_outer_angle: float = 360.0
    cone_outer_gain: float = 0.0
    filter: Filter | None = None
    effect_sends: tuple[EffectSend, ...] = ()

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
        min_gain = _finite_float("min_gain", self.min_gain)
        if not 0.0 <= min_gain <= 1.0:
            raise ValueError("min_gain must be between 0.0 and 1.0")
        max_gain = _finite_float("max_gain", self.max_gain)
        if not 0.0 <= max_gain <= 1.0:
            raise ValueError("max_gain must be between 0.0 and 1.0")
        if min_gain > max_gain:
            raise ValueError("min_gain cannot exceed max_gain")
        reference_distance = _finite_float(
            "reference_distance", self.reference_distance
        )
        if reference_distance < 0.0:
            raise ValueError("reference_distance cannot be negative")
        max_distance = _finite_float("max_distance", self.max_distance)
        if max_distance < 0.0:
            raise ValueError("max_distance cannot be negative")
        rolloff_factor = _finite_float("rolloff_factor", self.rolloff_factor)
        if rolloff_factor < 0.0:
            raise ValueError("rolloff_factor cannot be negative")
        cone_inner_angle = _finite_float("cone_inner_angle", self.cone_inner_angle)
        if not 0.0 <= cone_inner_angle <= 360.0:
            raise ValueError("cone_inner_angle must be between 0.0 and 360.0")
        cone_outer_angle = _finite_float("cone_outer_angle", self.cone_outer_angle)
        if not 0.0 <= cone_outer_angle <= 360.0:
            raise ValueError("cone_outer_angle must be between 0.0 and 360.0")
        cone_outer_gain = _finite_float("cone_outer_gain", self.cone_outer_gain)
        if not 0.0 <= cone_outer_gain <= 1.0:
            raise ValueError("cone_outer_gain must be between 0.0 and 1.0")
        if self.filter is not None and not isinstance(
            self.filter, (LowPassFilter, HighPassFilter)
        ):
            raise TypeError("filter must be a LowPassFilter, HighPassFilter, or None")
        if not isinstance(self.effect_sends, (tuple, list)):
            raise TypeError("effect_sends must be a tuple or list")
        effect_sends = tuple(self.effect_sends)
        if not all(isinstance(send, EffectSend) for send in effect_sends):
            raise TypeError("effect_sends must contain only EffectSend values")
        object.__setattr__(self, "gain", gain)
        object.__setattr__(self, "pitch", pitch)
        object.__setattr__(self, "min_gain", min_gain)
        object.__setattr__(self, "max_gain", max_gain)
        object.__setattr__(self, "reference_distance", reference_distance)
        object.__setattr__(self, "max_distance", max_distance)
        object.__setattr__(self, "rolloff_factor", rolloff_factor)
        object.__setattr__(self, "cone_inner_angle", cone_inner_angle)
        object.__setattr__(self, "cone_outer_angle", cone_outer_angle)
        object.__setattr__(self, "cone_outer_gain", cone_outer_gain)
        object.__setattr__(self, "effect_sends", effect_sends)


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


@dataclass(frozen=True, slots=True, kw_only=True)
class Acoustics:
    """Global distance and Doppler settings for one playback context."""

    distance_model: DistanceModel = DistanceModel.INVERSE_CLAMPED
    doppler_factor: float = 1.0
    speed_of_sound: float = 343.3

    def __post_init__(self) -> None:
        if not isinstance(self.distance_model, DistanceModel):
            raise TypeError("distance_model must be a DistanceModel")
        doppler_factor = _finite_float("doppler_factor", self.doppler_factor)
        if doppler_factor < 0.0:
            raise ValueError("doppler_factor cannot be negative")
        speed_of_sound = _finite_float("speed_of_sound", self.speed_of_sound)
        if speed_of_sound < 0.0001:
            raise ValueError("speed_of_sound must be at least 0.0001")
        object.__setattr__(self, "doppler_factor", doppler_factor)
        object.__setattr__(self, "speed_of_sound", speed_of_sound)


@dataclass(frozen=True, slots=True)
class VoiceStatus:
    """Runtime state observed from a voice."""

    state: VoiceState
    offset_seconds: float
    offset_frames: int = 0


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
    _info: SoundInfo = field(repr=False)

    @property
    def info(self) -> SoundInfo:
        """Format and length information for this clip."""

        return self._info

    @property
    def duration_seconds(self) -> float:
        """Duration of this clip in source-audio seconds."""

        return self.info.duration_seconds

    @property
    def frame_count(self) -> int:
        """Number of sample frames in this clip."""

        return self.info.frame_count

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
class _EfxResources:
    direct_filter: int | None = None
    effects: tuple[int, ...] = ()
    slots: tuple[int, ...] = ()
    send_filters: tuple[int | None, ...] = ()

    @property
    def filters(self) -> tuple[int, ...]:
        identifiers = () if self.direct_filter is None else (self.direct_filter,)
        return identifiers + tuple(
            identifier for identifier in self.send_filters if identifier is not None
        )


_EMPTY_EFX_RESOURCES = _EfxResources()


@dataclass(frozen=True, slots=True)
class _EfxReplacement:
    current: _EfxResources
    created: _EfxResources
    retired: _EfxResources
    direct_filter_changed: bool
    effect_sends_changed: bool


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
    config: VoiceConfig
    efx: _EfxResources = _EMPTY_EFX_RESOURCES
    state: StreamState = StreamState.INITIAL
    input_finished: bool = False
    underrun_count: int = 0
    underrun_active: bool = False


class Playback:
    """Opaque owner for a playback device, context, clips, voices, and streams.

    Instances are returned by :func:`open_playback`. Use them as context
    managers or pass them to :func:`close_playback` for deterministic cleanup.
    Operations are serialized per session and across sessions sharing a native
    library, so a session may safely be used from multiple Python threads.
    """

    __slots__ = (
        "_clips",
        "_clip_infos",
        "_closed",
        "_context",
        "_device",
        "_library",
        "_lock",
        "_previous_context",
        "_previous_playback",
        "_streams",
        "_token",
        "_voice_clips",
        "_voice_configs",
        "_voice_efx",
        "_voices",
    )

    def __init__(
        self,
        library: bindings.OpenALLibrary,
        device: object,
        context: object,
        previous_context: object | None,
        previous_playback: Playback | None,
    ) -> None:
        self._library = library
        self._lock = RLock()
        self._device = device
        self._context = context
        self._previous_context = previous_context
        self._previous_playback = previous_playback
        self._token = object()
        self._clips: dict[object, int] = {}
        self._clip_infos: dict[object, SoundInfo] = {}
        self._voices: dict[object, int] = {}
        self._voice_clips: dict[object, object] = {}
        self._voice_configs: dict[object, VoiceConfig] = {}
        self._voice_efx: dict[object, _EfxResources] = {}
        self._streams: dict[object, _StreamRecord] = {}
        self._closed = False

    def __enter__(self) -> Self:
        with self._library._context_lock, self._lock:
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


_active_playbacks: set[Playback] = set()
_active_playbacks_lock = RLock()


def _serialized_playback[**P, R](
    function: Callable[Concatenate[Playback, P], R],
) -> Callable[Concatenate[Playback, P], R]:
    """Run one complete playback operation under stable context ownership."""

    @wraps(function)
    def serialized(playback: Playback, /, *args: P.args, **kwargs: P.kwargs) -> R:
        with _playback_operation(playback):
            return function(playback, *args, **kwargs)

    return serialized


@contextmanager
def _playback_operation(playback: Playback) -> Iterator[None]:
    if not isinstance(playback, Playback):
        raise TypeError("playback must be a Playback")
    with playback._library._context_lock, playback._lock:
        _require_playback(playback)
        yield


def _same_context(left: object | None, right: object | None) -> bool:
    """Compare native context handles while supporting test doubles."""

    if left is right:
        return True
    try:
        return _pointer_address(left) == _pointer_address(right)
    except TypeError:
        return False


def _playback_for_context(
    library: bindings.OpenALLibrary,
    context: object | None,
) -> Playback | None:
    if context is None:
        return None
    return next(
        (
            playback
            for playback in _active_playbacks
            if playback._library is library
            and not playback._closed
            and _same_context(playback._context, context)
        ),
        None,
    )


def _live_previous_context(playback: Playback) -> object | None:
    """Follow closed managed predecessors to the nearest live context."""

    predecessor = playback._previous_playback
    while predecessor is not None:
        if not predecessor._closed:
            return predecessor._context
        if predecessor._previous_playback is None:
            return predecessor._previous_context
        predecessor = predecessor._previous_playback
    return playback._previous_context


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
_DEFAULT_LISTENER = Listener()
_DEFAULT_ACOUSTICS = Acoustics()

_DISTANCE_MODEL_TO_AL = {
    DistanceModel.NONE: bindings.AL_NONE,
    DistanceModel.INVERSE: bindings.AL_INVERSE_DISTANCE,
    DistanceModel.INVERSE_CLAMPED: bindings.AL_INVERSE_DISTANCE_CLAMPED,
    DistanceModel.LINEAR: bindings.AL_LINEAR_DISTANCE,
    DistanceModel.LINEAR_CLAMPED: bindings.AL_LINEAR_DISTANCE_CLAMPED,
    DistanceModel.EXPONENT: bindings.AL_EXPONENT_DISTANCE,
    DistanceModel.EXPONENT_CLAMPED: bindings.AL_EXPONENT_DISTANCE_CLAMPED,
}
_DISTANCE_MODEL_BY_AL = {value: key for key, value in _DISTANCE_MODEL_TO_AL.items()}

_HRTF_STATUS_BY_ALC = {
    bindings.ALC_HRTF_DISABLED_SOFT: HRTFStatus.DISABLED,
    bindings.ALC_HRTF_ENABLED_SOFT: HRTFStatus.ENABLED,
    bindings.ALC_HRTF_DENIED_SOFT: HRTFStatus.DENIED,
    bindings.ALC_HRTF_REQUIRED_SOFT: HRTFStatus.REQUIRED,
    bindings.ALC_HRTF_HEADPHONES_DETECTED_SOFT: HRTFStatus.HEADPHONES_DETECTED,
    bindings.ALC_HRTF_UNSUPPORTED_FORMAT_SOFT: HRTFStatus.UNSUPPORTED_FORMAT,
}


def _voice_config_with_overrides(
    config: VoiceConfig | None,
    *,
    position: Vector3 | None = None,
    velocity: Vector3 | None = None,
    direction: Vector3 | None = None,
    gain: float | None = None,
    pitch: float | None = None,
    looping: bool | None = None,
    relative: bool | None = None,
    min_gain: float | None = None,
    max_gain: float | None = None,
    reference_distance: float | None = None,
    max_distance: float | None = None,
    rolloff_factor: float | None = None,
    cone_inner_angle: float | None = None,
    cone_outer_angle: float | None = None,
    cone_outer_gain: float | None = None,
    filter: Filter | None = _OMITTED_FILTER,
    effect_sends: tuple[EffectSend, ...] | list[EffectSend] | None = None,
) -> VoiceConfig:
    if config is None:
        config = _DEFAULT_VOICE_CONFIG
    elif not isinstance(config, VoiceConfig):
        raise TypeError("config must be a VoiceConfig or None")
    return VoiceConfig(
        position=config.position if position is None else position,
        velocity=config.velocity if velocity is None else velocity,
        direction=config.direction if direction is None else direction,
        gain=config.gain if gain is None else gain,
        pitch=config.pitch if pitch is None else pitch,
        looping=config.looping if looping is None else looping,
        relative=config.relative if relative is None else relative,
        min_gain=config.min_gain if min_gain is None else min_gain,
        max_gain=config.max_gain if max_gain is None else max_gain,
        reference_distance=(
            config.reference_distance
            if reference_distance is None
            else reference_distance
        ),
        max_distance=(config.max_distance if max_distance is None else max_distance),
        rolloff_factor=(
            config.rolloff_factor if rolloff_factor is None else rolloff_factor
        ),
        cone_inner_angle=(
            config.cone_inner_angle if cone_inner_angle is None else cone_inner_angle
        ),
        cone_outer_angle=(
            config.cone_outer_angle if cone_outer_angle is None else cone_outer_angle
        ),
        cone_outer_gain=(
            config.cone_outer_gain if cone_outer_gain is None else cone_outer_gain
        ),
        filter=config.filter if isinstance(filter, _UnsetType) else filter,
        effect_sends=(
            config.effect_sends if effect_sends is None else tuple(effect_sends)
        ),
    )


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


def _voice_clip_info(playback: Playback, voice: Voice) -> SoundInfo:
    """Return metadata for the clip attached to a validated static voice."""

    _voice_identifier(playback, voice)
    clip_token = playback._voice_clips[voice._token]
    return playback._clip_infos[clip_token]


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
    if isinstance(channels, bool) or not isinstance(channels, int):
        raise TypeError("channels must be an integer")
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


def _require_efx_support(playback: Playback, send_count: int) -> None:
    """Ensure the active device supports the requested EFX routing."""

    library = playback._library
    _clear_alc_errors(library, playback._device)
    supported = library.alc.is_extension_present(playback._device, "ALC_EXT_EFX")
    _check_alc_error(library, playback._device, "query EFX support")
    if not supported:
        raise AudioBackendError("the playback device does not support EFX")
    if not send_count:
        return
    maximum = library.alc.get_integerv(
        playback._device,
        bindings.ALC_MAX_AUXILIARY_SENDS,
        1,
    )[0]
    _check_alc_error(library, playback._device, "query auxiliary send limit")
    if send_count > maximum:
        raise AudioBackendError(
            f"the playback device supports at most {maximum} auxiliary effect sends"
        )


def _configure_filter(playback: Playback, identifier: int, config: Filter) -> None:
    al = playback._library.al
    if isinstance(config, LowPassFilter):
        al.filteri(identifier, bindings.AL_FILTER_TYPE, bindings.AL_FILTER_LOWPASS)
        al.filterf(identifier, bindings.AL_LOWPASS_GAIN, config.gain)
        al.filterf(
            identifier,
            bindings.AL_LOWPASS_GAINHF,
            config.high_frequency_gain,
        )
    else:
        al.filteri(identifier, bindings.AL_FILTER_TYPE, bindings.AL_FILTER_HIGHPASS)
        al.filterf(identifier, bindings.AL_HIGHPASS_GAIN, config.gain)
        al.filterf(
            identifier,
            bindings.AL_HIGHPASS_GAINLF,
            config.low_frequency_gain,
        )


def _configure_reverb(playback: Playback, identifier: int, config: Reverb) -> None:
    al = playback._library.al
    al.effecti(identifier, bindings.AL_EFFECT_TYPE, bindings.AL_EFFECT_REVERB)
    float_properties = (
        (bindings.AL_REVERB_DENSITY, config.density),
        (bindings.AL_REVERB_DIFFUSION, config.diffusion),
        (bindings.AL_REVERB_GAIN, config.gain),
        (bindings.AL_REVERB_GAINHF, config.high_frequency_gain),
        (bindings.AL_REVERB_DECAY_TIME, config.decay_time),
        (
            bindings.AL_REVERB_DECAY_HFRATIO,
            config.high_frequency_decay_ratio,
        ),
        (bindings.AL_REVERB_REFLECTIONS_GAIN, config.reflections_gain),
        (bindings.AL_REVERB_REFLECTIONS_DELAY, config.reflections_delay),
        (bindings.AL_REVERB_LATE_REVERB_GAIN, config.late_reverb_gain),
        (bindings.AL_REVERB_LATE_REVERB_DELAY, config.late_reverb_delay),
        (
            bindings.AL_REVERB_AIR_ABSORPTION_GAINHF,
            config.air_absorption_high_frequency_gain,
        ),
        (bindings.AL_REVERB_ROOM_ROLLOFF_FACTOR, config.room_rolloff_factor),
    )
    for parameter, value in float_properties:
        al.effectf(identifier, parameter, value)
    al.effecti(
        identifier,
        bindings.AL_REVERB_DECAY_HFLIMIT,
        int(config.high_frequency_decay_limit),
    )


def _delete_efx_resources(
    playback: Playback,
    resources: _EfxResources,
    *,
    operation: str,
) -> None:
    if resources == _EMPTY_EFX_RESOURCES:
        return
    al = playback._library.al
    if resources.slots:
        al.delete_auxiliary_effect_slots(resources.slots)
    if resources.effects:
        al.delete_effects(resources.effects)
    if resources.filters:
        al.delete_filters(resources.filters)
    _check_al_error(playback, operation)


def _create_efx_resources(
    playback: Playback,
    direct_filter_config: Filter | None,
    effect_sends: tuple[EffectSend, ...],
) -> _EfxResources:
    if direct_filter_config is None and not effect_sends:
        return _EMPTY_EFX_RESOURCES
    _require_efx_support(playback, len(effect_sends))
    al = playback._library.al
    direct_filter: int | None = None
    effects: list[int] = []
    slots: list[int] = []
    send_filters: list[int | None] = []
    try:
        if direct_filter_config is not None:
            identifiers = al.gen_filters()
            if len(identifiers) != 1:
                raise AudioBackendError("OpenAL did not create exactly one filter")
            direct_filter = identifiers[0]
            _check_al_error(playback, "create direct filter")
            _configure_filter(playback, direct_filter, direct_filter_config)
            _check_al_error(playback, "configure direct filter")

        for send in effect_sends:
            effect_ids = al.gen_effects()
            if len(effect_ids) != 1:
                raise AudioBackendError("OpenAL did not create exactly one effect")
            effect = effect_ids[0]
            effects.append(effect)
            _check_al_error(playback, "create effect")
            _configure_reverb(playback, effect, send.effect)
            _check_al_error(playback, "configure reverb")

            slot_ids = al.gen_auxiliary_effect_slots()
            if len(slot_ids) != 1:
                raise AudioBackendError(
                    "OpenAL did not create exactly one auxiliary effect slot"
                )
            slot = slot_ids[0]
            slots.append(slot)
            _check_al_error(playback, "create auxiliary effect slot")
            al.auxiliary_effect_sloti(slot, bindings.AL_EFFECTSLOT_EFFECT, effect)
            _check_al_error(playback, "attach effect to auxiliary slot")

            if send.filter is None:
                send_filters.append(None)
            else:
                filter_ids = al.gen_filters()
                if len(filter_ids) != 1:
                    raise AudioBackendError(
                        "OpenAL did not create exactly one send filter"
                    )
                send_filter = filter_ids[0]
                send_filters.append(send_filter)
                _check_al_error(playback, "create send filter")
                _configure_filter(playback, send_filter, send.filter)
                _check_al_error(playback, "configure send filter")
    except BaseException:
        _clear_al_errors(playback)
        resources = _EfxResources(
            direct_filter=direct_filter,
            effects=tuple(effects),
            slots=tuple(slots),
            send_filters=tuple(send_filters),
        )
        with suppress(Exception):
            _delete_efx_resources(
                playback,
                resources,
                operation="clean up incomplete EFX resources",
            )
        playback._library.al.get_error()
        raise
    return _EfxResources(
        direct_filter=direct_filter,
        effects=tuple(effects),
        slots=tuple(slots),
        send_filters=tuple(send_filters),
    )


def _attach_efx_resources(
    playback: Playback,
    source: int,
    resources: _EfxResources,
    *,
    clear_send_count: int,
    attach_direct_filter: bool = True,
    attach_effect_sends: bool = True,
) -> None:
    al = playback._library.al
    if attach_direct_filter:
        al.sourcei(
            source,
            bindings.AL_DIRECT_FILTER,
            resources.direct_filter or bindings.AL_FILTER_NULL,
        )
    if attach_effect_sends:
        for index, slot in enumerate(resources.slots):
            send_filter = resources.send_filters[index]
            al.source3i(
                source,
                bindings.AL_AUXILIARY_SEND_FILTER,
                slot,
                index,
                send_filter or bindings.AL_FILTER_NULL,
            )
        for index in range(len(resources.slots), clear_send_count):
            al.source3i(
                source,
                bindings.AL_AUXILIARY_SEND_FILTER,
                bindings.AL_EFFECTSLOT_NULL,
                index,
                bindings.AL_FILTER_NULL,
            )
    _check_al_error(playback, "configure voice EFX routing")


def _install_efx_resources(
    playback: Playback,
    source: int,
    config: VoiceConfig,
) -> _EfxResources:
    if config.filter is None and not config.effect_sends:
        return _EMPTY_EFX_RESOURCES
    current = _create_efx_resources(
        playback,
        config.filter,
        config.effect_sends,
    )
    try:
        _attach_efx_resources(
            playback,
            source,
            current,
            clear_send_count=0,
        )
    except BaseException:
        with suppress(Exception):
            _clear_al_errors(playback)
        with suppress(Exception):
            _attach_efx_resources(
                playback,
                source,
                _EMPTY_EFX_RESOURCES,
                clear_send_count=len(current.slots),
            )
        with suppress(Exception):
            _clear_al_errors(playback)
        with suppress(Exception):
            _delete_efx_resources(
                playback,
                current,
                operation="clean up replacement EFX resources",
            )
        playback._library.al.get_error()
        raise
    return current


def _prepare_efx_replacement(
    playback: Playback,
    previous_config: VoiceConfig,
    previous: _EfxResources,
    current_config: VoiceConfig,
) -> _EfxReplacement:
    direct_filter_changed = current_config.filter != previous_config.filter
    effect_sends_changed = current_config.effect_sends != previous_config.effect_sends
    if not direct_filter_changed and not effect_sends_changed:
        return _EfxReplacement(
            current=previous,
            created=_EMPTY_EFX_RESOURCES,
            retired=_EMPTY_EFX_RESOURCES,
            direct_filter_changed=False,
            effect_sends_changed=False,
        )

    created = _create_efx_resources(
        playback,
        current_config.filter if direct_filter_changed else None,
        current_config.effect_sends if effect_sends_changed else (),
    )
    current = _EfxResources(
        direct_filter=(
            created.direct_filter if direct_filter_changed else previous.direct_filter
        ),
        effects=created.effects if effect_sends_changed else previous.effects,
        slots=created.slots if effect_sends_changed else previous.slots,
        send_filters=(
            created.send_filters if effect_sends_changed else previous.send_filters
        ),
    )
    retired = _EfxResources(
        direct_filter=previous.direct_filter if direct_filter_changed else None,
        effects=previous.effects if effect_sends_changed else (),
        slots=previous.slots if effect_sends_changed else (),
        send_filters=previous.send_filters if effect_sends_changed else (),
    )
    return _EfxReplacement(
        current=current,
        created=created,
        retired=retired,
        direct_filter_changed=direct_filter_changed,
        effect_sends_changed=effect_sends_changed,
    )


def _apply_voice_config(
    playback: Playback, identifier: int, config: VoiceConfig
) -> None:
    al = playback._library.al
    al.source3f(identifier, bindings.AL_POSITION, *config.position)
    al.source3f(identifier, bindings.AL_VELOCITY, *config.velocity)
    al.source3f(identifier, bindings.AL_DIRECTION, *config.direction)
    al.sourcef(identifier, bindings.AL_GAIN, config.gain)
    al.sourcef(identifier, bindings.AL_PITCH, config.pitch)
    al.sourcef(identifier, bindings.AL_MIN_GAIN, config.min_gain)
    al.sourcef(identifier, bindings.AL_MAX_GAIN, config.max_gain)
    al.sourcef(identifier, bindings.AL_REFERENCE_DISTANCE, config.reference_distance)
    al.sourcef(identifier, bindings.AL_MAX_DISTANCE, config.max_distance)
    al.sourcef(identifier, bindings.AL_ROLLOFF_FACTOR, config.rolloff_factor)
    al.sourcef(identifier, bindings.AL_CONE_INNER_ANGLE, config.cone_inner_angle)
    al.sourcef(identifier, bindings.AL_CONE_OUTER_ANGLE, config.cone_outer_angle)
    al.sourcef(identifier, bindings.AL_CONE_OUTER_GAIN, config.cone_outer_gain)
    al.sourcei(identifier, bindings.AL_LOOPING, int(config.looping))
    al.sourcei(identifier, bindings.AL_SOURCE_RELATIVE, int(config.relative))


def _apply_voice_config_changes(
    playback: Playback,
    identifier: int,
    previous: VoiceConfig,
    current: VoiceConfig,
) -> None:
    """Apply only properties changed by a partial voice update."""

    al = playback._library.al
    if current.position != previous.position:
        al.source3f(identifier, bindings.AL_POSITION, *current.position)
    if current.velocity != previous.velocity:
        al.source3f(identifier, bindings.AL_VELOCITY, *current.velocity)
    if current.direction != previous.direction:
        al.source3f(identifier, bindings.AL_DIRECTION, *current.direction)
    float_properties = (
        (bindings.AL_GAIN, previous.gain, current.gain),
        (bindings.AL_PITCH, previous.pitch, current.pitch),
        (bindings.AL_MIN_GAIN, previous.min_gain, current.min_gain),
        (bindings.AL_MAX_GAIN, previous.max_gain, current.max_gain),
        (
            bindings.AL_REFERENCE_DISTANCE,
            previous.reference_distance,
            current.reference_distance,
        ),
        (bindings.AL_MAX_DISTANCE, previous.max_distance, current.max_distance),
        (bindings.AL_ROLLOFF_FACTOR, previous.rolloff_factor, current.rolloff_factor),
        (
            bindings.AL_CONE_INNER_ANGLE,
            previous.cone_inner_angle,
            current.cone_inner_angle,
        ),
        (
            bindings.AL_CONE_OUTER_ANGLE,
            previous.cone_outer_angle,
            current.cone_outer_angle,
        ),
        (
            bindings.AL_CONE_OUTER_GAIN,
            previous.cone_outer_gain,
            current.cone_outer_gain,
        ),
    )
    for parameter, old_value, new_value in float_properties:
        if new_value != old_value:
            al.sourcef(identifier, parameter, new_value)
    if current.looping != previous.looping:
        al.sourcei(identifier, bindings.AL_LOOPING, int(current.looping))
    if current.relative != previous.relative:
        al.sourcei(identifier, bindings.AL_SOURCE_RELATIVE, int(current.relative))


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
    with _active_playbacks_lock, library._context_lock:
        previous_context = library.alc.get_current_context()
        previous_playback = _playback_for_context(library, previous_context)
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
        playback = Playback(
            library,
            device,
            context,
            previous_context,
            previous_playback,
        )
        _active_playbacks.add(playback)
        return playback


@_serialized_playback
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
    with (
        _active_playbacks_lock,
        playback._library._context_lock,
        playback._lock,
    ):
        if not playback._closed:
            _close_playback(playback)


def _close_playback(playback: Playback) -> None:
    """Close a validated, live playback while lifecycle state is serialized."""

    first_error: Exception | None = None

    def remember(error: Exception) -> None:
        nonlocal first_error
        if first_error is None:
            first_error = error

    current_context = playback._library.alc.get_current_context()
    restore_context = (
        _live_previous_context(playback)
        if _same_context(current_context, playback._context)
        else current_context
    )

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
                efx_resources = tuple(playback._voice_efx.values()) + tuple(
                    record.efx for record in playback._streams.values()
                )
                combined_efx = _EfxResources(
                    effects=tuple(
                        identifier
                        for resources in efx_resources
                        for identifier in resources.effects
                    ),
                    slots=tuple(
                        identifier
                        for resources in efx_resources
                        for identifier in resources.slots
                    ),
                    send_filters=tuple(
                        identifier
                        for resources in efx_resources
                        for identifier in resources.filters
                    ),
                )
                _delete_efx_resources(
                    playback,
                    combined_efx,
                    operation="EFX cleanup",
                )
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
            if not playback._library.alc.make_context_current(restore_context):
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
        playback._voice_configs.clear()
        playback._voice_efx.clear()
        playback._streams.clear()
        playback._clips.clear()
        playback._clip_infos.clear()
        playback._closed = True
        _active_playbacks.discard(playback)

    if first_error is not None:
        raise first_error


@_serialized_playback
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
    playback._clip_infos[token] = pcm.info
    return Clip(playback._token, token, identifier, pcm.info)


@_serialized_playback
def _create_voice(
    playback: Playback,
    clip: Clip,
    config: VoiceConfig = _DEFAULT_VOICE_CONFIG,
    *,
    offset_seconds: float = 0.0,
    offset_frames: int | None = None,
    start: bool = True,
) -> Voice:
    """Create one configured static voice and optionally start it."""

    if not isinstance(config, VoiceConfig):
        raise TypeError("config must be a VoiceConfig")
    if not isinstance(start, bool):
        raise TypeError("start must be a boolean")
    clip_identifier = _clip_identifier(playback, clip)
    offset_seconds, offset_frames = _validate_offsets(
        clip.info, offset_seconds, offset_frames
    )
    _prepare_al(playback)
    identifiers = playback._library.al.gen_sources()
    if len(identifiers) != 1:
        raise AudioBackendError("OpenAL did not create exactly one source")
    identifier = identifiers[0]
    efx = _EMPTY_EFX_RESOURCES
    try:
        _check_al_error(playback, "create voice")
        _apply_voice_config(playback, identifier, config)
        playback._library.al.sourcei(identifier, bindings.AL_BUFFER, clip_identifier)
        if offset_frames is not None:
            playback._library.al.sourcei(
                identifier, bindings.AL_SAMPLE_OFFSET, offset_frames
            )
        elif offset_seconds:
            playback._library.al.sourcef(
                identifier, bindings.AL_SEC_OFFSET, offset_seconds
            )
        efx = _install_efx_resources(
            playback,
            identifier,
            config,
        )
        if start:
            playback._library.al.source_play(identifier)
        _check_al_error(playback, "play voice" if start else "create voice")
    except Exception:
        _clear_al_errors(playback)
        playback._library.al.source_stop(identifier)
        playback._library.al.delete_sources((identifier,))
        with suppress(Exception):
            _delete_efx_resources(
                playback,
                efx,
                operation="clean up incomplete voice EFX resources",
            )
        playback._library.al.get_error()
        raise
    token = object()
    playback._voices[token] = identifier
    playback._voice_clips[token] = clip._token
    playback._voice_configs[token] = config
    playback._voice_efx[token] = efx
    return Voice(playback._token, token, identifier)


def _play_voice(
    playback: Playback,
    clip: Clip,
    config: VoiceConfig = _DEFAULT_VOICE_CONFIG,
    *,
    offset_seconds: float = 0.0,
    offset_frames: int | None = None,
) -> Voice:
    """Create and immediately play one voice using a clip."""

    return _create_voice(
        playback,
        clip,
        config,
        offset_seconds=offset_seconds,
        offset_frames=offset_frames,
        start=True,
    )


@_serialized_playback
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
    efx = _EMPTY_EFX_RESOURCES
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
        efx = _install_efx_resources(
            playback,
            source_ids[0],
            config,
        )
        _check_al_error(playback, "configure stream")
    except Exception:
        _clear_al_errors(playback)
        if source_ids:
            playback._library.al.source_stopv(source_ids)
            playback._library.al.delete_sources(source_ids)
        with suppress(Exception):
            _delete_efx_resources(
                playback,
                efx,
                operation="clean up incomplete stream EFX resources",
            )
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
        config=config,
        efx=efx,
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


@_serialized_playback
def try_write_stream(
    playback: Playback,
    stream: Stream,
    samples: Buffer,
) -> bool:
    """Queue one complete PCM chunk, or report bounded-buffer backpressure."""

    record = _stream_record(playback, stream)
    if record.state in (StreamState.FINISHED, StreamState.STOPPED):
        raise InvalidVoiceStateError(
            f"cannot write to a stream in the {record.state.value} state"
        )
    if record.input_finished:
        raise InvalidVoiceStateError("cannot write to a stream after end-of-input")
    if not record.free_buffers:
        return False

    data = _copy_stream_samples(samples)
    if not data:
        raise ValueError("samples cannot be empty")
    frame_width = record.channels * record.sample_type.byte_width
    if len(data) % frame_width:
        raise ValueError("samples must contain a whole number of frames")

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


@_serialized_playback
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


@_serialized_playback
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


@_serialized_playback
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


@_serialized_playback
def _set_voice_config(
    playback: Playback,
    voice: Voice | Stream,
    config: VoiceConfig,
    *,
    changed_only: bool,
) -> None:
    if not isinstance(config, VoiceConfig):
        raise TypeError("config must be a VoiceConfig")
    if isinstance(voice, Stream):
        record = _stream_record(playback, voice)
        identifier = record.identifier
        if config.looping:
            raise ValueError("streaming voices cannot loop")
        previous = record.config
        previous_efx = record.efx
    else:
        identifier = _voice_identifier(playback, voice)
        previous = playback._voice_configs[voice._token]
        previous_efx = playback._voice_efx[voice._token]
    _prepare_al(playback)
    replacement = _prepare_efx_replacement(
        playback,
        previous,
        previous_efx,
        config,
    )
    try:
        if changed_only:
            _apply_voice_config_changes(playback, identifier, previous, config)
        else:
            _apply_voice_config(playback, identifier, config)
        _check_al_error(playback, "configure voice")
        if replacement.direct_filter_changed or replacement.effect_sends_changed:
            _attach_efx_resources(
                playback,
                identifier,
                replacement.current,
                clear_send_count=len(previous_efx.slots),
                attach_direct_filter=replacement.direct_filter_changed,
                attach_effect_sends=replacement.effect_sends_changed,
            )
    except BaseException:
        with suppress(Exception):
            _clear_al_errors(playback)
        with suppress(Exception):
            _apply_voice_config(playback, identifier, previous)
            _check_al_error(playback, "restore voice configuration")
        with suppress(Exception):
            _clear_al_errors(playback)
        if replacement.direct_filter_changed or replacement.effect_sends_changed:
            with suppress(Exception):
                _attach_efx_resources(
                    playback,
                    identifier,
                    previous_efx,
                    clear_send_count=len(replacement.current.slots),
                    attach_direct_filter=replacement.direct_filter_changed,
                    attach_effect_sends=replacement.effect_sends_changed,
                )
        with suppress(Exception):
            _clear_al_errors(playback)
        with suppress(Exception):
            _delete_efx_resources(
                playback,
                replacement.created,
                operation="clean up failed voice configuration",
            )
        playback._library.al.get_error()
        raise

    if isinstance(voice, Stream):
        record.config = config
        record.efx = replacement.current
    else:
        playback._voice_configs[voice._token] = config
        playback._voice_efx[voice._token] = replacement.current

    _delete_efx_resources(
        playback,
        replacement.retired,
        operation="release replaced EFX resources",
    )


def set_voice_config(
    playback: Playback, voice: Voice | Stream, config: VoiceConfig
) -> None:
    """Apply a complete immutable configuration to a live voice or stream."""

    _set_voice_config(playback, voice, config, changed_only=False)


@_serialized_playback
def seek(playback: Playback, voice: Voice, offset_seconds: float) -> None:
    """Move a static voice's playhead to an offset in source-audio seconds."""

    info = _voice_clip_info(playback, voice)
    offset_seconds = _sound_offset(offset_seconds, info.duration_seconds)
    identifier = _voice_identifier(playback, voice)
    _prepare_al(playback)
    playback._library.al.sourcef(identifier, bindings.AL_SEC_OFFSET, offset_seconds)
    _check_al_error(playback, "seek voice")


@_serialized_playback
def seek_frames(playback: Playback, voice: Voice, offset_frames: int) -> None:
    """Move a static voice's playhead to an exact sample-frame offset."""

    info = _voice_clip_info(playback, voice)
    offset_frames = _frame_offset(offset_frames, info.frame_count)
    identifier = _voice_identifier(playback, voice)
    _prepare_al(playback)
    playback._library.al.sourcei(identifier, bindings.AL_SAMPLE_OFFSET, offset_frames)
    _check_al_error(playback, "seek voice by frames")


@_serialized_playback
def rewind(playback: Playback, voice: Voice) -> None:
    """Move a static voice to its beginning and set it to the initial state."""

    _control_voice(playback, voice, "rewind")


@_serialized_playback
def restart(playback: Playback, voice: Voice) -> None:
    """Rewind a static voice and immediately start it playing."""

    identifier = _voice_identifier(playback, voice)
    _prepare_al(playback)
    playback._library.al.source_rewind(identifier)
    playback._library.al.source_play(identifier)
    _check_al_error(playback, "restart voice")


@_serialized_playback
def _set_listener(playback: Playback, listener: Listener) -> None:
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


@_serialized_playback
def _get_listener(playback: Playback) -> Listener:
    """Query the current listener description from a playback context."""

    _prepare_al(playback)
    al = playback._library.al
    position = al.get_listener3f(bindings.AL_POSITION)
    velocity = al.get_listener3f(bindings.AL_VELOCITY)
    orientation = al.get_listenerfv(bindings.AL_ORIENTATION, 6)
    gain = al.get_listenerf(bindings.AL_GAIN)
    _check_al_error(playback, "query listener")
    if len(orientation) != 6:
        raise AudioBackendError("OpenAL returned an invalid listener orientation")
    return Listener(
        position=position,
        velocity=velocity,
        forward=orientation[:3],
        up=orientation[3:],
        gain=float(gain),
    )


@_serialized_playback
def _set_acoustics(playback: Playback, acoustics: Acoustics) -> None:
    """Apply global distance and Doppler controls to a playback context."""

    if not isinstance(acoustics, Acoustics):
        raise TypeError("acoustics must be an Acoustics value")
    _prepare_al(playback)
    al = playback._library.al
    al.distance_model(_DISTANCE_MODEL_TO_AL[acoustics.distance_model])
    al.doppler_factor(acoustics.doppler_factor)
    al.speed_of_sound(acoustics.speed_of_sound)
    _check_al_error(playback, "configure acoustics")


@_serialized_playback
def _get_acoustics(playback: Playback) -> Acoustics:
    """Query global distance and Doppler controls from a playback context."""

    _prepare_al(playback)
    al = playback._library.al
    native_model = int(al.get_integer(bindings.AL_DISTANCE_MODEL))
    doppler_factor = float(al.get_float(bindings.AL_DOPPLER_FACTOR))
    speed_of_sound = float(al.get_float(bindings.AL_SPEED_OF_SOUND))
    _check_al_error(playback, "query acoustics")
    try:
        distance_model = _DISTANCE_MODEL_BY_AL[native_model]
    except KeyError as error:
        raise AudioBackendError(
            f"OpenAL returned unknown distance model 0x{native_model:04x}"
        ) from error
    return Acoustics(
        distance_model=distance_model,
        doppler_factor=doppler_factor,
        speed_of_sound=speed_of_sound,
    )


@_serialized_playback
def get_voice_status(playback: Playback, voice: Voice) -> VoiceStatus:
    """Return the current state and playback offset of a live voice."""

    identifier = _voice_identifier(playback, voice)
    _prepare_al(playback)
    state = _get_voice_state(playback, identifier, "query voice state")
    offset = playback._library.al.get_sourcef(identifier, bindings.AL_SEC_OFFSET)
    offset_frames = playback._library.al.get_sourcei(
        identifier, bindings.AL_SAMPLE_OFFSET
    )
    _check_al_error(playback, "query voice")
    return VoiceStatus(
        state=state,
        offset_seconds=float(offset),
        offset_frames=int(offset_frames),
    )


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


@_serialized_playback
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


@_serialized_playback
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


@_serialized_playback
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


@_serialized_playback
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
    released_efx = tuple(
        playback._voice_efx[token] for token in stopped_tokens
    ) + tuple(playback._streams[token].efx for token in stream_tokens)
    combined_efx = _EfxResources(
        effects=tuple(
            identifier for resources in released_efx for identifier in resources.effects
        ),
        slots=tuple(
            identifier for resources in released_efx for identifier in resources.slots
        ),
        send_filters=tuple(
            identifier for resources in released_efx for identifier in resources.filters
        ),
    )
    _delete_efx_resources(
        playback,
        combined_efx,
        operation="release finished voice EFX resources",
    )
    if stream_buffers:
        playback._library.al.delete_buffers(stream_buffers)
    _check_al_error(playback, "release finished voices and streams")
    for token in stopped_tokens:
        del playback._voices[token]
        del playback._voice_clips[token]
        del playback._voice_configs[token]
        del playback._voice_efx[token]
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

    _release(playback, resource)


@_serialized_playback
def _release(playback: Playback, resource: Clip | Voice | Stream) -> None:
    if isinstance(resource, Stream):
        record = _stream_record(playback, resource)
        _prepare_al(playback)
        playback._library.al.source_stop(record.identifier)
        playback._library.al.delete_sources((record.identifier,))
        _delete_efx_resources(
            playback,
            record.efx,
            operation="release stream EFX resources",
        )
        playback._library.al.delete_buffers(record.buffers)
        _check_al_error(playback, "release stream")
        del playback._streams[resource._token]
        return
    if isinstance(resource, Voice):
        identifier = _voice_identifier(playback, resource)
        _prepare_al(playback)
        playback._library.al.source_stop(identifier)
        playback._library.al.delete_sources((identifier,))
        _delete_efx_resources(
            playback,
            playback._voice_efx[resource._token],
            operation="release voice EFX resources",
        )
        _check_al_error(playback, "release voice")
        del playback._voices[resource._token]
        del playback._voice_clips[resource._token]
        del playback._voice_configs[resource._token]
        del playback._voice_efx[resource._token]
        return
    if isinstance(resource, Clip):
        identifier = _clip_identifier(playback, resource)
        if resource._token in playback._voice_clips.values():
            raise ResourceInUseError("clip is still attached to a live voice")
        _prepare_al(playback)
        playback._library.al.delete_buffers((identifier,))
        _check_al_error(playback, "release clip")
        del playback._clips[resource._token]
        del playback._clip_infos[resource._token]
        return
    raise TypeError("resource must be a Clip, Voice, or Stream")


@dataclass(slots=True)
class _SoundRecord:
    token: object
    voice: Voice
    clip: Clip | None
    info: SoundInfo
    path: Path | None
    pcm: PCM | None
    config: VoiceConfig
    final_status: VoiceStatus | None = None
    end_reason: SoundEndReason | None = None


@dataclass(frozen=True, slots=True)
class _CachedSoundClip:
    clip: Clip


@dataclass(slots=True, eq=False)
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

    @property
    def paused(self) -> bool:
        """Whether the sound is currently paused."""

        return self.state is VoiceState.PAUSED

    @property
    def stopped(self) -> bool:
        """Whether the sound is no longer playing or resumable."""

        return self.state is VoiceState.STOPPED

    @property
    def done(self) -> bool:
        """Whether the sound has completed naturally or was stopped."""

        return self.stopped

    @property
    def finished(self) -> bool:
        """Whether playback reached the end naturally."""

        return self.end_reason is SoundEndReason.FINISHED

    @property
    def end_reason(self) -> SoundEndReason | None:
        """Why the sound ended, or ``None`` while it remains active."""

        return self._runtime.end_reason(self._record)

    @property
    def offset_seconds(self) -> float:
        """Current playhead position in seconds of source audio."""

        return self.status.offset_seconds

    @property
    def offset_frames(self) -> int:
        """Current playhead position as an exact sample-frame offset."""

        return self.status.offset_frames

    @property
    def info(self) -> SoundInfo:
        """Format and length information for the source audio."""

        return self._record.info

    @property
    def path(self) -> Path:
        """Resolved source path for file-backed audio.

        In-memory PCM audio has no source path, so querying this property for
        such a sound raises :class:`AudioError`.
        """

        if self._record.path is None:
            raise AudioError("in-memory PCM audio has no source path")
        return self._record.path

    @property
    def duration_seconds(self) -> float:
        """Duration of the source audio, unaffected by pitch."""

        return self.info.duration_seconds

    @property
    def frame_count(self) -> int:
        """Number of sample frames in the source audio."""

        return self.info.frame_count

    @property
    def channels(self) -> int:
        """Number of interleaved audio channels."""

        return self.info.channels

    @property
    def sample_rate(self) -> int:
        """Number of sample frames per second."""

        return self.info.sample_rate

    @property
    def sample_type(self) -> SampleType:
        """PCM representation used by each channel sample."""

        return self.info.sample_type

    @property
    def remaining_seconds(self) -> float:
        """Source-audio seconds remaining in the current pass."""

        return max(0.0, self.duration_seconds - self.offset_seconds)

    @property
    def remaining_frames(self) -> int:
        """Sample frames remaining in the current pass."""

        return max(0, self.frame_count - self.offset_frames)

    @property
    def progress(self) -> float:
        """Current playhead position as a value from 0.0 through 1.0."""

        return min(1.0, max(0.0, self.offset_frames / self.frame_count))

    @property
    def config(self) -> VoiceConfig:
        """Current complete voice configuration."""

        return self._runtime.config(self._record)

    @property
    def position(self) -> Vector3:
        """Sound location in 3D space."""

        return self.config.position

    @position.setter
    def position(self, value: Vector3) -> None:
        self.update(position=value)

    @property
    def velocity(self) -> Vector3:
        """Sound velocity used for Doppler shift."""

        return self.config.velocity

    @velocity.setter
    def velocity(self, value: Vector3) -> None:
        self.update(velocity=value)

    @property
    def direction(self) -> Vector3:
        """Direction the sound's attenuation cone points."""

        return self.config.direction

    @direction.setter
    def direction(self, value: Vector3) -> None:
        self.update(direction=value)

    @property
    def gain(self) -> float:
        """Linear pre-attenuation amplitude multiplier."""

        return self.config.gain

    @gain.setter
    def gain(self, value: float) -> None:
        self.update(gain=value)

    @property
    def pitch(self) -> float:
        """Playback-rate and pitch multiplier."""

        return self.config.pitch

    @pitch.setter
    def pitch(self, value: float) -> None:
        self.update(pitch=value)

    @property
    def looping(self) -> bool:
        """Whether the complete sound repeats after reaching its end."""

        return self.config.looping

    @looping.setter
    def looping(self, value: bool) -> None:
        self.update(looping=value)

    @property
    def relative(self) -> bool:
        """Whether coordinates are relative to the listener."""

        return self.config.relative

    @relative.setter
    def relative(self, value: bool) -> None:
        self.update(relative=value)

    @property
    def min_gain(self) -> float:
        """Lower clamp applied after distance and cone attenuation."""

        return self.config.min_gain

    @min_gain.setter
    def min_gain(self, value: float) -> None:
        self.update(min_gain=value)

    @property
    def max_gain(self) -> float:
        """Upper clamp applied after distance and cone attenuation."""

        return self.config.max_gain

    @max_gain.setter
    def max_gain(self, value: float) -> None:
        self.update(max_gain=value)

    @property
    def reference_distance(self) -> float:
        """Reference point where distance attenuation has unity gain."""

        return self.config.reference_distance

    @reference_distance.setter
    def reference_distance(self, value: float) -> None:
        self.update(reference_distance=value)

    @property
    def max_distance(self) -> float:
        """Distance used as the outer bound by clamped distance models."""

        return self.config.max_distance

    @max_distance.setter
    def max_distance(self, value: float) -> None:
        self.update(max_distance=value)

    @property
    def rolloff_factor(self) -> float:
        """Multiplier controlling how rapidly distance attenuation changes."""

        return self.config.rolloff_factor

    @rolloff_factor.setter
    def rolloff_factor(self, value: float) -> None:
        self.update(rolloff_factor=value)

    @property
    def cone_inner_angle(self) -> float:
        """Full angle in which a directional sound is unattenuated."""

        return self.config.cone_inner_angle

    @cone_inner_angle.setter
    def cone_inner_angle(self, value: float) -> None:
        self.update(cone_inner_angle=value)

    @property
    def cone_outer_angle(self) -> float:
        """Full angle beyond which cone_outer_gain is applied."""

        return self.config.cone_outer_angle

    @cone_outer_angle.setter
    def cone_outer_angle(self, value: float) -> None:
        self.update(cone_outer_angle=value)

    @property
    def cone_outer_gain(self) -> float:
        """Gain applied outside a directional sound's outer cone."""

        return self.config.cone_outer_gain

    @cone_outer_gain.setter
    def cone_outer_gain(self, value: float) -> None:
        self.update(cone_outer_gain=value)

    @property
    def filter(self) -> Filter | None:
        """Direct EFX filter applied to the sound's dry signal."""

        return self.config.filter

    @filter.setter
    def filter(self, value: Filter | None) -> None:
        self.set_config(replace(self.config, filter=value))

    @property
    def effect_sends(self) -> tuple[EffectSend, ...]:
        """Ordered auxiliary EFX routes applied to this sound."""

        return self.config.effect_sends

    @effect_sends.setter
    def effect_sends(self, value: tuple[EffectSend, ...] | list[EffectSend]) -> None:
        self.set_config(replace(self.config, effect_sends=tuple(value)))

    def pause(self) -> None:
        """Pause the sound if it is currently playing."""

        self._runtime.pause(self._record)

    def resume(self) -> None:
        """Resume the sound if it is paused."""

        self._runtime.resume(self._record)

    def stop(self) -> None:
        """Stop the sound and release its playback voice."""

        self._runtime.stop(self._record)

    def seek(self, offset_seconds: float) -> None:
        """Move the playhead to an offset in source-audio seconds."""

        self._runtime.seek(self._record, offset_seconds)

    def seek_frames(self, offset_frames: int) -> None:
        """Move the playhead to an exact sample-frame offset."""

        self._runtime.seek_frames(self._record, offset_frames)

    def rewind(self) -> None:
        """Move the playhead to the beginning and enter the initial state."""

        self._runtime.rewind(self._record)

    def restart(self) -> None:
        """Start the sound again from its beginning."""

        self._runtime.restart(self._record)

    def set_config(self, config: VoiceConfig) -> None:
        """Apply a complete immutable voice configuration."""

        self._runtime.set_config(self._record, config)

    def update(
        self,
        *,
        position: Vector3 | None = None,
        velocity: Vector3 | None = None,
        direction: Vector3 | None = None,
        gain: float | None = None,
        pitch: float | None = None,
        looping: bool | None = None,
        relative: bool | None = None,
        min_gain: float | None = None,
        max_gain: float | None = None,
        reference_distance: float | None = None,
        max_distance: float | None = None,
        rolloff_factor: float | None = None,
        cone_inner_angle: float | None = None,
        cone_outer_angle: float | None = None,
        cone_outer_gain: float | None = None,
        filter: Filter | None = _OMITTED_FILTER,
        effect_sends: tuple[EffectSend, ...] | list[EffectSend] | None = None,
    ) -> None:
        """Validate and apply a batch of partial source-control changes."""

        self._runtime.update(
            self._record,
            position=position,
            velocity=velocity,
            direction=direction,
            gain=gain,
            pitch=pitch,
            looping=looping,
            relative=relative,
            min_gain=min_gain,
            max_gain=max_gain,
            reference_distance=reference_distance,
            max_distance=max_distance,
            rolloff_factor=rolloff_factor,
            cone_inner_angle=cone_inner_angle,
            cone_outer_angle=cone_outer_angle,
            cone_outer_gain=cone_outer_gain,
            filter=filter,
            effect_sends=effect_sends,
        )

    def __repr__(self) -> str:
        return "PlayingSound(<opaque>)"


def _wave_info(source: wave.Wave_read, path: Path) -> SoundInfo:
    """Validate a WAV header and return its supported PCM layout."""

    if source.getcomptype() != "NONE":
        raise AudioFileError(f"unsupported compressed WAV file: {path}")
    sample_width = source.getsampwidth()
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
        return SoundInfo(
            channels=source.getnchannels(),
            sample_rate=source.getframerate(),
            sample_type=sample_type,
            frame_count=source.getnframes(),
        )
    except (TypeError, ValueError) as error:
        raise AudioFileError(f"unsupported WAV file {path}: {error}") from error


def _read_wave(path: Path) -> PCM:
    try:
        with wave.open(str(path), "rb") as source:
            info = _wave_info(source, path)
            samples = source.readframes(info.frame_count)
    except (EOFError, OSError, wave.Error) as error:
        raise AudioFileError(f"could not read WAV file {path}: {error}") from error

    if len(samples) != info.byte_count:
        raise AudioFileError(
            f"truncated WAV file {path}: expected {info.byte_count} sample bytes, "
            f"read {len(samples)}"
        )

    try:
        return PCM(
            samples=samples,
            channels=info.channels,
            sample_rate=info.sample_rate,
            sample_type=info.sample_type,
        )
    except (TypeError, ValueError) as error:
        raise AudioFileError(f"unsupported WAV file {path}: {error}") from error


def get_sound_info(path: AudioPath) -> SoundInfo:
    """Read WAV format and length information without opening an audio device."""

    if not isinstance(path, (str, PathLike)):
        raise TypeError("sound must be a path to a WAV file")
    normalized = Path(path).expanduser().resolve()
    try:
        with wave.open(str(normalized), "rb") as source:
            return _wave_info(source, normalized)
    except (EOFError, OSError, wave.Error) as error:
        raise AudioFileError(
            f"could not read WAV file {normalized}: {error}"
        ) from error


class _DefaultRuntime:
    """Own the implicit session, cached clips, and active playback voices."""

    __slots__ = (
        "_acoustics",
        "_active",
        "_cache_bytes",
        "_cache_limit",
        "_clips",
        "_closed",
        "_listener",
        "_lock",
        "_pending_evictions",
        "_playback",
    )

    def __init__(self) -> None:
        self._playback: Playback | None = None
        self._clips: OrderedDict[Path, _CachedSoundClip] = OrderedDict()
        self._active: dict[object, _SoundRecord] = {}
        self._cache_limit: int | None = _DEFAULT_SOUND_CACHE_LIMIT
        self._cache_bytes = 0
        self._pending_evictions: set[Path] = set()
        self._listener = _DEFAULT_LISTENER
        self._acoustics = _DEFAULT_ACOUSTICS
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

    def _active_cache_paths(self) -> set[Path]:
        return {
            record.path
            for record in self._active.values()
            if record.path is not None and record.path in self._clips
        }

    def _evict_cached_path(self, path: Path, active_paths: set[Path]) -> bool:
        cached = self._clips.get(path)
        if cached is None:
            self._pending_evictions.discard(path)
            return False
        if path in active_paths:
            self._pending_evictions.add(path)
            return False
        release(self._opened_playback(), cached.clip)
        del self._clips[path]
        self._cache_bytes -= cached.clip.info.byte_count
        self._pending_evictions.discard(path)
        return True

    def _trim_cache(self, *, protected: Path | None = None) -> None:
        active_paths = self._active_cache_paths()
        if protected is not None:
            active_paths.add(protected)
        for path in tuple(self._pending_evictions):
            self._evict_cached_path(path, active_paths)
        while self._cache_limit is not None and self._cache_bytes > self._cache_limit:
            candidate = next(
                (path for path in self._clips if path not in active_paths),
                None,
            )
            if candidate is None:
                return
            self._evict_cached_path(candidate, active_paths)

    def _finalize(
        self,
        record: _SoundRecord,
        status: VoiceStatus,
        *,
        end_reason: SoundEndReason,
    ) -> None:
        playback = self._opened_playback()
        release(playback, record.voice)
        try:
            if record.pcm is not None:
                assert record.clip is not None
                release(playback, record.clip)
        finally:
            if record.pcm is not None:
                record.clip = None
            record.final_status = status
            record.end_reason = end_reason
            del self._active[record.token]
            self._trim_cache()

    def _create_replacement_voice(
        self,
        record: _SoundRecord,
        *,
        offset_seconds: float = 0.0,
        offset_frames: int | None = None,
        start: bool,
    ) -> Voice:
        playback = self._opened_playback()
        clip = record.clip
        uploaded = False
        if clip is None:
            assert record.pcm is not None
            clip = upload(playback, record.pcm)
            uploaded = True
        try:
            voice = _create_voice(
                playback,
                clip,
                record.config,
                offset_seconds=offset_seconds,
                offset_frames=offset_frames,
                start=start,
            )
        except BaseException:
            if uploaded:
                with suppress(Exception):
                    release(playback, clip)
            raise
        record.clip = clip
        return voice

    def _device_disconnected(self) -> bool:
        playback = self._opened_playback()
        library = playback._library
        if not library.alc.is_extension_present(playback._device, "ALC_EXT_disconnect"):
            return False
        _clear_alc_errors(library, playback._device)
        connected = library.alc.get_integerv(
            playback._device, bindings.ALC_CONNECTED, 1
        )[0]
        _check_alc_error(library, playback._device, "query playback device connection")
        return not bool(connected)

    def _status(self, record: _SoundRecord) -> VoiceStatus:
        if record.final_status is not None:
            return record.final_status
        self._require_open()
        status = get_voice_status(self._opened_playback(), record.voice)
        if status.state is VoiceState.STOPPED:
            if self._device_disconnected():
                end_reason = SoundEndReason.DEVICE_LOST
            else:
                end_reason = SoundEndReason.FINISHED
                status = VoiceStatus(
                    state=VoiceState.STOPPED,
                    offset_seconds=record.info.duration_seconds,
                    offset_frames=record.info.frame_count,
                )
            self._finalize(record, status, end_reason=end_reason)
        return status

    def _reap_finished(self) -> None:
        for record in tuple(self._active.values()):
            self._status(record)

    def play(
        self,
        sound: AudioPath | PCM,
        config: VoiceConfig,
        *,
        offset_seconds: float = 0.0,
        offset_frames: int | None = None,
    ) -> PlayingSound:
        if not isinstance(config, VoiceConfig):
            raise TypeError("config must be a VoiceConfig")
        normalized = (
            None if isinstance(sound, PCM) else Path(sound).expanduser().resolve()
        )
        with self._lock:
            self._require_open()
            self._reap_finished()
            if isinstance(sound, PCM):
                pcm = sound
                offset_seconds, offset_frames = _validate_offsets(
                    pcm.info, offset_seconds, offset_frames
                )
                clip = upload(self._ensure_playback(), pcm)
            else:
                assert normalized is not None
                cached = self._clips.get(normalized)
                if cached is None:
                    pcm = _read_wave(normalized)
                    offset_seconds, offset_frames = _validate_offsets(
                        pcm.info, offset_seconds, offset_frames
                    )
                    cached = _CachedSoundClip(
                        clip=upload(self._ensure_playback(), pcm),
                    )
                    self._clips[normalized] = cached
                    self._cache_bytes += cached.clip.info.byte_count
                else:
                    offset_seconds, offset_frames = _validate_offsets(
                        cached.clip.info, offset_seconds, offset_frames
                    )
                self._clips.move_to_end(normalized)
                self._trim_cache(protected=normalized)
                clip = cached.clip
            try:
                voice = _play_voice(
                    self._opened_playback(),
                    clip,
                    config,
                    offset_seconds=offset_seconds,
                    offset_frames=offset_frames,
                )
            except BaseException:
                if isinstance(sound, PCM):
                    with suppress(Exception):
                        release(self._opened_playback(), clip)
                else:
                    with suppress(Exception):
                        self._trim_cache()
                raise
            token = object()
            record = _SoundRecord(
                token=token,
                voice=voice,
                clip=clip,
                info=clip.info,
                path=normalized,
                pcm=sound if isinstance(sound, PCM) else None,
                config=config,
            )
            self._active[token] = record
            return PlayingSound(self, record)

    def set_cache_limit(self, max_bytes: int | None) -> None:
        with self._lock:
            self._require_open()
            self._cache_limit = max_bytes
            self._reap_finished()
            self._trim_cache()

    def clear_cache(self, path: Path | None) -> int:
        with self._lock:
            self._require_open()
            self._reap_finished()
            active_paths = self._active_cache_paths()
            if path is not None:
                return int(self._evict_cached_path(path, active_paths))
            evicted = 0
            for cached_path in tuple(self._clips):
                evicted += self._evict_cached_path(cached_path, active_paths)
            return evicted

    def cache_info(self) -> SoundCacheInfo:
        with self._lock:
            self._require_open()
            self._reap_finished()
            active_paths = self._active_cache_paths()
            return SoundCacheInfo(
                max_bytes=self._cache_limit,
                current_bytes=self._cache_bytes,
                clip_count=len(self._clips),
                active_clip_count=len(active_paths),
                pending_eviction_count=len(self._pending_evictions),
            )

    def status(self, record: _SoundRecord) -> VoiceStatus:
        with self._lock:
            return self._status(record)

    def end_reason(self, record: _SoundRecord) -> SoundEndReason | None:
        with self._lock:
            self._status(record)
            return record.end_reason

    def config(self, record: _SoundRecord) -> VoiceConfig:
        with self._lock:
            return record.config

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
                    offset_frames=status.offset_frames,
                ),
                end_reason=SoundEndReason.STOPPED,
            )

    def seek(self, record: _SoundRecord, offset_seconds: float) -> None:
        offset_seconds = _sound_offset(offset_seconds, record.info.duration_seconds)
        with self._lock:
            self._require_open()
            status = self._status(record)
            if status.state is VoiceState.STOPPED:
                record.voice = self._create_replacement_voice(
                    record,
                    offset_seconds=offset_seconds,
                    start=False,
                )
                record.final_status = None
                record.end_reason = None
                self._active[record.token] = record
                return
            seek(self._opened_playback(), record.voice, offset_seconds)

    def seek_frames(self, record: _SoundRecord, offset_frames: int) -> None:
        offset_frames = _frame_offset(offset_frames, record.info.frame_count)
        with self._lock:
            self._require_open()
            status = self._status(record)
            if status.state is VoiceState.STOPPED:
                record.voice = self._create_replacement_voice(
                    record,
                    offset_frames=offset_frames,
                    start=False,
                )
                record.final_status = None
                record.end_reason = None
                self._active[record.token] = record
                return
            seek_frames(self._opened_playback(), record.voice, offset_frames)

    def rewind(self, record: _SoundRecord) -> None:
        with self._lock:
            self._require_open()
            status = self._status(record)
            if status.state is VoiceState.STOPPED:
                record.voice = self._create_replacement_voice(
                    record,
                    start=False,
                )
                record.final_status = None
                record.end_reason = None
                self._active[record.token] = record
                return
            rewind(self._opened_playback(), record.voice)
            record.end_reason = None

    def restart(self, record: _SoundRecord) -> None:
        with self._lock:
            self._require_open()
            status = self._status(record)
            if status.state is VoiceState.STOPPED:
                record.voice = self._create_replacement_voice(
                    record,
                    start=True,
                )
                record.final_status = None
                record.end_reason = None
                self._active[record.token] = record
                return
            restart(self._opened_playback(), record.voice)
            record.end_reason = None

    def set_config(self, record: _SoundRecord, config: VoiceConfig) -> None:
        if not isinstance(config, VoiceConfig):
            raise TypeError("config must be a VoiceConfig")
        with self._lock:
            status = self._status(record)
            if status.state is VoiceState.STOPPED:
                if record.end_reason is SoundEndReason.SHUTDOWN:
                    self._require_open()
                record.config = config
                return
            set_voice_config(self._opened_playback(), record.voice, config)
            record.config = config

    def update(
        self,
        record: _SoundRecord,
        *,
        position: Vector3 | None = None,
        velocity: Vector3 | None = None,
        direction: Vector3 | None = None,
        gain: float | None = None,
        pitch: float | None = None,
        looping: bool | None = None,
        relative: bool | None = None,
        min_gain: float | None = None,
        max_gain: float | None = None,
        reference_distance: float | None = None,
        max_distance: float | None = None,
        rolloff_factor: float | None = None,
        cone_inner_angle: float | None = None,
        cone_outer_angle: float | None = None,
        cone_outer_gain: float | None = None,
        filter: Filter | None = _OMITTED_FILTER,
        effect_sends: tuple[EffectSend, ...] | list[EffectSend] | None = None,
    ) -> None:
        with self._lock:
            current = record.config
            updated = _voice_config_with_overrides(
                current,
                position=position,
                velocity=velocity,
                direction=direction,
                gain=gain,
                pitch=pitch,
                looping=looping,
                relative=relative,
                min_gain=min_gain,
                max_gain=max_gain,
                reference_distance=reference_distance,
                max_distance=max_distance,
                rolloff_factor=rolloff_factor,
                cone_inner_angle=cone_inner_angle,
                cone_outer_angle=cone_outer_angle,
                cone_outer_gain=cone_outer_gain,
                filter=filter,
                effect_sends=effect_sends,
            )
            status = self._status(record)
            if status.state is VoiceState.STOPPED:
                if record.end_reason is SoundEndReason.SHUTDOWN:
                    self._require_open()
                record.config = updated
                return
            _set_voice_config(
                self._opened_playback(),
                record.voice,
                updated,
                changed_only=True,
            )
            record.config = updated

    def listener(self) -> Listener:
        with self._lock:
            return self._listener

    def set_listener(self, listener: Listener) -> None:
        with self._lock:
            _set_listener(self._ensure_playback(), listener)
            self._listener = listener

    def acoustics(self) -> Acoustics:
        with self._lock:
            return self._acoustics

    def set_acoustics(self, acoustics: Acoustics) -> None:
        with self._lock:
            _set_acoustics(self._ensure_playback(), acoustics)
            self._acoustics = acoustics

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            statuses: dict[object, VoiceStatus] = {}
            for token, record in tuple(self._active.items()):
                try:
                    status = self._status(record)
                except Exception:
                    statuses[token] = VoiceStatus(
                        state=VoiceState.STOPPED,
                        offset_seconds=0.0,
                        offset_frames=0,
                    )
                else:
                    if token in self._active:
                        statuses[token] = VoiceStatus(
                            state=VoiceState.STOPPED,
                            offset_seconds=status.offset_seconds,
                            offset_frames=status.offset_frames,
                        )
            try:
                if self._playback is not None:
                    close_playback(self._playback)
            finally:
                for token, record in self._active.items():
                    record.final_status = statuses[token]
                    record.end_reason = SoundEndReason.SHUTDOWN
                self._active.clear()
                self._clips.clear()
                self._cache_bytes = 0
                self._pending_evictions.clear()
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


def set_sound_cache_limit(max_bytes: int | None) -> None:
    """Set the implicit file cache byte budget, or ``None`` for no limit."""

    if max_bytes is not None:
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int):
            raise TypeError("max_bytes must be an integer or None")
        if max_bytes < 0:
            raise ValueError("max_bytes cannot be negative")
    _get_default_runtime().set_cache_limit(max_bytes)


def clear_sound_cache(path: AudioPath | None = None) -> int:
    """Evict cached file clips, deferring active entries until they finish."""

    normalized: Path | None = None
    if path is not None:
        if not isinstance(path, (str, PathLike)):
            raise TypeError("path must be a path to a WAV file or None")
        normalized = Path(path).expanduser().resolve()
    return _get_default_runtime().clear_cache(normalized)


def get_sound_cache_info() -> SoundCacheInfo:
    """Return byte usage and activity for the implicit file cache."""

    return _get_default_runtime().cache_info()


@overload
def set_listener(playback: Playback, listener: Listener) -> None: ...


@overload
def set_listener(listener: Listener, /) -> None: ...


def set_listener(
    playback: Playback | Listener, listener: Listener | None = None
) -> None:
    """Set the listener for an explicit session or the convenience runtime."""

    if isinstance(playback, Playback):
        if listener is None:
            raise TypeError("listener must be provided with an explicit Playback")
        _set_listener(playback, listener)
        return
    if listener is not None:
        raise TypeError("listener is only valid with an explicit Playback")
    if not isinstance(playback, Listener):
        raise TypeError("listener must be a Listener")
    _get_default_runtime().set_listener(playback)


def get_listener(playback: Playback | None = None) -> Listener:
    """Return the listener for an explicit session or the convenience runtime."""

    if playback is None:
        return _get_default_runtime().listener()
    return _get_listener(playback)


def update_listener(
    playback: Playback | None = None,
    *,
    position: Vector3 | None = None,
    velocity: Vector3 | None = None,
    forward: Vector3 | None = None,
    up: Vector3 | None = None,
    gain: float | None = None,
) -> Listener:
    """Apply a validated batch of partial listener changes and return it."""

    operation = nullcontext() if playback is None else _playback_operation(playback)
    with operation:
        current = get_listener(playback)
        updated = Listener(
            position=current.position if position is None else position,
            velocity=current.velocity if velocity is None else velocity,
            forward=current.forward if forward is None else forward,
            up=current.up if up is None else up,
            gain=current.gain if gain is None else gain,
        )
        if playback is None:
            _get_default_runtime().set_listener(updated)
        else:
            _set_listener(playback, updated)
        return updated


@overload
def set_acoustics(playback: Playback, acoustics: Acoustics) -> None: ...


@overload
def set_acoustics(acoustics: Acoustics, /) -> None: ...


def set_acoustics(
    playback: Playback | Acoustics, acoustics: Acoustics | None = None
) -> None:
    """Set acoustics for an explicit session or the convenience runtime."""

    if isinstance(playback, Playback):
        if acoustics is None:
            raise TypeError("acoustics must be provided with an explicit Playback")
        _set_acoustics(playback, acoustics)
        return
    if acoustics is not None:
        raise TypeError("acoustics is only valid with an explicit Playback")
    if not isinstance(playback, Acoustics):
        raise TypeError("acoustics must be an Acoustics value")
    _get_default_runtime().set_acoustics(playback)


def get_acoustics(playback: Playback | None = None) -> Acoustics:
    """Return acoustics for an explicit session or the convenience runtime."""

    if playback is None:
        return _get_default_runtime().acoustics()
    return _get_acoustics(playback)


def update_acoustics(
    playback: Playback | None = None,
    *,
    distance_model: DistanceModel | None = None,
    doppler_factor: float | None = None,
    speed_of_sound: float | None = None,
) -> Acoustics:
    """Apply a validated batch of partial acoustic changes and return it."""

    operation = nullcontext() if playback is None else _playback_operation(playback)
    with operation:
        current = get_acoustics(playback)
        updated = Acoustics(
            distance_model=(
                current.distance_model if distance_model is None else distance_model
            ),
            doppler_factor=(
                current.doppler_factor if doppler_factor is None else doppler_factor
            ),
            speed_of_sound=(
                current.speed_of_sound if speed_of_sound is None else speed_of_sound
            ),
        )
        if playback is None:
            _get_default_runtime().set_acoustics(updated)
        else:
            _set_acoustics(playback, updated)
        return updated


@overload
def play(
    playback: Playback,
    clip: Clip,
    config: VoiceConfig | None = None,
    *,
    position: Vector3 | None = None,
    velocity: Vector3 | None = None,
    direction: Vector3 | None = None,
    gain: float | None = None,
    pitch: float | None = None,
    looping: bool | None = None,
    relative: bool | None = None,
    min_gain: float | None = None,
    max_gain: float | None = None,
    reference_distance: float | None = None,
    max_distance: float | None = None,
    rolloff_factor: float | None = None,
    cone_inner_angle: float | None = None,
    cone_outer_angle: float | None = None,
    cone_outer_gain: float | None = None,
    filter: Filter | None = None,
    effect_sends: tuple[EffectSend, ...] | list[EffectSend] | None = None,
    offset_seconds: float = 0.0,
    offset_frames: int | None = None,
) -> Voice: ...


@overload
def play(
    playback: AudioPath | PCM,
    /,
    *,
    config: VoiceConfig | None = None,
    position: Vector3 | None = None,
    velocity: Vector3 | None = None,
    direction: Vector3 | None = None,
    gain: float | None = None,
    pitch: float | None = None,
    looping: bool | None = None,
    relative: bool | None = None,
    min_gain: float | None = None,
    max_gain: float | None = None,
    reference_distance: float | None = None,
    max_distance: float | None = None,
    rolloff_factor: float | None = None,
    cone_inner_angle: float | None = None,
    cone_outer_angle: float | None = None,
    cone_outer_gain: float | None = None,
    filter: Filter | None = None,
    effect_sends: tuple[EffectSend, ...] | list[EffectSend] | None = None,
    offset_seconds: float = 0.0,
    offset_frames: int | None = None,
) -> PlayingSound: ...


def play(
    playback: Playback | AudioPath | PCM,
    clip: Clip | None = None,
    config: VoiceConfig | None = None,
    *,
    position: Vector3 | None = None,
    velocity: Vector3 | None = None,
    direction: Vector3 | None = None,
    gain: float | None = None,
    pitch: float | None = None,
    looping: bool | None = None,
    relative: bool | None = None,
    min_gain: float | None = None,
    max_gain: float | None = None,
    reference_distance: float | None = None,
    max_distance: float | None = None,
    rolloff_factor: float | None = None,
    cone_inner_angle: float | None = None,
    cone_outer_angle: float | None = None,
    cone_outer_gain: float | None = None,
    filter: Filter | None = _OMITTED_FILTER,
    effect_sends: tuple[EffectSend, ...] | list[EffectSend] | None = None,
    offset_seconds: float = 0.0,
    offset_frames: int | None = None,
) -> Voice | PlayingSound:
    """Play an explicit clip, WAV file, or PCM value.

    ``play(playback, clip, config)`` preserves the explicit managed API.
    ``play(sound, config=config)`` starts asynchronous, fire-and-forget playback
    through the default runtime and returns an optional control handle. *sound*
    may be a WAV path or an in-memory :class:`PCM` value. Individual keyword
    controls override the corresponding values in ``config``.
    """

    resolved_config = _voice_config_with_overrides(
        config,
        position=position,
        velocity=velocity,
        direction=direction,
        gain=gain,
        pitch=pitch,
        looping=looping,
        relative=relative,
        min_gain=min_gain,
        max_gain=max_gain,
        reference_distance=reference_distance,
        max_distance=max_distance,
        rolloff_factor=rolloff_factor,
        cone_inner_angle=cone_inner_angle,
        cone_outer_angle=cone_outer_angle,
        cone_outer_gain=cone_outer_gain,
        filter=filter,
        effect_sends=effect_sends,
    )
    if isinstance(playback, Playback):
        if clip is None:
            raise TypeError("clip must be provided with an explicit Playback")
        return _play_voice(
            playback,
            clip,
            resolved_config,
            offset_seconds=offset_seconds,
            offset_frames=offset_frames,
        )
    if clip is not None:
        raise TypeError("clip is only valid with an explicit Playback")
    if not isinstance(playback, (str, PathLike, PCM)):
        raise TypeError("sound must be a path to a WAV file or a PCM value")
    return _get_default_runtime().play(
        playback,
        resolved_config,
        offset_seconds=offset_seconds,
        offset_frames=offset_frames,
    )


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
    "Acoustics",
    "AudioBackendError",
    "AudioError",
    "AudioFileError",
    "Clip",
    "DistanceModel",
    "EffectSend",
    "Filter",
    "HRTFStatus",
    "HighPassFilter",
    "InvalidHandleError",
    "InvalidVoiceStateError",
    "Listener",
    "LowPassFilter",
    "PCM",
    "Playback",
    "PlaybackConfig",
    "PlaybackClosedError",
    "PlaybackDevice",
    "PlaybackInfo",
    "PlaybackOpenError",
    "PlayingSound",
    "ResourceInUseError",
    "Reverb",
    "SampleType",
    "SoundCacheInfo",
    "SoundEndReason",
    "SoundInfo",
    "Stream",
    "StreamState",
    "StreamStatus",
    "Vector3",
    "Voice",
    "VoiceConfig",
    "VoiceState",
    "VoiceStatus",
    "clear_sound_cache",
    "close_playback",
    "finish_stream",
    "get_acoustics",
    "get_listener",
    "get_playback_info",
    "get_sound_cache_info",
    "get_sound_info",
    "get_voice_status",
    "list_playback_devices",
    "open_playback",
    "open_stream",
    "pause",
    "play",
    "release",
    "release_finished",
    "restart",
    "resume",
    "rewind",
    "seek",
    "seek_frames",
    "set_acoustics",
    "set_listener",
    "set_sound_cache_limit",
    "set_voice_config",
    "shutdown",
    "start_stream",
    "stop",
    "try_write_stream",
    "update_acoustics",
    "update_listener",
    "update_stream",
    "upload",
]
