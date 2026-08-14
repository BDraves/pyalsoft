"""Spatial, acoustic, and effects configuration values."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import cast

from pyalsoft._managed.audio import SoundInfo

type Vector3 = tuple[float, float, float]
"""A Cartesian ``(x, y, z)`` vector used for spatial coordinates."""

_FLOAT32_MAX = float.fromhex("0x1.fffffep+127")


class _UnsetType:
    __slots__ = ()

    def __repr__(self) -> str:
        return "<omitted>"


_UNSET = _UnsetType()


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


@dataclass(frozen=True, slots=True, kw_only=True)
class Reverb:
    """Immutable standard EFX reverb parameters.

    Values use the OpenAL EFX standard-reverb ranges and defaults. Attach a
    reverb to a voice through [`EffectSend`][pyalsoft.EffectSend].

    Attributes:
        density: Modal density, from 0.0 through 1.0.
        diffusion: Echo density, from 0.0 through 1.0.
        gain: Overall linear wet-signal gain, from 0.0 through 1.0.
        high_frequency_gain: High-frequency wet gain, from 0.0 through 1.0.
        decay_time: Reverberation decay time in seconds, from 0.1 through 20.0.
        high_frequency_decay_ratio: High-frequency to low-frequency decay ratio,
            from 0.1 through 2.0.
        reflections_gain: Early-reflections gain, from 0.0 through 3.16.
        reflections_delay: Early-reflections delay in seconds, from 0.0 through 0.3.
        late_reverb_gain: Late-reverberation gain, from 0.0 through 10.0.
        late_reverb_delay: Late-reverberation delay in seconds, from 0.0 through 0.1.
        air_absorption_high_frequency_gain: Per-meter high-frequency air absorption
            gain, from 0.892 through 1.0.
        room_rolloff_factor: Distance-based room attenuation factor, from 0.0
            through 10.0.
        high_frequency_decay_limit: Whether air absorption limits high-frequency
            decay time.

    Raises:
        TypeError: A parameter has the wrong type.
        ValueError: A parameter is non-finite or outside its supported range.
    """

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
class _FilterConfig:
    """Shared fields and validation for managed EFX filters."""

    gain: float = 1.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "gain", _bounded_float("gain", self.gain, 0.0, 1.0))


@dataclass(frozen=True, slots=True, kw_only=True)
class LowPassFilter(_FilterConfig):
    """An EFX filter that attenuates the high-frequency signal.

    Attributes:
        gain: Overall linear gain, from 0.0 through 1.0.
        high_frequency_gain: Additional high-frequency gain, from 0.0 through 1.0.

    Raises:
        TypeError: A gain has the wrong type.
        ValueError: A gain is non-finite or outside its supported range.
    """

    high_frequency_gain: float = 1.0

    def __post_init__(self) -> None:
        _FilterConfig.__post_init__(self)
        object.__setattr__(
            self,
            "high_frequency_gain",
            _bounded_float("high_frequency_gain", self.high_frequency_gain, 0.0, 1.0),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class HighPassFilter(_FilterConfig):
    """An EFX filter that attenuates the low-frequency signal.

    Attributes:
        gain: Overall linear gain, from 0.0 through 1.0.
        low_frequency_gain: Additional low-frequency gain, from 0.0 through 1.0.

    Raises:
        TypeError: A gain has the wrong type.
        ValueError: A gain is non-finite or outside its supported range.
    """

    low_frequency_gain: float = 1.0

    def __post_init__(self) -> None:
        _FilterConfig.__post_init__(self)
        object.__setattr__(
            self,
            "low_frequency_gain",
            _bounded_float("low_frequency_gain", self.low_frequency_gain, 0.0, 1.0),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class BandPassFilter(_FilterConfig):
    """An EFX filter that attenuates low and high frequencies independently.

    Attributes:
        gain: Overall linear gain, from 0.0 through 1.0.
        low_frequency_gain: Additional low-frequency gain, from 0.0 through 1.0.
        high_frequency_gain: Additional high-frequency gain, from 0.0 through 1.0.

    Raises:
        TypeError: A gain has the wrong type.
        ValueError: A gain is non-finite or outside its supported range.
    """

    low_frequency_gain: float = 1.0
    high_frequency_gain: float = 1.0

    def __post_init__(self) -> None:
        _FilterConfig.__post_init__(self)
        object.__setattr__(
            self,
            "low_frequency_gain",
            _bounded_float("low_frequency_gain", self.low_frequency_gain, 0.0, 1.0),
        )
        object.__setattr__(
            self,
            "high_frequency_gain",
            _bounded_float("high_frequency_gain", self.high_frequency_gain, 0.0, 1.0),
        )


type Filter = LowPassFilter | HighPassFilter | BandPassFilter
"""A supported direct or auxiliary EFX filter configuration."""

_FILTER_TYPES = (LowPassFilter, HighPassFilter, BandPassFilter)


def _validate_filter(name: str, value: object) -> None:
    if value is not None and not isinstance(value, _FILTER_TYPES):
        raise TypeError(
            f"{name} must be a LowPassFilter, HighPassFilter, BandPassFilter, or None"
        )


_OMITTED_FILTER = cast(Filter | None, _UNSET)


@dataclass(frozen=True, slots=True, kw_only=True)
class EffectSend:
    """One auxiliary effect route, with an optional wet-signal filter.

    Tuple order in ``VoiceConfig.effect_sends`` determines the native
    auxiliary-send index. The device limits the number of simultaneous sends.

    Attributes:
        effect: Reverb applied to this route.
        filter: Optional low-pass, high-pass, or band-pass filter applied only
            to this route.

    Raises:
        TypeError: ``effect`` or ``filter`` is not a supported configuration.
    """

    effect: Reverb
    filter: Filter | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.effect, Reverb):
            raise TypeError("effect must be a Reverb")
        _validate_filter("filter", self.filter)


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
