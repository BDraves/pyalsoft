"""Tests for explicit managed playback sessions."""

from __future__ import annotations

import pytest

from pyalsoft import (
    PCM,
    AudioBackendError,
    AutoWah,
    BandPassFilter,
    Chorus,
    Compressor,
    DedicatedDialogue,
    DedicatedLowFrequencyEffect,
    Distortion,
    EAXReverb,
    Echo,
    Effect,
    EffectBusConfig,
    EffectSend,
    Equalizer,
    Flanger,
    FrequencyShiftDirection,
    FrequencyShifter,
    HighPassFilter,
    InvalidHandleError,
    LowPassFilter,
    ModulationWaveform,
    PitchShifter,
    ResourceInUseError,
    Reverb,
    RingModulator,
    RingModulatorWaveform,
    VocalMorpher,
    VocalMorpherPhoneme,
    VocalMorpherWaveform,
    VoiceConfig,
    bindings,
    create_effect_bus,
    get_effect_bus_config,
    open_playback,
    play,
    release,
    set_effect_bus_config,
    set_voice_config,
    upload,
)
from tests._support.managed_backend import FakeLibrary, as_library


@pytest.mark.parametrize(
    ("effect", "effect_type", "properties"),
    [
        (
            Reverb(),
            bindings.AL_EFFECT_REVERB,
            {
                bindings.AL_REVERB_DENSITY: 1.0,
                bindings.AL_REVERB_DIFFUSION: 1.0,
                bindings.AL_REVERB_GAIN: 0.32,
                bindings.AL_REVERB_GAINHF: 0.89,
                bindings.AL_REVERB_DECAY_TIME: 1.49,
                bindings.AL_REVERB_DECAY_HFRATIO: 0.83,
                bindings.AL_REVERB_REFLECTIONS_GAIN: 0.05,
                bindings.AL_REVERB_REFLECTIONS_DELAY: 0.007,
                bindings.AL_REVERB_LATE_REVERB_GAIN: 1.26,
                bindings.AL_REVERB_LATE_REVERB_DELAY: 0.011,
                bindings.AL_REVERB_AIR_ABSORPTION_GAINHF: 0.994,
                bindings.AL_REVERB_ROOM_ROLLOFF_FACTOR: 0.0,
                bindings.AL_REVERB_DECAY_HFLIMIT: 1,
            },
        ),
        (
            EAXReverb(
                reflections_pan=(1, 2, 3),
                late_reverb_pan=(-1, -2, -3),
                high_frequency_decay_limit=False,
            ),
            bindings.AL_EFFECT_EAXREVERB,
            {
                bindings.AL_EAXREVERB_DENSITY: 1.0,
                bindings.AL_EAXREVERB_DIFFUSION: 1.0,
                bindings.AL_EAXREVERB_GAIN: 0.32,
                bindings.AL_EAXREVERB_GAINHF: 0.89,
                bindings.AL_EAXREVERB_GAINLF: 1.0,
                bindings.AL_EAXREVERB_DECAY_TIME: 1.49,
                bindings.AL_EAXREVERB_DECAY_HFRATIO: 0.83,
                bindings.AL_EAXREVERB_DECAY_LFRATIO: 1.0,
                bindings.AL_EAXREVERB_REFLECTIONS_GAIN: 0.05,
                bindings.AL_EAXREVERB_REFLECTIONS_DELAY: 0.007,
                bindings.AL_EAXREVERB_REFLECTIONS_PAN: (1.0, 2.0, 3.0),
                bindings.AL_EAXREVERB_LATE_REVERB_GAIN: 1.26,
                bindings.AL_EAXREVERB_LATE_REVERB_DELAY: 0.011,
                bindings.AL_EAXREVERB_LATE_REVERB_PAN: (-1.0, -2.0, -3.0),
                bindings.AL_EAXREVERB_ECHO_TIME: 0.25,
                bindings.AL_EAXREVERB_ECHO_DEPTH: 0.0,
                bindings.AL_EAXREVERB_MODULATION_TIME: 0.25,
                bindings.AL_EAXREVERB_MODULATION_DEPTH: 0.0,
                bindings.AL_EAXREVERB_AIR_ABSORPTION_GAINHF: 0.994,
                bindings.AL_EAXREVERB_HFREFERENCE: 5000.0,
                bindings.AL_EAXREVERB_LFREFERENCE: 250.0,
                bindings.AL_EAXREVERB_ROOM_ROLLOFF_FACTOR: 0.0,
                bindings.AL_EAXREVERB_DECAY_HFLIMIT: 0,
            },
        ),
        (
            Chorus(waveform=ModulationWaveform.SINUSOID),
            bindings.AL_EFFECT_CHORUS,
            {
                bindings.AL_CHORUS_WAVEFORM: bindings.AL_CHORUS_WAVEFORM_SINUSOID,
                bindings.AL_CHORUS_PHASE: 90,
                bindings.AL_CHORUS_RATE: 1.1,
                bindings.AL_CHORUS_DEPTH: 0.1,
                bindings.AL_CHORUS_FEEDBACK: 0.25,
                bindings.AL_CHORUS_DELAY: 0.016,
            },
        ),
        (
            Distortion(),
            bindings.AL_EFFECT_DISTORTION,
            {
                bindings.AL_DISTORTION_EDGE: 0.2,
                bindings.AL_DISTORTION_GAIN: 0.05,
                bindings.AL_DISTORTION_LOWPASS_CUTOFF: 8000.0,
                bindings.AL_DISTORTION_EQCENTER: 3600.0,
                bindings.AL_DISTORTION_EQBANDWIDTH: 3600.0,
            },
        ),
        (
            Echo(),
            bindings.AL_EFFECT_ECHO,
            {
                bindings.AL_ECHO_DELAY: 0.1,
                bindings.AL_ECHO_LRDELAY: 0.1,
                bindings.AL_ECHO_DAMPING: 0.5,
                bindings.AL_ECHO_FEEDBACK: 0.5,
                bindings.AL_ECHO_SPREAD: -1.0,
            },
        ),
        (
            Flanger(waveform=ModulationWaveform.SINUSOID),
            bindings.AL_EFFECT_FLANGER,
            {
                bindings.AL_FLANGER_WAVEFORM: bindings.AL_FLANGER_WAVEFORM_SINUSOID,
                bindings.AL_FLANGER_PHASE: 0,
                bindings.AL_FLANGER_RATE: 0.27,
                bindings.AL_FLANGER_DEPTH: 1.0,
                bindings.AL_FLANGER_FEEDBACK: -0.5,
                bindings.AL_FLANGER_DELAY: 0.002,
            },
        ),
        (
            FrequencyShifter(
                left_direction=FrequencyShiftDirection.UP,
                right_direction=FrequencyShiftDirection.OFF,
            ),
            bindings.AL_EFFECT_FREQUENCY_SHIFTER,
            {
                bindings.AL_FREQUENCY_SHIFTER_FREQUENCY: 0.0,
                bindings.AL_FREQUENCY_SHIFTER_LEFT_DIRECTION: (
                    bindings.AL_FREQUENCY_SHIFTER_DIRECTION_UP
                ),
                bindings.AL_FREQUENCY_SHIFTER_RIGHT_DIRECTION: (
                    bindings.AL_FREQUENCY_SHIFTER_DIRECTION_OFF
                ),
            },
        ),
        (
            VocalMorpher(
                phoneme_a=VocalMorpherPhoneme.Z,
                phoneme_b=VocalMorpherPhoneme.AA,
                waveform=VocalMorpherWaveform.SAWTOOTH,
            ),
            bindings.AL_EFFECT_VOCAL_MORPHER,
            {
                bindings.AL_VOCAL_MORPHER_PHONEMEA: (
                    bindings.AL_VOCAL_MORPHER_PHONEME_Z
                ),
                bindings.AL_VOCAL_MORPHER_PHONEMEA_COARSE_TUNING: 0,
                bindings.AL_VOCAL_MORPHER_PHONEMEB: (
                    bindings.AL_VOCAL_MORPHER_PHONEME_AA
                ),
                bindings.AL_VOCAL_MORPHER_PHONEMEB_COARSE_TUNING: 0,
                bindings.AL_VOCAL_MORPHER_WAVEFORM: (
                    bindings.AL_VOCAL_MORPHER_WAVEFORM_SAWTOOTH
                ),
                bindings.AL_VOCAL_MORPHER_RATE: 1.41,
            },
        ),
        (
            PitchShifter(),
            bindings.AL_EFFECT_PITCH_SHIFTER,
            {
                bindings.AL_PITCH_SHIFTER_COARSE_TUNE: 12,
                bindings.AL_PITCH_SHIFTER_FINE_TUNE: 0,
            },
        ),
        (
            RingModulator(waveform=RingModulatorWaveform.SQUARE),
            bindings.AL_EFFECT_RING_MODULATOR,
            {
                bindings.AL_RING_MODULATOR_FREQUENCY: 440.0,
                bindings.AL_RING_MODULATOR_HIGHPASS_CUTOFF: 800.0,
                bindings.AL_RING_MODULATOR_WAVEFORM: (
                    bindings.AL_RING_MODULATOR_SQUARE
                ),
            },
        ),
        (
            AutoWah(),
            bindings.AL_EFFECT_AUTOWAH,
            {
                bindings.AL_AUTOWAH_ATTACK_TIME: 0.06,
                bindings.AL_AUTOWAH_RELEASE_TIME: 0.06,
                bindings.AL_AUTOWAH_RESONANCE: 1000.0,
                bindings.AL_AUTOWAH_PEAK_GAIN: 11.22,
            },
        ),
        (
            Compressor(enabled=False),
            bindings.AL_EFFECT_COMPRESSOR,
            {bindings.AL_COMPRESSOR_ONOFF: 0},
        ),
        (
            Equalizer(),
            bindings.AL_EFFECT_EQUALIZER,
            {
                bindings.AL_EQUALIZER_LOW_GAIN: 1.0,
                bindings.AL_EQUALIZER_LOW_CUTOFF: 200.0,
                bindings.AL_EQUALIZER_MID1_GAIN: 1.0,
                bindings.AL_EQUALIZER_MID1_CENTER: 500.0,
                bindings.AL_EQUALIZER_MID1_WIDTH: 1.0,
                bindings.AL_EQUALIZER_MID2_GAIN: 1.0,
                bindings.AL_EQUALIZER_MID2_CENTER: 3000.0,
                bindings.AL_EQUALIZER_MID2_WIDTH: 1.0,
                bindings.AL_EQUALIZER_HIGH_GAIN: 1.0,
                bindings.AL_EQUALIZER_HIGH_CUTOFF: 6000.0,
            },
        ),
        (
            DedicatedDialogue(gain=0.75),
            bindings.AL_EFFECT_DEDICATED_DIALOGUE,
            {bindings.AL_DEDICATED_GAIN: 0.75},
        ),
        (
            DedicatedLowFrequencyEffect(gain=0.5),
            bindings.AL_EFFECT_DEDICATED_LOW_FREQUENCY_EFFECT,
            {bindings.AL_DEDICATED_GAIN: 0.5},
        ),
    ],
    ids=lambda value: type(value).__name__ if not isinstance(value, int) else None,
)
def test_all_managed_effects_configure_every_native_parameter(
    effect: Effect,
    effect_type: int,
    properties: dict[int, object],
) -> None:
    library = FakeLibrary()
    with open_playback(library=as_library(library)) as playback:
        clip = upload(playback, PCM(b"\0\0" * 10, channels=1, sample_rate=10))
        voice = play(
            playback,
            clip,
            effect_sends=(EffectSend(effect=effect),),
        )

        assert library.al.effects[200] == {
            bindings.AL_EFFECT_TYPE: effect_type,
            **properties,
        }

        release(playback, voice)
        release(playback, clip)

    assert library.al.allocated_effects == set()
    assert library.al.allocated_effect_slots == set()


def test_voice_efx_are_created_replaced_and_released_with_the_voice() -> None:
    library = FakeLibrary()
    reverb = Reverb(gain=0.2, decay_time=0.6, high_frequency_decay_ratio=0.8)
    with open_playback(library=as_library(library)) as playback:
        clip = upload(playback, PCM(b"\0\0" * 10, channels=1, sample_rate=10))
        voice = play(
            playback,
            clip,
            filter=LowPassFilter(high_frequency_gain=0.1),
            effect_sends=(
                EffectSend(
                    effect=reverb,
                    filter=HighPassFilter(low_frequency_gain=0.25),
                ),
            ),
        )

        assert library.al.sources[100][bindings.AL_DIRECT_FILTER] == 300
        assert library.al.filters[300] == {
            bindings.AL_FILTER_TYPE: bindings.AL_FILTER_LOWPASS,
            bindings.AL_LOWPASS_GAIN: 1.0,
            bindings.AL_LOWPASS_GAINHF: 0.1,
        }
        assert library.al.effects[200][bindings.AL_EFFECT_TYPE] == (
            bindings.AL_EFFECT_REVERB
        )
        assert library.al.effects[200][bindings.AL_REVERB_GAIN] == 0.2
        assert library.al.effects[200][bindings.AL_REVERB_DECAY_TIME] == 0.6
        assert library.al.effect_slots[400] == {bindings.AL_EFFECTSLOT_EFFECT: 200}
        assert library.al.source_sends[(100, 0)] == (400, 301)
        assert library.al.filters[301][bindings.AL_FILTER_TYPE] == (
            bindings.AL_FILTER_HIGHPASS
        )

        set_voice_config(
            playback,
            voice,
            VoiceConfig(
                filter=BandPassFilter(
                    gain=0.75,
                    low_frequency_gain=0.1,
                    high_frequency_gain=0.2,
                )
            ),
        )

        assert library.al.sources[100][bindings.AL_DIRECT_FILTER] == 302
        assert library.al.filters[302] == {
            bindings.AL_FILTER_TYPE: bindings.AL_FILTER_BANDPASS,
            bindings.AL_BANDPASS_GAIN: 0.75,
            bindings.AL_BANDPASS_GAINLF: 0.1,
            bindings.AL_BANDPASS_GAINHF: 0.2,
        }
        assert library.al.source_sends[(100, 0)] == (
            bindings.AL_EFFECTSLOT_NULL,
            bindings.AL_FILTER_NULL,
        )
        assert library.al.allocated_effects == set()
        assert library.al.allocated_effect_slots == set()
        assert library.al.allocated_filters == {302}

        set_voice_config(playback, voice, VoiceConfig())
        assert library.al.sources[100][bindings.AL_DIRECT_FILTER] == (
            bindings.AL_FILTER_NULL
        )
        assert library.al.allocated_filters == set()
        release(playback, voice)
        release(playback, clip)

    assert library.al.allocated_effects == set()
    assert library.al.allocated_effect_slots == set()
    assert library.al.allocated_filters == set()


def test_effect_bus_is_shared_updated_chained_and_lifecycle_checked() -> None:
    library = FakeLibrary()
    with open_playback(library=as_library(library)) as playback:
        output = create_effect_bus(
            playback,
            EffectBusConfig(effect=Reverb(decay_time=1.5), gain=0.8),
        )
        source = create_effect_bus(
            playback,
            EffectBusConfig(
                effect=Chorus(rate=0.5),
                auxiliary_send_auto=False,
                target=output,
            ),
        )
        clip = upload(playback, PCM(b"\0\0" * 10, channels=1, sample_rate=10))
        first = play(playback, clip, effect_sends=(EffectSend(bus=source),))
        second = play(playback, clip, effect_sends=(EffectSend(bus=source),))

        assert library.al.source_sends[(100, 0)] == (401, bindings.AL_FILTER_NULL)
        assert library.al.source_sends[(101, 0)] == (401, bindings.AL_FILTER_NULL)
        assert library.al.effect_slots[400] == {
            bindings.AL_EFFECTSLOT_EFFECT: 200,
            bindings.AL_EFFECTSLOT_GAIN: 0.8,
            bindings.AL_EFFECTSLOT_AUXILIARY_SEND_AUTO: 1,
        }
        assert library.al.effect_slots[401] == {
            bindings.AL_EFFECTSLOT_EFFECT: 201,
            bindings.AL_EFFECTSLOT_GAIN: 1.0,
            bindings.AL_EFFECTSLOT_AUXILIARY_SEND_AUTO: 0,
            bindings.AL_EFFECTSLOT_TARGET_SOFT: 400,
        }
        assert get_effect_bus_config(playback, source).target == output

        replacement = EffectBusConfig(effect=Echo(delay=0.2), gain=0.25)
        set_effect_bus_config(playback, source, replacement)
        assert get_effect_bus_config(playback, source) == replacement
        assert library.al.effect_slots[401] == {
            bindings.AL_EFFECTSLOT_EFFECT: 202,
            bindings.AL_EFFECTSLOT_GAIN: 0.25,
            bindings.AL_EFFECTSLOT_AUXILIARY_SEND_AUTO: 1,
            bindings.AL_EFFECTSLOT_TARGET_SOFT: bindings.AL_EFFECTSLOT_NULL,
        }
        assert library.al.allocated_effects == {200, 202}

        with pytest.raises(ResourceInUseError, match="attached"):
            release(playback, source)
        release(playback, first)
        release(playback, second)
        release(playback, source)
        release(playback, output)
        release(playback, clip)

    assert library.al.allocated_effects == set()
    assert library.al.allocated_effect_slots == set()


def test_effect_bus_rejects_cross_session_targets_cycles_and_missing_extensions() -> None:
    first_library = FakeLibrary()
    second_library = FakeLibrary()
    with (
        open_playback(library=as_library(first_library)) as first_playback,
        open_playback(library=as_library(second_library)) as second_playback,
    ):
        first = create_effect_bus(first_playback, EffectBusConfig(effect=Reverb()))
        second = create_effect_bus(second_playback, EffectBusConfig(effect=Echo()))
        with pytest.raises(InvalidHandleError, match="does not belong"):
            create_effect_bus(
                first_playback,
                EffectBusConfig(effect=Chorus(), target=second),
            )
        with pytest.raises(ValueError, match="target itself"):
            set_effect_bus_config(
                first_playback,
                first,
                EffectBusConfig(effect=Reverb(), target=first),
            )

    library = FakeLibrary()
    library.al_extensions.remove("ALC_EXT_DEDICATED")
    with (
        open_playback(library=as_library(library)) as playback,
        pytest.raises(AudioBackendError, match="ALC_EXT_DEDICATED"),
    ):
        create_effect_bus(
            playback,
            EffectBusConfig(effect=DedicatedDialogue()),
        )


def test_effect_send_requires_exactly_one_inline_effect_or_bus() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        EffectSend()


def test_band_pass_filter_configures_an_auxiliary_send() -> None:
    library = FakeLibrary()
    send = EffectSend(
        effect=Reverb(),
        filter=BandPassFilter(
            gain=0.75,
            low_frequency_gain=0.1,
            high_frequency_gain=0.2,
        ),
    )
    with open_playback(library=as_library(library)) as playback:
        clip = upload(playback, PCM(b"\0\0" * 10, channels=1, sample_rate=10))
        voice = play(playback, clip, effect_sends=(send,))

        slot, filter_identifier = library.al.source_sends[(100, 0)]
        assert slot == 400
        assert library.al.filters[filter_identifier] == {
            bindings.AL_FILTER_TYPE: bindings.AL_FILTER_BANDPASS,
            bindings.AL_BANDPASS_GAIN: 0.75,
            bindings.AL_BANDPASS_GAINLF: 0.1,
            bindings.AL_BANDPASS_GAINHF: 0.2,
        }

        release(playback, voice)
        release(playback, clip)

    assert library.al.allocated_effects == set()
    assert library.al.allocated_effect_slots == set()
    assert library.al.allocated_filters == set()


def test_failed_voice_efx_update_restores_config_and_native_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library = FakeLibrary()
    previous = VoiceConfig(
        gain=0.75,
        effect_sends=(EffectSend(effect=Reverb(decay_time=0.5)),),
    )
    replacement = VoiceConfig(
        gain=0.25,
        effect_sends=(EffectSend(effect=Reverb(decay_time=1.0)),),
    )
    with open_playback(library=as_library(library)) as playback:
        clip = upload(playback, PCM(b"\0\0" * 10, channels=1, sample_rate=10))
        voice = play(playback, clip, previous)
        original_source3i = library.al.source3i
        fail_next_attachment = True

        def fail_one_attachment(
            identifier: int,
            parameter: int,
            value1: int,
            value2: int,
            value3: int,
        ) -> None:
            nonlocal fail_next_attachment
            original_source3i(identifier, parameter, value1, value2, value3)
            if fail_next_attachment:
                fail_next_attachment = False
                library.al.error = bindings.AL_INVALID_OPERATION

        monkeypatch.setattr(library.al, "source3i", fail_one_attachment)

        with pytest.raises(AudioBackendError, match="configure voice EFX routing"):
            set_voice_config(playback, voice, replacement)

        assert library.al.sources[100][bindings.AL_GAIN] == 0.75
        assert library.al.source_sends[(100, 0)] == (400, bindings.AL_FILTER_NULL)
        assert library.al.allocated_effects == {200}
        assert library.al.allocated_effect_slots == {400}
        assert library.al.allocated_filters == set()
        assert playback._voice_configs[voice._token] == previous


@pytest.mark.parametrize(
    ("effect", "method_name"),
    [
        (Chorus(), "effecti"),
        (Echo(), "effectf"),
        (EAXReverb(), "effectfv"),
    ],
)
def test_failed_effect_configuration_releases_incomplete_resources(
    monkeypatch: pytest.MonkeyPatch,
    effect: Effect,
    method_name: str,
) -> None:
    library = FakeLibrary()
    original = getattr(library.al, method_name)
    fail_next_call = True

    def fail_once(*arguments: object) -> None:
        nonlocal fail_next_call
        original(*arguments)
        if fail_next_call:
            fail_next_call = False
            library.al.error = bindings.AL_INVALID_VALUE

    monkeypatch.setattr(library.al, method_name, fail_once)
    with open_playback(library=as_library(library)) as playback:
        clip = upload(playback, PCM(b"\0\0", channels=1, sample_rate=1))
        with pytest.raises(AudioBackendError, match="configure"):
            play(playback, clip, effect_sends=(EffectSend(effect=effect),))

        assert library.al.sources == {}
        assert library.al.allocated_effects == set()
        assert library.al.allocated_effect_slots == set()
        assert library.al.allocated_filters == set()


def test_voice_efx_require_device_support_and_available_send_slots() -> None:
    library = FakeLibrary()
    library.alc.extensions.remove("ALC_EXT_EFX")
    with open_playback(library=as_library(library)) as playback:
        clip = upload(playback, PCM(b"\0\0", channels=1, sample_rate=1))
        with pytest.raises(AudioBackendError, match="does not support EFX"):
            play(playback, clip, filter=LowPassFilter())
        assert library.al.sources == {}
        assert library.al.allocated_filters == set()

    library = FakeLibrary()
    library.alc.max_auxiliary_sends = 1
    sends = (
        EffectSend(effect=Reverb(decay_time=0.5)),
        EffectSend(effect=Reverb(decay_time=1.0)),
    )
    with open_playback(library=as_library(library)) as playback:
        clip = upload(playback, PCM(b"\0\0", channels=1, sample_rate=1))
        voice = play(playback, clip)
        with pytest.raises(AudioBackendError, match="at most 1"):
            set_voice_config(
                playback,
                voice,
                VoiceConfig(gain=0.25, effect_sends=sends),
            )
        assert library.al.sources[100][bindings.AL_GAIN] == 1.0
        assert playback._voice_configs[voice._token] == VoiceConfig()
        release(playback, voice)

        with pytest.raises(AudioBackendError, match="at most 1"):
            play(playback, clip, effect_sends=sends)
        assert library.al.sources == {}
        assert library.al.allocated_effects == set()
