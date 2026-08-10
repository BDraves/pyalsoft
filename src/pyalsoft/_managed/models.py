"""Values and opaque handles shared by the managed audio APIs."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from os import PathLike
from typing import cast

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
