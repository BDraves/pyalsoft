"""Spatial and acoustic configuration values."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import cast

from pyalsoft._managed._values import Vector3 as Vector3
from pyalsoft._managed._values import _finite_float, _vector3
from pyalsoft._managed.audio import SoundInfo
from pyalsoft._managed.effects import (
    _OMITTED_VALUE,
    EffectSend,
    Filter,
    _validate_filter,
)

_FLOAT32_MAX = float.fromhex("0x1.fffffep+127")


class DistanceModel(Enum):
    """Distance-attenuation model used by a playback context.

    ``NONE`` disables distance attenuation. The other values select the OpenAL
    inverse, linear, or exponential formulas, with either clamped or unclamped
    distance inputs.

    Attributes:
        NONE: Do not attenuate sounds based on distance.
        INVERSE: Use the inverse-distance formula.
        INVERSE_CLAMPED: Use inverse distance clamped to the configured bounds.
        LINEAR: Use the linear-distance formula.
        LINEAR_CLAMPED: Use linear distance clamped to the configured bounds.
        EXPONENT: Use the exponential-distance formula.
        EXPONENT_CLAMPED: Use exponential distance clamped to the configured bounds.
    """

    NONE = "none"
    INVERSE = "inverse"
    INVERSE_CLAMPED = "inverse_clamped"
    LINEAR = "linear"
    LINEAR_CLAMPED = "linear_clamped"
    EXPONENT = "exponent"
    EXPONENT_CLAMPED = "exponent_clamped"


class SpatializationMode(Enum):
    """Per-source spatialization behavior.

    ``AUTO`` follows the source format, ``ENABLED`` forces positional processing,
    and ``DISABLED`` bypasses position, distance, cone, and Doppler processing.
    """

    AUTO = "auto"
    ENABLED = "enabled"
    DISABLED = "disabled"


class DirectChannelsMode(Enum):
    """Routing behavior for non-spatial stereo sources."""

    OFF = "off"
    DROP_UNMATCHED = "drop_unmatched"
    REMIX_UNMATCHED = "remix_unmatched"


class StereoMode(Enum):
    """Processing mode for ordinary stereo source data."""

    NORMAL = "normal"
    SUPER_STEREO = "super_stereo"


@dataclass(frozen=True, slots=True)
class Resampler:
    """One implementation-provided source resampler.

    Values are returned by [`list_resamplers`][pyalsoft.list_resamplers].
    Applications should select from that result rather than constructing values.
    """

    index: int
    name: str
    is_default: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.index, bool) or not isinstance(self.index, int):
            raise TypeError("index must be an integer")
        if self.index < 0:
            raise ValueError("index cannot be negative")
        if not isinstance(self.name, str):
            raise TypeError("name must be a string")
        if not self.name:
            raise ValueError("name cannot be empty")
        if not isinstance(self.is_default, bool):
            raise TypeError("is_default must be a boolean")


_OMITTED_DISTANCE_MODEL = cast(DistanceModel | None, _OMITTED_VALUE)
_OMITTED_STEREO_ANGLES = cast(tuple[float, float] | None, _OMITTED_VALUE)
_OMITTED_RESAMPLER = cast(Resampler | None, _OMITTED_VALUE)
_OMITTED_SUPER_STEREO_WIDTH = cast(float | None, _OMITTED_VALUE)


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


@dataclass(frozen=True, slots=True, kw_only=True)
class VoiceConfig:
    """Complete immutable configuration for a voice or stream.

    Mono audio is normally required for positional controls to have an audible
    effect. Gain values are linear amplitude multipliers; pitch changes playback
    rate and pitch together. Streaming voices reject ``looping=True``.

    Attributes:
        position: Sound position in world or listener-relative coordinates.
        velocity: Sound velocity used for Doppler shift.
        direction: Direction of the attenuation cone; the zero vector is
            omnidirectional.
        gain: Non-negative pre-attenuation linear gain.
        pitch: Playback-rate multiplier from 0.5 through 2.0.
        looping: Whether static audio repeats after reaching its end.
        relative: Whether coordinates are relative to the listener.
        min_gain: Lower post-attenuation gain clamp, from 0.0 through 1.0.
        max_gain: Upper post-attenuation gain clamp, from 0.0 through 1.0 and
            not less than ``min_gain``.
        reference_distance: Non-negative distance at which attenuation has
            unity gain.
        max_distance: Non-negative outer bound used by clamped distance models.
        rolloff_factor: Non-negative multiplier for distance attenuation.
        cone_inner_angle: Full unattenuated cone angle in degrees, from 0 to 360.
        cone_outer_angle: Full outer cone angle in degrees, from 0 to 360.
        cone_outer_gain: Linear gain outside the outer cone, from 0.0 through 1.0.
        distance_model: Optional per-source distance model. ``None`` inherits the
            playback context's model.
        radius: Non-negative physical source radius in world units.
        spatialization: Whether spatial processing is automatic, forced, or off.
        direct_channels: Direct stereo channel routing behavior.
        stereo_angles: Optional left and right virtual-speaker angles in radians.
        resampler: Optional implementation-provided source resampler.
        air_absorption_factor: Distance-based high-frequency absorption strength.
        room_rolloff_factor: Distance rolloff applied to auxiliary effect paths.
        stereo_mode: Normal stereo or UHJ Super Stereo processing.
        super_stereo_width: Optional Super Stereo soundfield width.
        filter: Optional EFX filter applied directly to the dry signal.
        effect_sends: Ordered auxiliary EFX routes applied to the wet signal.

    Raises:
        TypeError: A field has the wrong type.
        ValueError: A numeric field is non-finite or outside its supported range.
    """

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
    distance_model: DistanceModel | None = None
    radius: float = 0.0
    spatialization: SpatializationMode = SpatializationMode.AUTO
    direct_channels: DirectChannelsMode = DirectChannelsMode.OFF
    stereo_angles: tuple[float, float] | None = None
    resampler: Resampler | None = None
    air_absorption_factor: float = 0.0
    room_rolloff_factor: float = 0.0
    stereo_mode: StereoMode = StereoMode.NORMAL
    super_stereo_width: float | None = None
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
        if self.distance_model is not None and not isinstance(
            self.distance_model, DistanceModel
        ):
            raise TypeError("distance_model must be a DistanceModel or None")
        radius = _finite_float("radius", self.radius)
        if radius < 0.0:
            raise ValueError("radius cannot be negative")
        if not isinstance(self.spatialization, SpatializationMode):
            raise TypeError("spatialization must be a SpatializationMode")
        if not isinstance(self.direct_channels, DirectChannelsMode):
            raise TypeError("direct_channels must be a DirectChannelsMode")
        if self.stereo_angles is None:
            stereo_angles = None
        else:
            if not isinstance(self.stereo_angles, (tuple, list)):
                raise TypeError("stereo_angles must be a tuple or list")
            if len(self.stereo_angles) != 2:
                raise ValueError("stereo_angles must contain exactly two values")
            stereo_angles = (
                _finite_float("stereo_angles[0]", self.stereo_angles[0]),
                _finite_float("stereo_angles[1]", self.stereo_angles[1]),
            )
        if self.resampler is not None and not isinstance(self.resampler, Resampler):
            raise TypeError("resampler must be a Resampler or None")
        air_absorption_factor = _finite_float(
            "air_absorption_factor", self.air_absorption_factor
        )
        if not 0.0 <= air_absorption_factor <= 10.0:
            raise ValueError("air_absorption_factor must be between 0.0 and 10.0")
        room_rolloff_factor = _finite_float(
            "room_rolloff_factor", self.room_rolloff_factor
        )
        if not 0.0 <= room_rolloff_factor <= 10.0:
            raise ValueError("room_rolloff_factor must be between 0.0 and 10.0")
        if not isinstance(self.stereo_mode, StereoMode):
            raise TypeError("stereo_mode must be a StereoMode")
        if self.super_stereo_width is None:
            super_stereo_width = None
        else:
            super_stereo_width = _finite_float(
                "super_stereo_width", self.super_stereo_width
            )
            if not 0.0 <= super_stereo_width <= 1.0:
                raise ValueError("super_stereo_width must be between 0.0 and 1.0")
            if self.stereo_mode is not StereoMode.SUPER_STEREO:
                raise ValueError(
                    "super_stereo_width requires stereo_mode=StereoMode.SUPER_STEREO"
                )
        if self.direct_channels is not DirectChannelsMode.OFF:
            if self.spatialization is SpatializationMode.ENABLED:
                raise ValueError(
                    "direct_channels cannot be combined with enabled spatialization"
                )
            if stereo_angles is not None or self.stereo_mode is not StereoMode.NORMAL:
                raise ValueError(
                    "direct_channels cannot be combined with virtualized stereo controls"
                )
        if stereo_angles is not None and self.stereo_mode is not StereoMode.NORMAL:
            raise ValueError(
                "stereo_angles cannot be combined with Super Stereo processing"
            )
        _validate_filter("filter", self.filter)
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
        object.__setattr__(self, "radius", radius)
        object.__setattr__(self, "stereo_angles", stereo_angles)
        object.__setattr__(self, "air_absorption_factor", air_absorption_factor)
        object.__setattr__(self, "room_rolloff_factor", room_rolloff_factor)
        object.__setattr__(self, "super_stereo_width", super_stereo_width)
        object.__setattr__(self, "effect_sends", effect_sends)


_DEFAULT_VOICE_CONFIG = VoiceConfig()


@dataclass(frozen=True, slots=True, kw_only=True)
class Listener:
    """Complete immutable spatial state for a playback listener.

    Attributes:
        position: Listener position in world coordinates.
        velocity: Listener velocity used for Doppler shift.
        forward: Non-zero vector describing the viewing direction.
        up: Non-zero vector describing the upward direction.
        gain: Non-negative linear gain applied to the final mix.

    Raises:
        TypeError: A field has the wrong type.
        ValueError: A vector is invalid or ``gain`` is negative or non-finite.
    """

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
    """Complete immutable acoustic settings for one playback context.

    Attributes:
        distance_model: Formula used for distance attenuation.
        doppler_factor: Non-negative scale for Doppler shift; 0 disables it.
        speed_of_sound: Propagation speed in world-units per second; at least
            0.0001. The default 343.3 represents meters per second in dry air.

    Raises:
        TypeError: A field has the wrong type.
        ValueError: A numeric field is non-finite or outside its supported range.
    """

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


_DEFAULT_LISTENER = Listener()
_DEFAULT_ACOUSTICS = Acoustics()
