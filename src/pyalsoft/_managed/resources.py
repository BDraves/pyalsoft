"""Playback device descriptions, states, and opaque resource handles."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from pyalsoft._managed.audio import BufferInfo, SoundInfo


class VoiceState(Enum):
    """Observed playback state of a static voice.

    Attributes:
        INITIAL: Ready to play from the beginning.
        PLAYING: Playing, including while waiting for a scheduled start time.
        PAUSED: Paused at the current playhead position.
        STOPPED: Finished naturally or explicitly stopped.
    """

    INITIAL = "initial"
    PLAYING = "playing"
    PAUSED = "paused"
    STOPPED = "stopped"


class StreamState(Enum):
    """Managed lifecycle state of a stream.

    Attributes:
        INITIAL: Created but not yet started.
        PLAYING: Started and logically playing, including during an underrun.
        PAUSED: Explicitly paused.
        FINISHED: End-of-input was declared and all queued audio drained.
        STOPPED: Explicitly stopped; queued audio was discarded.
    """

    INITIAL = "initial"
    PLAYING = "playing"
    PAUSED = "paused"
    FINISHED = "finished"
    STOPPED = "stopped"


class SoundEndReason(Enum):
    """Why a convenience playback handle entered its terminal state.

    Attributes:
        FINISHED: Playback reached the end of the source naturally.
        STOPPED: The sound was stopped explicitly.
        SHUTDOWN: The convenience runtime was shut down while the sound was active.
        DEVICE_LOST: The backend reported that the playback device disconnected.
    """

    FINISHED = "finished"
    STOPPED = "stopped"
    SHUTDOWN = "shutdown"
    DEVICE_LOST = "device_lost"


class HRTFStatus(Enum):
    """Observed HRTF state for an open playback session.

    Attributes:
        UNAVAILABLE: The device does not expose ``ALC_SOFT_HRTF``.
        DISABLED: HRTF rendering is disabled.
        ENABLED: HRTF rendering is enabled.
        DENIED: HRTF was requested but could not be enabled.
        REQUIRED: HRTF was enabled because the device requires it.
        HEADPHONES_DETECTED: HRTF was enabled after headphone detection.
        UNSUPPORTED_FORMAT: The output format does not support HRTF rendering.
        UNKNOWN: The backend returned a status unknown to this PyALSoft version.
    """

    UNAVAILABLE = "unavailable"
    DISABLED = "disabled"
    ENABLED = "enabled"
    DENIED = "denied"
    REQUIRED = "required"
    HEADPHONES_DETECTED = "headphones_detected"
    UNSUPPORTED_FORMAT = "unsupported_format"
    UNKNOWN = "unknown"


class PlaybackOutputMode(Enum):
    """Requested or observed playback-device output layout.

    ``ANY`` lets the backend select a layout. The stereo variants distinguish
    ordinary speaker mixing, UHJ surround encoding, and HRTF rendering.
    ``UNKNOWN`` is reserved for an unrecognized value reported by a newer
    backend and cannot be requested in [`PlaybackConfig`][pyalsoft.PlaybackConfig].
    """

    ANY = "any"
    MONO = "mono"
    STEREO = "stereo"
    STEREO_BASIC = "stereo_basic"
    STEREO_UHJ = "stereo_uhj"
    STEREO_HRTF = "stereo_hrtf"
    QUAD = "quad"
    SURROUND_5_1 = "surround_5_1"
    SURROUND_6_1 = "surround_6_1"
    SURROUND_7_1 = "surround_7_1"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class PlaybackDevice:
    """A named playback device reported by the selected OpenAL runtime.

    Instances returned by
    [`list_playback_devices`][pyalsoft.list_playback_devices] can be passed
    directly to [`open_playback`][pyalsoft.open_playback].

    Attributes:
        name: Implementation-provided device specifier.
        is_default: Whether the runtime reported this as its default device.

    Raises:
        TypeError: ``name`` is not a string or ``is_default`` is not a boolean.
        ValueError: ``name`` is empty.
    """

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
    """Preferences applied while creating an OpenAL playback context.

    ``None`` preserves the backend default when opening a session. When passed
    to [`reconfigure_playback`][pyalsoft.reconfigure_playback], ``None`` omits
    that field from a patch and preserves the session's previous request. With
    ``replace=True``, ``None`` returns the field to backend-selected behavior.

    Attributes:
        sample_rate: Requested device sample rate in frames per second. ``None``
            preserves the backend default.
        refresh_rate: Requested context refresh rate in updates per second.
            ``None`` preserves the backend default. OpenAL Soft accepts but
            ignores this core OpenAL attribute.
        synchronous: Whether to request a synchronous context. ``None``
            preserves the backend default. OpenAL Soft accepts but ignores this
            core OpenAL attribute.
        mono_sources: Requested minimum number of mono, spatial source voices.
        stereo_sources: Requested minimum number of stereo, non-spatial source
            voices.
        max_auxiliary_sends: Requested maximum EFX sends per source. A request
            is ignored when ``ALC_EXT_EFX`` is unavailable.
        hrtf: Whether to request HRTF rendering. ``None`` leaves the backend's
            default unchanged. A request is ignored when the selected device
            does not expose ``ALC_SOFT_HRTF``; inspect ``PlaybackInfo.hrtf_status``
            for the result.
        hrtf_name: Preferred HRTF profile from
            [`list_hrtf_profiles`][pyalsoft.list_hrtf_profiles]. This is a hint
            independent of ``hrtf`` and is ignored when ``ALC_SOFT_HRTF`` is
            unavailable.
        output_limiter: Whether to request the device output limiter. ``None``
            preserves the backend default. A request is ignored when
            ``ALC_SOFT_output_limiter`` is unavailable.
        output_mode: Requested speaker or stereo-rendering layout. ``None``
            preserves the backend default. A request is ignored when
            ``ALC_SOFT_output_mode`` is unavailable.

    Raises:
        TypeError: A field has the wrong type.
        ValueError: A numeric request is outside the ALC integer range or
            ``PlaybackOutputMode.UNKNOWN`` is requested.
    """

    sample_rate: int | None = None
    refresh_rate: int | None = None
    synchronous: bool | None = None
    mono_sources: int | None = None
    stereo_sources: int | None = None
    max_auxiliary_sends: int | None = None
    hrtf: bool | None = None
    hrtf_name: str | None = None
    output_limiter: bool | None = None
    output_mode: PlaybackOutputMode | None = None

    def __post_init__(self) -> None:
        for name in ("sample_rate", "refresh_rate"):
            value = getattr(self, name)
            if value is None:
                continue
            _validate_optional_alc_integer(name, value, minimum=1)
        for name in (
            "mono_sources",
            "stereo_sources",
            "max_auxiliary_sends",
        ):
            value = getattr(self, name)
            if value is None:
                continue
            _validate_optional_alc_integer(name, value, minimum=0)
        for name in ("synchronous", "hrtf", "output_limiter"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, bool):
                raise TypeError(f"{name} must be a boolean or None")
        if self.hrtf_name is not None:
            if not isinstance(self.hrtf_name, str):
                raise TypeError("hrtf_name must be a string or None")
            if not self.hrtf_name:
                raise ValueError("hrtf_name cannot be empty")
        if self.output_mode is not None and not isinstance(
            self.output_mode, PlaybackOutputMode
        ):
            raise TypeError("output_mode must be a PlaybackOutputMode or None")
        if self.output_mode is PlaybackOutputMode.UNKNOWN:
            raise ValueError("output_mode cannot be PlaybackOutputMode.UNKNOWN")


_ALC_INTEGER_MAX = 2**31 - 1


def _validate_optional_alc_integer(name: str, value: object, *, minimum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer or None")
    if not minimum <= value <= _ALC_INTEGER_MAX:
        if minimum == 0:
            raise ValueError(f"{name} must be between 0 and {_ALC_INTEGER_MAX}")
        raise ValueError(f"{name} must be between {minimum} and {_ALC_INTEGER_MAX}")


@dataclass(frozen=True, slots=True)
class PlaybackInfo:
    """Observed properties of an open playback session.

    Attributes:
        device_name: Implementation-provided name of the opened device.
        renderer: Active OpenAL renderer name.
        version: Active OpenAL implementation version.
        hrtf_status: Observed HRTF state.
        hrtf_name: Active HRTF specifier, or ``None`` when none is available.
        sample_rate: Active device sample rate in frames per second.
        refresh_rate: Active context refresh rate in updates per second.
        synchronous: Whether the active context is synchronous.
        mono_sources: Number of mono, spatial source voices available.
        stereo_sources: Number of stereo, non-spatial source voices available.
        max_auxiliary_sends: Active EFX send limit per source, or ``None`` when
            ``ALC_EXT_EFX`` is unavailable.
        output_limiter: Active output-limiter state, or ``None`` when
            ``ALC_SOFT_output_limiter`` is unavailable.
        output_mode: Active device output mode, or ``None`` when
            ``ALC_SOFT_output_mode`` is unavailable.
    """

    device_name: str
    renderer: str
    version: str
    hrtf_status: HRTFStatus
    hrtf_name: str | None
    sample_rate: int | None = None
    refresh_rate: int | None = None
    synchronous: bool | None = None
    mono_sources: int | None = None
    stereo_sources: int | None = None
    max_auxiliary_sends: int | None = None
    output_limiter: bool | None = None
    output_mode: PlaybackOutputMode | None = None


@dataclass(frozen=True, slots=True)
class SoundCacheInfo:
    """Observed state of the implicit file-clip cache.

    Attributes:
        max_bytes: Configured byte budget, or ``None`` when unlimited.
        current_bytes: Bytes occupied by all cached clips, including pinned ones.
        clip_count: Number of cached file clips.
        active_clip_count: Number of cached clips pinned by active sounds.
        pending_eviction_count: Pinned clips marked for eviction after playback.
    """

    max_bytes: int | None
    current_bytes: int
    clip_count: int
    active_clip_count: int
    pending_eviction_count: int


@dataclass(frozen=True, slots=True)
class VoiceStatus:
    """Runtime state observed from a static voice.

    Attributes:
        state: Current OpenAL playback state.
        offset_seconds: Current playhead position in source-audio seconds. This
            is negative while consuming an initial playback delay.
        offset_frames: Current playhead position as an exact sample-frame index.
            This is negative while consuming an initial playback delay.
    """

    state: VoiceState
    offset_seconds: float
    offset_frames: int = 0


@dataclass(frozen=True, slots=True)
class VoiceLatency:
    """Atomically measured source position and physical-output latency.

    ``offset_frames_fixed`` preserves OpenAL's exact unsigned 32.32
    fixed-point sample offset. The convenience properties convert it to a
    floating-point frame count or source-audio seconds only when requested.
    """

    offset_frames_fixed: int
    output_latency_ns: int
    sample_rate: int

    @property
    def offset_frames(self) -> float:
        """Source position in sample frames, including the fractional frame."""

        return self.offset_frames_fixed / (1 << 32)

    @property
    def offset_seconds(self) -> float:
        """Source position in source-audio seconds."""

        return self.offset_frames / self.sample_rate

    @property
    def output_latency_seconds(self) -> float:
        """Physical-output latency in seconds."""

        return self.output_latency_ns / 1_000_000_000


@dataclass(frozen=True, slots=True)
class VoiceClock:
    """Atomically measured source position and audio-device clock time.

    ``offset_frames_fixed`` preserves OpenAL's exact signed 32.32 fixed-point
    sample offset. The device time remains an integer nanosecond count.
    """

    offset_frames_fixed: int
    device_time_ns: int
    sample_rate: int

    @property
    def offset_frames(self) -> float:
        """Source position in sample frames, including the fractional frame."""

        return self.offset_frames_fixed / (1 << 32)

    @property
    def offset_seconds(self) -> float:
        """Source position in source-audio seconds."""

        return self.offset_frames / self.sample_rate

    @property
    def device_time_seconds(self) -> float:
        """Audio-device clock time in seconds."""

        return self.device_time_ns / 1_000_000_000


@dataclass(frozen=True, slots=True)
class PlaybackClock:
    """Atomically measured audio-device clock and output latency."""

    device_time_ns: int
    output_latency_ns: int

    @property
    def device_time_seconds(self) -> float:
        """Audio-device clock time in seconds."""

        return self.device_time_ns / 1_000_000_000

    @property
    def output_latency_seconds(self) -> float:
        """Physical-output latency in seconds."""

        return self.output_latency_ns / 1_000_000_000


@dataclass(frozen=True, slots=True)
class StreamStatus:
    """Runtime state and queue accounting for a stream.

    Attributes:
        state: Current managed lifecycle state.
        input_finished: Whether end-of-input has been declared.
        queued_chunks: Chunks queued for playback, including the active chunk.
        queued_seconds: Approximate source-audio duration remaining in the queue.
        underrun_count: Number of distinct times a playing stream exhausted its
            queue before end-of-input.
    """

    state: StreamState
    input_finished: bool
    queued_chunks: int
    queued_seconds: float
    underrun_count: int


@dataclass(frozen=True, slots=True)
class Clip:
    """Opaque identity for audio data uploaded to a playback session.

    Do not construct instances directly. A clip belongs to the
    [`Playback`][pyalsoft.Playback] that returned it and remains valid until it
    is passed to [`release`][pyalsoft.release] or that session is closed.
    """

    _owner: object = field(repr=False)
    _token: object = field(repr=False)
    _identifier: int = field(repr=False)
    _info: SoundInfo | BufferInfo = field(repr=False)
    _loop_points: tuple[int, int] | None = field(repr=False)

    @property
    def info(self) -> SoundInfo | BufferInfo:
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

    @property
    def loop_points(self) -> tuple[int, int] | None:
        """Configured ``(start, end)`` loop-frame range, or ``None``.

        The start frame is inclusive and the end frame is exclusive. When this
        is ``None``, a looping voice repeats the complete clip.
        """

        return self._loop_points

    def __repr__(self) -> str:
        return "Clip(<opaque>)"


@dataclass(frozen=True, slots=True)
class Voice:
    """Opaque identity for one playback instance of a clip.

    Do not construct instances directly. A voice belongs to the
    [`Playback`][pyalsoft.Playback] that returned it and remains valid until it
    is released or its session is closed.
    """

    _owner: object = field(repr=False)
    _token: object = field(repr=False)
    _identifier: int = field(repr=False)

    def __repr__(self) -> str:
        return "Voice(<opaque>)"


@dataclass(frozen=True, slots=True)
class Stream:
    """Opaque identity for one managed streaming source.

    Do not construct instances directly. A stream belongs to the
    [`Playback`][pyalsoft.Playback] that returned it and remains valid until it
    is released or its session is closed.
    """

    _owner: object = field(repr=False)
    _token: object = field(repr=False)
    _identifier: int = field(repr=False)

    def __repr__(self) -> str:
        return "Stream(<opaque>)"
