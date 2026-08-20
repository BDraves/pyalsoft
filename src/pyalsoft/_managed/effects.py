"""Immutable managed EFX effect and filter descriptions."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import Field, dataclass, field, fields
from enum import Enum
from typing import Any, ClassVar, cast

from pyalsoft import bindings
from pyalsoft._managed._values import Vector3, _bounded_float, _vector3

_PARAMETER_METADATA = "pyalsoft_efx_parameter"


class _ParameterKind(Enum):
    FLOAT = "float"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    ENUM = "enum"
    VECTOR3 = "vector3"


@dataclass(frozen=True, slots=True)
class _ParameterSpec:
    parameter: int
    kind: _ParameterKind
    minimum: float | int | None = None
    maximum: float | int | None = None
    enum_values: Mapping[object, int] | None = None


def _parameter_field[T](
    default: T,
    parameter: int,
    kind: _ParameterKind,
    *,
    minimum: float | int | None = None,
    maximum: float | int | None = None,
    enum_values: Mapping[Any, int] | None = None,
) -> T:
    """Declare one dataclass field and its native EFX representation."""

    spec = _ParameterSpec(parameter, kind, minimum, maximum, enum_values)
    return field(default=default, metadata={_PARAMETER_METADATA: spec})


def _float_field(
    default: float, parameter: int, minimum: float, maximum: float
) -> float:
    return _parameter_field(
        default,
        parameter,
        _ParameterKind.FLOAT,
        minimum=minimum,
        maximum=maximum,
    )


def _integer_field(default: int, parameter: int, minimum: int, maximum: int) -> int:
    return _parameter_field(
        default,
        parameter,
        _ParameterKind.INTEGER,
        minimum=minimum,
        maximum=maximum,
    )


def _boolean_field(default: bool, parameter: int) -> bool:
    return _parameter_field(default, parameter, _ParameterKind.BOOLEAN)


def _enum_field[T](
    default: T,
    parameter: int,
    enum_values: Mapping[T, int],
) -> T:
    return _parameter_field(
        default,
        parameter,
        _ParameterKind.ENUM,
        enum_values=enum_values,
    )


def _vector3_field(default: Vector3, parameter: int) -> Vector3:
    return _parameter_field(default, parameter, _ParameterKind.VECTOR3)


def _parameter_spec(value: Field[object]) -> _ParameterSpec | None:
    return cast(_ParameterSpec | None, value.metadata.get(_PARAMETER_METADATA))


def _validate_parameter(name: str, value: object, spec: _ParameterSpec) -> object:
    if spec.kind is _ParameterKind.FLOAT:
        assert isinstance(spec.minimum, (int, float))
        assert isinstance(spec.maximum, (int, float))
        return _bounded_float(name, cast(float, value), spec.minimum, spec.maximum)
    if spec.kind is _ParameterKind.INTEGER:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{name} must be an integer")
        assert isinstance(spec.minimum, int)
        assert isinstance(spec.maximum, int)
        if not spec.minimum <= value <= spec.maximum:
            raise ValueError(
                f"{name} must be between {spec.minimum:g} and {spec.maximum:g}"
            )
        return value
    if spec.kind is _ParameterKind.BOOLEAN:
        if not isinstance(value, bool):
            raise TypeError(f"{name} must be a boolean")
        return value
    if spec.kind is _ParameterKind.ENUM:
        assert spec.enum_values is not None
        enum_type = type(next(iter(spec.enum_values)))
        if not isinstance(value, enum_type):
            raise TypeError(f"{name} must be a {enum_type.__name__}")
        return value
    if spec.kind is _ParameterKind.VECTOR3:
        return _vector3(name, cast(Vector3, value))
    raise AssertionError(f"unknown EFX parameter kind: {spec.kind}")


class _NativeConfig:
    _native_type: ClassVar[int]

    def __post_init__(self) -> None:
        for value_field in fields(cast(Any, self)):
            spec = _parameter_spec(value_field)
            if spec is not None:
                object.__setattr__(
                    self,
                    value_field.name,
                    _validate_parameter(
                        value_field.name,
                        getattr(self, value_field.name),
                        spec,
                    ),
                )


class _EffectConfig(_NativeConfig):
    """Marker base for supported managed effects."""


def _iter_native_parameters(
    config: _NativeConfig,
) -> Iterator[tuple[_ParameterSpec, object]]:
    for value_field in fields(cast(Any, config)):
        spec = _parameter_spec(value_field)
        if spec is not None:
            yield spec, getattr(config, value_field.name)


class ModulationWaveform(Enum):
    """Waveform used by chorus and flanger modulation."""

    SINUSOID = "sinusoid"
    TRIANGLE = "triangle"


class FrequencyShiftDirection(Enum):
    """Direction applied to a frequency-shifter channel."""

    DOWN = "down"
    UP = "up"
    OFF = "off"


class VocalMorpherPhoneme(Enum):
    """Phoneme selected for one side of a vocal-morpher transition."""

    A = "a"
    E = "e"
    I = "i"  # noqa: E741
    O = "o"  # noqa: E741
    U = "u"
    AA = "aa"
    AE = "ae"
    AH = "ah"
    AO = "ao"
    EH = "eh"
    ER = "er"
    IH = "ih"
    IY = "iy"
    UH = "uh"
    UW = "uw"
    B = "b"
    D = "d"
    F = "f"
    G = "g"
    J = "j"
    K = "k"
    L = "l"
    M = "m"
    N = "n"
    P = "p"
    R = "r"
    S = "s"
    T = "t"
    V = "v"
    Z = "z"


class VocalMorpherWaveform(Enum):
    """Waveform used to blend vocal-morpher phonemes."""

    SINUSOID = "sinusoid"
    TRIANGLE = "triangle"
    SAWTOOTH = "sawtooth"


class RingModulatorWaveform(Enum):
    """Carrier waveform used by a ring modulator."""

    SINUSOID = "sinusoid"
    SAWTOOTH = "sawtooth"
    SQUARE = "square"


_MODULATION_WAVEFORMS = {
    ModulationWaveform.SINUSOID: bindings.AL_CHORUS_WAVEFORM_SINUSOID,
    ModulationWaveform.TRIANGLE: bindings.AL_CHORUS_WAVEFORM_TRIANGLE,
}
_FREQUENCY_SHIFT_DIRECTIONS = {
    FrequencyShiftDirection.DOWN: bindings.AL_FREQUENCY_SHIFTER_DIRECTION_DOWN,
    FrequencyShiftDirection.UP: bindings.AL_FREQUENCY_SHIFTER_DIRECTION_UP,
    FrequencyShiftDirection.OFF: bindings.AL_FREQUENCY_SHIFTER_DIRECTION_OFF,
}
_VOCAL_MORPHER_PHONEMES = {
    phoneme: getattr(bindings, f"AL_VOCAL_MORPHER_PHONEME_{phoneme.name}")
    for phoneme in VocalMorpherPhoneme
}
_VOCAL_MORPHER_WAVEFORMS = {
    VocalMorpherWaveform.SINUSOID: bindings.AL_VOCAL_MORPHER_WAVEFORM_SINUSOID,
    VocalMorpherWaveform.TRIANGLE: bindings.AL_VOCAL_MORPHER_WAVEFORM_TRIANGLE,
    VocalMorpherWaveform.SAWTOOTH: bindings.AL_VOCAL_MORPHER_WAVEFORM_SAWTOOTH,
}
_RING_MODULATOR_WAVEFORMS = {
    RingModulatorWaveform.SINUSOID: bindings.AL_RING_MODULATOR_SINUSOID,
    RingModulatorWaveform.SAWTOOTH: bindings.AL_RING_MODULATOR_SAWTOOTH,
    RingModulatorWaveform.SQUARE: bindings.AL_RING_MODULATOR_SQUARE,
}


@dataclass(frozen=True, slots=True, kw_only=True)
class Reverb(_EffectConfig):
    """Standard EFX reverb with OpenAL's ranges and defaults.

    Attach the immutable value through [`EffectSend`][pyalsoft.EffectSend].
    Gain values are linear. Times are measured in seconds.

    Attributes:
        density: Modal density, from 0.0 through 1.0.
        diffusion: Echo density, from 0.0 through 1.0.
        gain: Overall wet-signal gain, from 0.0 through 1.0.
        high_frequency_gain: High-frequency wet gain, from 0.0 through 1.0.
        decay_time: Decay time, from 0.1 through 20.0 seconds.
        high_frequency_decay_ratio: High-to-low-frequency decay ratio, from
            0.1 through 2.0.
        reflections_gain: Early-reflections gain, from 0.0 through 3.16.
        reflections_delay: Early-reflections delay, from 0.0 through 0.3 seconds.
        late_reverb_gain: Late-reverberation gain, from 0.0 through 10.0.
        late_reverb_delay: Late-reverberation delay, from 0.0 through 0.1 seconds.
        air_absorption_high_frequency_gain: Per-meter high-frequency air
            absorption gain, from 0.892 through 1.0.
        room_rolloff_factor: Distance-based room attenuation, from 0.0 through 10.0.
        high_frequency_decay_limit: Whether air absorption limits high-frequency
            decay time.
    """

    _native_type = bindings.AL_EFFECT_REVERB
    density: float = _float_field(1.0, bindings.AL_REVERB_DENSITY, 0.0, 1.0)
    diffusion: float = _float_field(1.0, bindings.AL_REVERB_DIFFUSION, 0.0, 1.0)
    gain: float = _float_field(0.32, bindings.AL_REVERB_GAIN, 0.0, 1.0)
    high_frequency_gain: float = _float_field(0.89, bindings.AL_REVERB_GAINHF, 0.0, 1.0)
    decay_time: float = _float_field(1.49, bindings.AL_REVERB_DECAY_TIME, 0.1, 20.0)
    high_frequency_decay_ratio: float = _float_field(
        0.83, bindings.AL_REVERB_DECAY_HFRATIO, 0.1, 2.0
    )
    reflections_gain: float = _float_field(
        0.05, bindings.AL_REVERB_REFLECTIONS_GAIN, 0.0, 3.16
    )
    reflections_delay: float = _float_field(
        0.007, bindings.AL_REVERB_REFLECTIONS_DELAY, 0.0, 0.3
    )
    late_reverb_gain: float = _float_field(
        1.26, bindings.AL_REVERB_LATE_REVERB_GAIN, 0.0, 10.0
    )
    late_reverb_delay: float = _float_field(
        0.011, bindings.AL_REVERB_LATE_REVERB_DELAY, 0.0, 0.1
    )
    air_absorption_high_frequency_gain: float = _float_field(
        0.994, bindings.AL_REVERB_AIR_ABSORPTION_GAINHF, 0.892, 1.0
    )
    room_rolloff_factor: float = _float_field(
        0.0, bindings.AL_REVERB_ROOM_ROLLOFF_FACTOR, 0.0, 10.0
    )
    high_frequency_decay_limit: bool = _boolean_field(
        True, bindings.AL_REVERB_DECAY_HFLIMIT
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class EAXReverb(_EffectConfig):
    """Extended EAX reverb, including pan, echo, and modulation controls.

    Attributes:
        density: Modal density, from 0.0 through 1.0.
        diffusion: Echo density, from 0.0 through 1.0.
        gain: Overall wet-signal gain, from 0.0 through 1.0.
        high_frequency_gain: High-frequency wet gain, from 0.0 through 1.0.
        low_frequency_gain: Low-frequency wet gain, from 0.0 through 1.0.
        decay_time: Decay time, from 0.1 through 20.0 seconds.
        high_frequency_decay_ratio: High-frequency decay ratio, from 0.1 to 2.0.
        low_frequency_decay_ratio: Low-frequency decay ratio, from 0.1 to 2.0.
        reflections_gain: Early-reflections gain, from 0.0 through 3.16.
        reflections_delay: Early-reflections delay, from 0.0 through 0.3 seconds.
        reflections_pan: Early-reflections pan vector.
        late_reverb_gain: Late-reverberation gain, from 0.0 through 10.0.
        late_reverb_delay: Late-reverberation delay, from 0.0 through 0.1 seconds.
        late_reverb_pan: Late-reverberation pan vector.
        echo_time: Echo repetition time, from 0.075 through 0.25 seconds.
        echo_depth: Echo depth, from 0.0 through 1.0.
        modulation_time: Modulation period, from 0.04 through 4.0 seconds.
        modulation_depth: Modulation depth, from 0.0 through 1.0.
        air_absorption_high_frequency_gain: Per-meter high-frequency air
            absorption gain, from 0.892 through 1.0.
        high_frequency_reference: High-frequency reference, from 1000 through
            20000 Hz.
        low_frequency_reference: Low-frequency reference, from 20 through 1000 Hz.
        room_rolloff_factor: Distance-based room attenuation, from 0.0 through 10.0.
        high_frequency_decay_limit: Whether air absorption limits high-frequency
            decay time.
    """

    _native_type = bindings.AL_EFFECT_EAXREVERB
    density: float = _float_field(1.0, bindings.AL_EAXREVERB_DENSITY, 0.0, 1.0)
    diffusion: float = _float_field(1.0, bindings.AL_EAXREVERB_DIFFUSION, 0.0, 1.0)
    gain: float = _float_field(0.32, bindings.AL_EAXREVERB_GAIN, 0.0, 1.0)
    high_frequency_gain: float = _float_field(
        0.89, bindings.AL_EAXREVERB_GAINHF, 0.0, 1.0
    )
    low_frequency_gain: float = _float_field(
        1.0, bindings.AL_EAXREVERB_GAINLF, 0.0, 1.0
    )
    decay_time: float = _float_field(1.49, bindings.AL_EAXREVERB_DECAY_TIME, 0.1, 20.0)
    high_frequency_decay_ratio: float = _float_field(
        0.83, bindings.AL_EAXREVERB_DECAY_HFRATIO, 0.1, 2.0
    )
    low_frequency_decay_ratio: float = _float_field(
        1.0, bindings.AL_EAXREVERB_DECAY_LFRATIO, 0.1, 2.0
    )
    reflections_gain: float = _float_field(
        0.05, bindings.AL_EAXREVERB_REFLECTIONS_GAIN, 0.0, 3.16
    )
    reflections_delay: float = _float_field(
        0.007, bindings.AL_EAXREVERB_REFLECTIONS_DELAY, 0.0, 0.3
    )
    reflections_pan: Vector3 = _vector3_field(
        (0.0, 0.0, 0.0), bindings.AL_EAXREVERB_REFLECTIONS_PAN
    )
    late_reverb_gain: float = _float_field(
        1.26, bindings.AL_EAXREVERB_LATE_REVERB_GAIN, 0.0, 10.0
    )
    late_reverb_delay: float = _float_field(
        0.011, bindings.AL_EAXREVERB_LATE_REVERB_DELAY, 0.0, 0.1
    )
    late_reverb_pan: Vector3 = _vector3_field(
        (0.0, 0.0, 0.0), bindings.AL_EAXREVERB_LATE_REVERB_PAN
    )
    echo_time: float = _float_field(0.25, bindings.AL_EAXREVERB_ECHO_TIME, 0.075, 0.25)
    echo_depth: float = _float_field(0.0, bindings.AL_EAXREVERB_ECHO_DEPTH, 0.0, 1.0)
    modulation_time: float = _float_field(
        0.25, bindings.AL_EAXREVERB_MODULATION_TIME, 0.04, 4.0
    )
    modulation_depth: float = _float_field(
        0.0, bindings.AL_EAXREVERB_MODULATION_DEPTH, 0.0, 1.0
    )
    air_absorption_high_frequency_gain: float = _float_field(
        0.994, bindings.AL_EAXREVERB_AIR_ABSORPTION_GAINHF, 0.892, 1.0
    )
    high_frequency_reference: float = _float_field(
        5000.0, bindings.AL_EAXREVERB_HFREFERENCE, 1000.0, 20000.0
    )
    low_frequency_reference: float = _float_field(
        250.0, bindings.AL_EAXREVERB_LFREFERENCE, 20.0, 1000.0
    )
    room_rolloff_factor: float = _float_field(
        0.0, bindings.AL_EAXREVERB_ROOM_ROLLOFF_FACTOR, 0.0, 10.0
    )
    high_frequency_decay_limit: bool = _boolean_field(
        True, bindings.AL_EAXREVERB_DECAY_HFLIMIT
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class Chorus(_EffectConfig):
    """Chorus modulation effect.

    Attributes:
        waveform: Sinusoid or triangle modulation.
        phase: Stereo phase difference, from -180 through 180 degrees.
        rate: Modulation frequency, from 0.0 through 10.0 Hz.
        depth: Modulation depth, from 0.0 through 1.0.
        feedback: Feedback amount, from -1.0 through 1.0.
        delay: Average delay, from 0.0 through 0.016 seconds.
    """

    _native_type = bindings.AL_EFFECT_CHORUS
    waveform: ModulationWaveform = _enum_field(
        ModulationWaveform.TRIANGLE, bindings.AL_CHORUS_WAVEFORM, _MODULATION_WAVEFORMS
    )
    phase: int = _integer_field(90, bindings.AL_CHORUS_PHASE, -180, 180)
    rate: float = _float_field(1.1, bindings.AL_CHORUS_RATE, 0.0, 10.0)
    depth: float = _float_field(0.1, bindings.AL_CHORUS_DEPTH, 0.0, 1.0)
    feedback: float = _float_field(0.25, bindings.AL_CHORUS_FEEDBACK, -1.0, 1.0)
    delay: float = _float_field(0.016, bindings.AL_CHORUS_DELAY, 0.0, 0.016)


@dataclass(frozen=True, slots=True, kw_only=True)
class Distortion(_EffectConfig):
    """Distortion with pre- and post-equalization controls.

    Attributes:
        edge: Distortion edge, from 0.0 through 1.0.
        gain: Output gain, from 0.01 through 1.0.
        low_pass_cutoff: Low-pass cutoff, from 80 through 24000 Hz.
        equalizer_center: Equalizer center, from 80 through 24000 Hz.
        equalizer_bandwidth: Equalizer bandwidth, from 80 through 24000 Hz.
    """

    _native_type = bindings.AL_EFFECT_DISTORTION
    edge: float = _float_field(0.2, bindings.AL_DISTORTION_EDGE, 0.0, 1.0)
    gain: float = _float_field(0.05, bindings.AL_DISTORTION_GAIN, 0.01, 1.0)
    low_pass_cutoff: float = _float_field(
        8000.0, bindings.AL_DISTORTION_LOWPASS_CUTOFF, 80.0, 24000.0
    )
    equalizer_center: float = _float_field(
        3600.0, bindings.AL_DISTORTION_EQCENTER, 80.0, 24000.0
    )
    equalizer_bandwidth: float = _float_field(
        3600.0, bindings.AL_DISTORTION_EQBANDWIDTH, 80.0, 24000.0
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class Echo(_EffectConfig):
    """Echo with stereo offset, damping, feedback, and spread controls.

    Attributes:
        delay: Primary tap delay, from 0.0 through 0.207 seconds.
        left_right_delay: Left/right tap delay, from 0.0 through 0.404 seconds.
        damping: High-frequency damping, from 0.0 through 0.99.
        feedback: Feedback amount, from 0.0 through 1.0.
        spread: Stereo spread, from -1.0 through 1.0.
    """

    _native_type = bindings.AL_EFFECT_ECHO
    delay: float = _float_field(0.1, bindings.AL_ECHO_DELAY, 0.0, 0.207)
    left_right_delay: float = _float_field(0.1, bindings.AL_ECHO_LRDELAY, 0.0, 0.404)
    damping: float = _float_field(0.5, bindings.AL_ECHO_DAMPING, 0.0, 0.99)
    feedback: float = _float_field(0.5, bindings.AL_ECHO_FEEDBACK, 0.0, 1.0)
    spread: float = _float_field(-1.0, bindings.AL_ECHO_SPREAD, -1.0, 1.0)


@dataclass(frozen=True, slots=True, kw_only=True)
class Flanger(_EffectConfig):
    """Flanger modulation effect.

    Attributes:
        waveform: Sinusoid or triangle modulation.
        phase: Stereo phase difference, from -180 through 180 degrees.
        rate: Modulation frequency, from 0.0 through 10.0 Hz.
        depth: Modulation depth, from 0.0 through 1.0.
        feedback: Feedback amount, from -1.0 through 1.0.
        delay: Average delay, from 0.0 through 0.004 seconds.
    """

    _native_type = bindings.AL_EFFECT_FLANGER
    waveform: ModulationWaveform = _enum_field(
        ModulationWaveform.TRIANGLE,
        bindings.AL_FLANGER_WAVEFORM,
        _MODULATION_WAVEFORMS,
    )
    phase: int = _integer_field(0, bindings.AL_FLANGER_PHASE, -180, 180)
    rate: float = _float_field(0.27, bindings.AL_FLANGER_RATE, 0.0, 10.0)
    depth: float = _float_field(1.0, bindings.AL_FLANGER_DEPTH, 0.0, 1.0)
    feedback: float = _float_field(-0.5, bindings.AL_FLANGER_FEEDBACK, -1.0, 1.0)
    delay: float = _float_field(0.002, bindings.AL_FLANGER_DELAY, 0.0, 0.004)


@dataclass(frozen=True, slots=True, kw_only=True)
class FrequencyShifter(_EffectConfig):
    """Independent left- and right-channel frequency shifting.

    Attributes:
        frequency: Shift frequency, from 0 through 24000 Hz.
        left_direction: Down, up, or disabled for the left channel.
        right_direction: Down, up, or disabled for the right channel.
    """

    _native_type = bindings.AL_EFFECT_FREQUENCY_SHIFTER
    frequency: float = _float_field(
        0.0, bindings.AL_FREQUENCY_SHIFTER_FREQUENCY, 0.0, 24000.0
    )
    left_direction: FrequencyShiftDirection = _enum_field(
        FrequencyShiftDirection.DOWN,
        bindings.AL_FREQUENCY_SHIFTER_LEFT_DIRECTION,
        _FREQUENCY_SHIFT_DIRECTIONS,
    )
    right_direction: FrequencyShiftDirection = _enum_field(
        FrequencyShiftDirection.DOWN,
        bindings.AL_FREQUENCY_SHIFTER_RIGHT_DIRECTION,
        _FREQUENCY_SHIFT_DIRECTIONS,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class VocalMorpher(_EffectConfig):
    """Blend between two selected phonemes.

    Attributes:
        phoneme_a: First phoneme.
        phoneme_a_coarse_tuning: First tuning, from -24 through 24 semitones.
        phoneme_b: Second phoneme.
        phoneme_b_coarse_tuning: Second tuning, from -24 through 24 semitones.
        waveform: Waveform used to blend the phonemes.
        rate: Morphing frequency, from 0.0 through 10.0 Hz.
    """

    _native_type = bindings.AL_EFFECT_VOCAL_MORPHER
    phoneme_a: VocalMorpherPhoneme = _enum_field(
        VocalMorpherPhoneme.A,
        bindings.AL_VOCAL_MORPHER_PHONEMEA,
        _VOCAL_MORPHER_PHONEMES,
    )
    phoneme_a_coarse_tuning: int = _integer_field(
        0, bindings.AL_VOCAL_MORPHER_PHONEMEA_COARSE_TUNING, -24, 24
    )
    phoneme_b: VocalMorpherPhoneme = _enum_field(
        VocalMorpherPhoneme.ER,
        bindings.AL_VOCAL_MORPHER_PHONEMEB,
        _VOCAL_MORPHER_PHONEMES,
    )
    phoneme_b_coarse_tuning: int = _integer_field(
        0, bindings.AL_VOCAL_MORPHER_PHONEMEB_COARSE_TUNING, -24, 24
    )
    waveform: VocalMorpherWaveform = _enum_field(
        VocalMorpherWaveform.SINUSOID,
        bindings.AL_VOCAL_MORPHER_WAVEFORM,
        _VOCAL_MORPHER_WAVEFORMS,
    )
    rate: float = _float_field(1.41, bindings.AL_VOCAL_MORPHER_RATE, 0.0, 10.0)


@dataclass(frozen=True, slots=True, kw_only=True)
class PitchShifter(_EffectConfig):
    """Pitch shift in semitones and cents.

    Attributes:
        coarse_tuning: Shift from -12 through 12 semitones.
        fine_tuning: Additional shift from -50 through 50 cents.
    """

    _native_type = bindings.AL_EFFECT_PITCH_SHIFTER
    coarse_tuning: int = _integer_field(
        12, bindings.AL_PITCH_SHIFTER_COARSE_TUNE, -12, 12
    )
    fine_tuning: int = _integer_field(0, bindings.AL_PITCH_SHIFTER_FINE_TUNE, -50, 50)


@dataclass(frozen=True, slots=True, kw_only=True)
class RingModulator(_EffectConfig):
    """Ring modulation with a selectable carrier waveform.

    Attributes:
        frequency: Carrier frequency, from 0 through 8000 Hz.
        high_pass_cutoff: High-pass cutoff, from 0 through 24000 Hz.
        waveform: Sinusoid, sawtooth, or square carrier waveform.
    """

    _native_type = bindings.AL_EFFECT_RING_MODULATOR
    frequency: float = _float_field(
        440.0, bindings.AL_RING_MODULATOR_FREQUENCY, 0.0, 8000.0
    )
    high_pass_cutoff: float = _float_field(
        800.0, bindings.AL_RING_MODULATOR_HIGHPASS_CUTOFF, 0.0, 24000.0
    )
    waveform: RingModulatorWaveform = _enum_field(
        RingModulatorWaveform.SINUSOID,
        bindings.AL_RING_MODULATOR_WAVEFORM,
        _RING_MODULATOR_WAVEFORMS,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class AutoWah(_EffectConfig):
    """Envelope-controlled wah effect.

    Attributes:
        attack_time: Envelope attack, from 0.0001 through 1.0 seconds.
        release_time: Envelope release, from 0.0001 through 1.0 seconds.
        resonance: Filter resonance, from 2.0 through 1000.0.
        peak_gain: Peak filter gain, from 0.00003 through 31621.0.
    """

    _native_type = bindings.AL_EFFECT_AUTOWAH
    attack_time: float = _float_field(
        0.06, bindings.AL_AUTOWAH_ATTACK_TIME, 0.0001, 1.0
    )
    release_time: float = _float_field(
        0.06, bindings.AL_AUTOWAH_RELEASE_TIME, 0.0001, 1.0
    )
    resonance: float = _float_field(1000.0, bindings.AL_AUTOWAH_RESONANCE, 2.0, 1000.0)
    peak_gain: float = _float_field(
        11.22, bindings.AL_AUTOWAH_PEAK_GAIN, 0.00003, 31621.0
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class Compressor(_EffectConfig):
    """Automatic gain compressor with an enable switch.

    Attributes:
        enabled: Whether compression is enabled.
    """

    _native_type = bindings.AL_EFFECT_COMPRESSOR
    enabled: bool = _boolean_field(True, bindings.AL_COMPRESSOR_ONOFF)


@dataclass(frozen=True, slots=True, kw_only=True)
class Equalizer(_EffectConfig):
    """Four-band equalizer.

    Attributes:
        low_gain: Low-band gain, from 0.126 through 7.943.
        low_cutoff: Low-band cutoff, from 50 through 800 Hz.
        low_mid_gain: Low-mid-band gain, from 0.126 through 7.943.
        low_mid_center: Low-mid center, from 200 through 3000 Hz.
        low_mid_width: Low-mid relative width, from 0.01 through 1.0.
        high_mid_gain: High-mid-band gain, from 0.126 through 7.943.
        high_mid_center: High-mid center, from 1000 through 8000 Hz.
        high_mid_width: High-mid relative width, from 0.01 through 1.0.
        high_gain: High-band gain, from 0.126 through 7.943.
        high_cutoff: High-band cutoff, from 4000 through 16000 Hz.
    """

    _native_type = bindings.AL_EFFECT_EQUALIZER
    low_gain: float = _float_field(1.0, bindings.AL_EQUALIZER_LOW_GAIN, 0.126, 7.943)
    low_cutoff: float = _float_field(
        200.0, bindings.AL_EQUALIZER_LOW_CUTOFF, 50.0, 800.0
    )
    low_mid_gain: float = _float_field(
        1.0, bindings.AL_EQUALIZER_MID1_GAIN, 0.126, 7.943
    )
    low_mid_center: float = _float_field(
        500.0, bindings.AL_EQUALIZER_MID1_CENTER, 200.0, 3000.0
    )
    low_mid_width: float = _float_field(
        1.0, bindings.AL_EQUALIZER_MID1_WIDTH, 0.01, 1.0
    )
    high_mid_gain: float = _float_field(
        1.0, bindings.AL_EQUALIZER_MID2_GAIN, 0.126, 7.943
    )
    high_mid_center: float = _float_field(
        3000.0, bindings.AL_EQUALIZER_MID2_CENTER, 1000.0, 8000.0
    )
    high_mid_width: float = _float_field(
        1.0, bindings.AL_EQUALIZER_MID2_WIDTH, 0.01, 1.0
    )
    high_gain: float = _float_field(1.0, bindings.AL_EQUALIZER_HIGH_GAIN, 0.126, 7.943)
    high_cutoff: float = _float_field(
        6000.0, bindings.AL_EQUALIZER_HIGH_CUTOFF, 4000.0, 16000.0
    )


type Effect = (
    Reverb
    | EAXReverb
    | Chorus
    | Distortion
    | Echo
    | Flanger
    | FrequencyShifter
    | VocalMorpher
    | PitchShifter
    | RingModulator
    | AutoWah
    | Compressor
    | Equalizer
)
"""A supported auxiliary EFX effect configuration."""


@dataclass(frozen=True, slots=True, kw_only=True)
class _FilterConfig(_NativeConfig):
    """Shared fields and validation for managed EFX filters."""

    gain: float = _float_field(1.0, bindings.AL_LOWPASS_GAIN, 0.0, 1.0)


@dataclass(frozen=True, slots=True, kw_only=True)
class LowPassFilter(_FilterConfig):
    """An EFX filter that attenuates the high-frequency signal.

    ``gain`` and ``high_frequency_gain`` range from 0.0 through 1.0.
    """

    _native_type = bindings.AL_FILTER_LOWPASS
    high_frequency_gain: float = _float_field(1.0, bindings.AL_LOWPASS_GAINHF, 0.0, 1.0)


@dataclass(frozen=True, slots=True, kw_only=True)
class HighPassFilter(_FilterConfig):
    """An EFX filter that attenuates the low-frequency signal.

    ``gain`` and ``low_frequency_gain`` range from 0.0 through 1.0.
    """

    _native_type = bindings.AL_FILTER_HIGHPASS
    gain: float = _float_field(1.0, bindings.AL_HIGHPASS_GAIN, 0.0, 1.0)
    low_frequency_gain: float = _float_field(1.0, bindings.AL_HIGHPASS_GAINLF, 0.0, 1.0)


@dataclass(frozen=True, slots=True, kw_only=True)
class BandPassFilter(_FilterConfig):
    """An EFX filter that attenuates low and high frequencies independently.

    All three gain controls range from 0.0 through 1.0.
    """

    _native_type = bindings.AL_FILTER_BANDPASS
    gain: float = _float_field(1.0, bindings.AL_BANDPASS_GAIN, 0.0, 1.0)
    low_frequency_gain: float = _float_field(1.0, bindings.AL_BANDPASS_GAINLF, 0.0, 1.0)
    high_frequency_gain: float = _float_field(
        1.0, bindings.AL_BANDPASS_GAINHF, 0.0, 1.0
    )


type Filter = LowPassFilter | HighPassFilter | BandPassFilter
"""A supported direct or auxiliary EFX filter configuration."""


class _UnsetType:
    __slots__ = ()

    def __repr__(self) -> str:
        return "<omitted>"


_OMITTED_VALUE = _UnsetType()
_OMITTED_FILTER = cast(Filter | None, _OMITTED_VALUE)


def _validate_filter(name: str, value: object) -> None:
    if value is not None and not isinstance(value, _FilterConfig):
        raise TypeError(
            f"{name} must be a LowPassFilter, HighPassFilter, BandPassFilter, or None"
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class EffectSend:
    """One auxiliary effect route, with an optional wet-signal filter.

    Tuple order in ``VoiceConfig.effect_sends`` determines the native
    auxiliary-send index. The playback device limits the number of simultaneous
    sends.

    Attributes:
        effect: Effect applied to this route.
        filter: Optional filter applied only to this route's wet signal.
    """

    effect: Effect
    filter: Filter | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.effect, _EffectConfig):
            raise TypeError("effect must be an Effect")
        _validate_filter("filter", self.filter)
