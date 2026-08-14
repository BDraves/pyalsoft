"""Tests for explicit managed playback sessions."""

from __future__ import annotations

import pytest

from pyalsoft import (
    PCM,
    AudioBackendError,
    EffectSend,
    HighPassFilter,
    LowPassFilter,
    Reverb,
    VoiceConfig,
    bindings,
    open_playback,
    play,
    release,
    set_voice_config,
    upload,
)
from tests._support.managed_backend import FakeLibrary, as_library


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
            VoiceConfig(filter=HighPassFilter(low_frequency_gain=0.1)),
        )

        assert library.al.sources[100][bindings.AL_DIRECT_FILTER] == 302
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
