"""Playback device descriptions, states, and opaque resource handles."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from pyalsoft._managed.audio import SoundInfo


class VoiceState(Enum):
    """Observed playback state of a static voice.

    Attributes:
        INITIAL: Ready to play from the beginning.
        PLAYING: Currently advancing through the clip.
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

    Attributes:
        hrtf: Whether to request HRTF rendering. ``None`` leaves the backend's
            default unchanged. A request is ignored when the selected device
            does not expose ``ALC_SOFT_HRTF``; inspect ``PlaybackInfo.hrtf_status``
            for the result.

    Raises:
        TypeError: ``hrtf`` is neither a boolean nor ``None``.
    """

    hrtf: bool | None = None

    def __post_init__(self) -> None:
        if self.hrtf is not None and not isinstance(self.hrtf, bool):
            raise TypeError("hrtf must be a boolean or None")


@dataclass(frozen=True, slots=True)
class PlaybackInfo:
    """Observed properties of an open playback session.

    Attributes:
        device_name: Implementation-provided name of the opened device.
        renderer: Active OpenAL renderer name.
        version: Active OpenAL implementation version.
        hrtf_status: Observed HRTF state.
        hrtf_name: Active HRTF specifier, or ``None`` when none is available.
    """

    device_name: str
    renderer: str
    version: str
    hrtf_status: HRTFStatus
    hrtf_name: str | None


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
        offset_seconds: Current playhead position in source-audio seconds.
        offset_frames: Current playhead position as an exact sample-frame index.
    """

    state: VoiceState
    offset_seconds: float
    offset_frames: int = 0


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
    """Opaque identity for PCM uploaded to a playback session.

    Do not construct instances directly. A clip belongs to the
    [`Playback`][pyalsoft.Playback] that returned it and remains valid until it
    is passed to [`release`][pyalsoft.release] or that session is closed.
    """

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
