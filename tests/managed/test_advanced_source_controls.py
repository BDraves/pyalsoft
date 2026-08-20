"""Acceptance tests for managed advanced source controls."""

from __future__ import annotations

from dataclasses import replace
from math import pi

import pytest

import pyalsoft._managed.playback.voices as voices_module
from examples.advanced_sources import advanced_sources
from pyalsoft import (
    PCM,
    Acoustics,
    AudioBackendError,
    DirectChannelsMode,
    DistanceModel,
    InvalidVoiceStateError,
    Resampler,
    SpatializationMode,
    StereoMode,
    VoiceClock,
    VoiceConfig,
    VoiceLatency,
    bindings,
    close_playback,
    get_playback_clock,
    get_voice_clock,
    get_voice_latency,
    list_resamplers,
    open_playback,
    open_stream,
    pause,
    play,
    release,
    set_acoustics,
    set_voice_config,
    stop,
    try_write_stream,
    upload,
)
from pyalsoft._managed.playback.session import (
    _get_voice_state as original_get_voice_state,
)
from tests._support.managed_backend import FakeLibrary, as_library


def test_advanced_source_example_is_executable() -> None:
    library = FakeLibrary()

    report = advanced_sources(as_library(library))

    assert report.voice_latency == VoiceLatency(60_000 << 32, 25_000_000, 48_000)
    assert report.voice_clock == VoiceClock(60_000 << 32, 42_500_000_000, 48_000)
    assert report.playback_clock.output_latency_ns == 25_000_000
    assert (
        library.al.sources == {}
        and library.al.allocated_buffers == set()
        and library.al.allocated_effects == set()
    )


def test_advanced_source_config_maps_to_native_properties() -> None:
    library = FakeLibrary()
    pcm = PCM(bytes(32), channels=2, sample_rate=8)

    with open_playback(library=as_library(library)) as playback:
        resamplers = list_resamplers(playback)
        assert resamplers == (
            Resampler(0, "Nearest", is_default=False),
            Resampler(1, "Cubic Spline", is_default=True),
            Resampler(2, "23rd order Sinc", is_default=False),
        )
        clip = upload(playback, pcm)
        config = VoiceConfig(
            distance_model=DistanceModel.EXPONENT_CLAMPED,
            radius=2.5,
            spatialization=SpatializationMode.ENABLED,
            direct_channels=DirectChannelsMode.OFF,
            stereo_angles=(pi / 3.0, -pi / 3.0),
            resampler=resamplers[2],
            air_absorption_factor=1.0,
            room_rolloff_factor=0.7,
        )
        voice = play(playback, clip, config)
        super_config = VoiceConfig(
            stereo_mode=StereoMode.SUPER_STEREO,
            super_stereo_width=0.65,
        )
        super_stereo = play(playback, clip, super_config)

        source = library.al.sources[100]
        assert library.al.enabled == {bindings.AL_SOURCE_DISTANCE_MODEL}
        assert (
            source[bindings.AL_DISTANCE_MODEL] == bindings.AL_EXPONENT_DISTANCE_CLAMPED
        )
        assert source[bindings.AL_SOURCE_RADIUS] == 2.5
        assert source[bindings.AL_SOURCE_SPATIALIZE_SOFT] == bindings.AL_TRUE
        assert source[bindings.AL_STEREO_ANGLES] == pytest.approx((pi / 3.0, -pi / 3.0))
        assert source[bindings.AL_SOURCE_RESAMPLER_SOFT] == 2
        assert source[bindings.AL_AIR_ABSORPTION_FACTOR] == 1.0
        assert source[bindings.AL_ROOM_ROLLOFF_FACTOR] == 0.7
        assert (
            library.al.sources[101][bindings.AL_STEREO_MODE_SOFT]
            == bindings.AL_SUPER_STEREO_SOFT
        )
        assert library.al.sources[101][bindings.AL_SUPER_STEREO_WIDTH_SOFT] == 0.65
        set_voice_config(
            playback,
            super_stereo,
            replace(super_config, super_stereo_width=None),
        )
        assert library.al.sources[101][bindings.AL_SUPER_STEREO_WIDTH_SOFT] == 0.46

        library.al.states[100] = bindings.AL_STOPPED
        set_voice_config(
            playback,
            voice,
            replace(
                config,
                distance_model=None,
                spatialization=SpatializationMode.AUTO,
                stereo_angles=None,
                resampler=None,
                air_absorption_factor=0.0,
                room_rolloff_factor=0.0,
            ),
        )
        source = library.al.sources[100]
        assert (
            source[bindings.AL_DISTANCE_MODEL] == bindings.AL_INVERSE_DISTANCE_CLAMPED
        )
        assert source[bindings.AL_SOURCE_SPATIALIZE_SOFT] == bindings.AL_AUTO_SOFT
        assert source[bindings.AL_SOURCE_RESAMPLER_SOFT] == 1

        set_acoustics(
            playback,
            Acoustics(distance_model=DistanceModel.LINEAR_CLAMPED),
        )
        assert source[bindings.AL_DISTANCE_MODEL] == bindings.AL_LINEAR_DISTANCE_CLAMPED
        inherited = play(playback, clip)
        assert (
            library.al.sources[102][bindings.AL_DISTANCE_MODEL]
            == bindings.AL_LINEAR_DISTANCE_CLAMPED
        )
        assert inherited is not None


def test_direct_channel_remix_requires_stereo_and_rejects_virtualization() -> None:
    library = FakeLibrary()
    with open_playback(library=as_library(library)) as playback:
        mono = upload(playback, PCM(bytes(16), channels=1, sample_rate=8))
        stereo = upload(playback, PCM(bytes(32), channels=2, sample_rate=8))
        config = VoiceConfig(direct_channels=DirectChannelsMode.REMIX_UNMATCHED)

        with pytest.raises(ValueError, match="direct_channels requires a stereo"):
            play(playback, mono, config)

        voice = play(playback, stereo, config)
        assert (
            library.al.sources[100][bindings.AL_DIRECT_CHANNELS_SOFT]
            == bindings.AL_REMIX_UNMATCHED_SOFT
        )
        assert voice is not None

    with pytest.raises(ValueError, match="direct_channels cannot be combined"):
        VoiceConfig(
            direct_channels=DirectChannelsMode.DROP_UNMATCHED,
            spatialization=SpatializationMode.ENABLED,
        )


def test_advanced_timing_queries_return_atomic_managed_values() -> None:
    library = FakeLibrary()
    with open_playback(library=as_library(library)) as playback:
        clip = upload(playback, PCM(bytes(16), channels=1, sample_rate=8))
        voice = play(playback, clip)

        assert get_voice_latency(playback, voice) == VoiceLatency(
            10 << 32, 25_000_000, 8
        )
        assert get_voice_clock(playback, voice) == VoiceClock(
            10 << 32, 42_500_000_000, 8
        )
        clock = get_playback_clock(playback)
        assert clock.device_time_ns == 42_500_000_000
        assert clock.output_latency_ns == 25_000_000
        assert clock.device_time_seconds == 42.5
        assert clock.output_latency_seconds == 0.025


def test_stereo_mode_query_uses_the_voice_playback_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library = FakeLibrary()
    with open_playback(library=as_library(library)) as playback:
        clip = upload(playback, PCM(bytes(32), channels=2, sample_rate=8))
        voice = play(playback, clip)
        stop(playback, voice)
        library.alc.current_context = library.alc.previous_context

        def assert_active_context(
            active_playback: object, identifier: int, operation: str
        ) -> object:
            assert active_playback is playback
            assert library.alc.current_context is playback._context
            return original_get_voice_state(playback, identifier, operation)

        monkeypatch.setattr(
            voices_module,
            "_get_voice_state",
            assert_active_context,
        )

        set_voice_config(
            playback,
            voice,
            VoiceConfig(stereo_mode=StereoMode.SUPER_STEREO),
        )


def test_stereo_mode_cannot_change_while_playing_or_paused() -> None:
    library = FakeLibrary()
    with open_playback(library=as_library(library)) as playback:
        clip = upload(playback, PCM(bytes(32), channels=2, sample_rate=8))
        voice = play(playback, clip)
        config = VoiceConfig(stereo_mode=StereoMode.SUPER_STEREO)

        with pytest.raises(InvalidVoiceStateError, match="playing or paused"):
            set_voice_config(playback, voice, config)
        pause(playback, voice)
        with pytest.raises(InvalidVoiceStateError, match="playing or paused"):
            set_voice_config(playback, voice, config)
        stop(playback, voice)
        set_voice_config(playback, voice, config)


def test_advanced_controls_and_timing_support_streams() -> None:
    library = FakeLibrary()
    with open_playback(library=as_library(library)) as playback:
        resampler = list_resamplers(playback)[0]
        config = VoiceConfig(
            stereo_angles=(0.5, -0.5),
            resampler=resampler,
            radius=0.25,
        )
        stream = open_stream(
            playback,
            channels=2,
            sample_rate=8,
            config=config,
        )
        assert try_write_stream(playback, stream, bytes(32))

        source = library.al.sources[100]
        assert source[bindings.AL_STEREO_ANGLES] == (0.5, -0.5)
        assert source[bindings.AL_SOURCE_RESAMPLER_SOFT] == 0
        assert source[bindings.AL_SOURCE_RADIUS] == 0.25
        set_voice_config(
            playback,
            stream,
            VoiceConfig(
                direct_channels=DirectChannelsMode.REMIX_UNMATCHED,
                radius=0.25,
            ),
        )
        assert (
            source[bindings.AL_DIRECT_CHANNELS_SOFT] == bindings.AL_REMIX_UNMATCHED_SOFT
        )
        assert get_voice_latency(playback, stream) == VoiceLatency(
            10 << 32,
            25_000_000,
            8,
        )
        assert get_voice_clock(playback, stream) == VoiceClock(
            10 << 32,
            42_500_000_000,
            8,
        )
        release(playback, stream)


@pytest.mark.parametrize(
    ("extension", "config", "message", "device_extension"),
    [
        (
            "AL_EXT_source_distance_model",
            VoiceConfig(distance_model=DistanceModel.LINEAR),
            "AL_EXT_source_distance_model",
            False,
        ),
        (
            "AL_EXT_SOURCE_RADIUS",
            VoiceConfig(radius=1.0),
            "AL_EXT_SOURCE_RADIUS",
            False,
        ),
        (
            "AL_SOFT_source_spatialize",
            VoiceConfig(spatialization=SpatializationMode.DISABLED),
            "AL_SOFT_source_spatialize",
            False,
        ),
        (
            "AL_SOFT_direct_channels",
            VoiceConfig(direct_channels=DirectChannelsMode.DROP_UNMATCHED),
            "AL_SOFT_direct_channels",
            False,
        ),
        (
            "AL_SOFT_direct_channels_remix",
            VoiceConfig(direct_channels=DirectChannelsMode.REMIX_UNMATCHED),
            "AL_SOFT_direct_channels_remix",
            False,
        ),
        (
            "AL_EXT_STEREO_ANGLES",
            VoiceConfig(stereo_angles=(0.5, -0.5)),
            "AL_EXT_STEREO_ANGLES",
            False,
        ),
        (
            "AL_SOFT_source_resampler",
            VoiceConfig(resampler=Resampler(0, "Nearest")),
            "AL_SOFT_source_resampler",
            False,
        ),
        (
            "ALC_EXT_EFX",
            VoiceConfig(air_absorption_factor=1.0),
            "ALC_EXT_EFX",
            True,
        ),
        (
            "AL_SOFT_UHJ",
            VoiceConfig(stereo_mode=StereoMode.SUPER_STEREO),
            "AL_SOFT_UHJ",
            False,
        ),
    ],
)
def test_requested_advanced_controls_require_their_extensions(
    extension: str,
    config: VoiceConfig,
    message: str,
    device_extension: bool,
) -> None:
    library = FakeLibrary()
    if device_extension:
        library.alc.extensions.discard(extension)
    else:
        library.al_extensions.discard(extension)

    with open_playback(library=as_library(library)) as playback:
        clip = upload(playback, PCM(bytes(32), channels=2, sample_rate=8))
        with pytest.raises(AudioBackendError, match=message):
            play(playback, clip, config)

        assert library.al.sources == {}


def test_default_source_config_does_not_require_optional_extensions() -> None:
    library = FakeLibrary()
    with open_playback(library=as_library(library)) as playback:
        library.al_extensions.clear()
        library.alc.extensions.discard("ALC_EXT_EFX")
        clip = upload(playback, PCM(bytes(32), channels=2, sample_rate=8))

        voice = play(playback, clip)

        assert voice is not None


def test_timing_queries_validate_extensions_and_result_shapes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library = FakeLibrary()
    with open_playback(library=as_library(library)) as playback:
        clip = upload(playback, PCM(bytes(16), channels=1, sample_rate=8))
        voice = play(playback, clip)

        library.al_extensions.discard("AL_SOFT_source_latency")
        with pytest.raises(AudioBackendError, match="AL_SOFT_source_latency"):
            get_voice_latency(playback, voice)
        library.al_extensions.add("AL_SOFT_source_latency")

        monkeypatch.setattr(
            library.al,
            "get_sourcei64v_soft",
            lambda *args, **kwargs: (1,),
        )
        with pytest.raises(AudioBackendError, match="invalid source latency"):
            get_voice_latency(playback, voice)

        library.alc.extensions.discard("ALC_SOFT_device_clock")
        with pytest.raises(AudioBackendError, match="ALC_SOFT_device_clock"):
            get_voice_clock(playback, voice)
        with pytest.raises(AudioBackendError, match="ALC_SOFT_device_clock"):
            get_playback_clock(playback)


def test_playback_clock_clears_stale_alc_errors_and_validates_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library = FakeLibrary()
    with open_playback(library=as_library(library)) as playback:
        library.alc.error = bindings.ALC_INVALID_VALUE
        assert get_playback_clock(playback).device_time_seconds == 42.5

        monkeypatch.setattr(
            library.alc,
            "get_integer64v_soft",
            lambda *args, **kwargs: (1,),
        )
        with pytest.raises(AudioBackendError, match="invalid playback clock"):
            get_playback_clock(playback)


def test_failed_advanced_update_restores_native_and_managed_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library = FakeLibrary()
    with open_playback(library=as_library(library)) as playback:
        clip = upload(playback, PCM(bytes(32), channels=2, sample_rate=8))
        original = VoiceConfig(gain=0.5, radius=1.0)
        voice = play(playback, clip, original)
        sourcef = library.al.sourcef
        fail_once = True

        def fail_radius_once(identifier: int, parameter: int, value: float) -> None:
            nonlocal fail_once
            sourcef(identifier, parameter, value)
            if parameter == bindings.AL_SOURCE_RADIUS and value == 2.0 and fail_once:
                fail_once = False
                library.al.error = bindings.AL_INVALID_VALUE

        monkeypatch.setattr(library.al, "sourcef", fail_radius_once)
        with pytest.raises(AudioBackendError, match="configure voice"):
            set_voice_config(
                playback,
                voice,
                replace(original, gain=0.75, radius=2.0),
            )

        source = library.al.sources[100]
        assert source[bindings.AL_GAIN] == 0.5
        assert source[bindings.AL_SOURCE_RADIUS] == 1.0
        assert playback._voice_configs[voice._token] == original


def test_playback_close_clears_cached_super_stereo_defaults() -> None:
    library = FakeLibrary()
    playback = open_playback(library=as_library(library))
    clip = upload(playback, PCM(bytes(32), channels=2, sample_rate=8))
    play(
        playback,
        clip,
        stereo_mode=StereoMode.SUPER_STEREO,
        super_stereo_width=0.7,
    )
    assert playback._super_stereo_width_defaults == {100: 0.46}

    close_playback(playback)

    assert playback._super_stereo_width_defaults == {}


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"radius": -0.1}, "radius cannot be negative"),
        ({"stereo_angles": (0.0,)}, "stereo_angles must contain exactly two"),
        ({"resampler": 1}, "resampler must be a Resampler or None"),
        ({"air_absorption_factor": 10.1}, "air_absorption_factor must be between"),
        ({"room_rolloff_factor": -0.1}, "room_rolloff_factor must be between"),
        ({"super_stereo_width": 1.1}, "super_stereo_width must be between"),
    ],
)
def test_advanced_source_config_validation(
    arguments: dict[str, object], message: str
) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        VoiceConfig(**arguments)  # type: ignore[arg-type]
