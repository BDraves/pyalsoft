"""Tests for convenient file playback and its implicit runtime."""

from __future__ import annotations

from pathlib import Path

import pytest

from pyalsoft import (
    PCM,
    DirectChannelsMode,
    DistanceModel,
    EffectSend,
    HighPassFilter,
    InvalidVoiceStateError,
    LowPassFilter,
    PlayingSound,
    Resampler,
    Reverb,
    SampleType,
    SoundEndReason,
    SpatializationMode,
    StereoMode,
    VoiceConfig,
    VoiceState,
    bindings,
    get_sound_info,
    play,
)
from tests._support.managed_backend import FakeLibrary
from tests.managed._sound_support import _assert_state, _write_wave


def test_playing_sound_delegates_status_and_controls(
    tmp_path: Path,
    default_library: FakeLibrary,
) -> None:
    path = tmp_path / "sound.wav"
    _write_wave(path)

    sound = play(path, config=VoiceConfig(gain=0.5))

    assert isinstance(sound, PlayingSound)
    assert repr(sound) == "PlayingSound(<opaque>)"
    assert sound.playing
    _assert_state(sound, VoiceState.PLAYING)
    assert sound.status.offset_seconds == 0.25
    assert default_library.al.sources[100][bindings.AL_GAIN] == 0.5

    sound.set_config(VoiceConfig(position=(1.0, 2.0, 3.0)))
    assert default_library.al.sources[100][bindings.AL_POSITION] == (
        1.0,
        2.0,
        3.0,
    )
    sound.pause()
    _assert_state(sound, VoiceState.PAUSED)
    sound.resume()
    _assert_state(sound, VoiceState.PLAYING)

    sound.stop()
    sound.stop()
    assert not sound.playing
    _assert_state(sound, VoiceState.STOPPED)
    assert sound.end_reason is SoundEndReason.STOPPED
    assert default_library.al.sources == {}
    assert len(default_library.al.allocated_buffers) == 1
    with pytest.raises(InvalidVoiceStateError, match="stopped"):
        sound.resume()


def test_playing_sound_exposes_timeline_and_individual_source_controls(
    tmp_path: Path,
    default_library: FakeLibrary,
) -> None:
    path = tmp_path / "controlled.wav"
    _write_wave(path)

    sound = play(
        path,
        config=VoiceConfig(gain=0.1),
        position=(1.0, 2.0, 3.0),
        velocity=(4.0, 5.0, 6.0),
        direction=(0.0, 0.0, 1.0),
        gain=0.6,
        pitch=1.25,
        looping=True,
        relative=True,
        min_gain=0.1,
        max_gain=0.9,
        reference_distance=2.0,
        max_distance=20.0,
        rolloff_factor=0.5,
        cone_inner_angle=60.0,
        cone_outer_angle=180.0,
        cone_outer_gain=0.2,
        offset_seconds=0.0005,
    )

    assert sound.duration_seconds == pytest.approx(0.001)
    assert sound.frame_count == 8
    assert sound.offset_frames == 4
    assert sound.remaining_frames == 4
    assert sound.channels == 1
    assert sound.sample_rate == 8_000
    assert sound.sample_type is SampleType.INT16
    assert sound.path == path.resolve()
    assert sound.info == get_sound_info(path)
    assert sound.offset_seconds == pytest.approx(0.0005)
    assert sound.remaining_seconds == pytest.approx(0.0005)
    assert sound.progress == pytest.approx(0.5)
    assert sound.position == (1.0, 2.0, 3.0)
    assert sound.velocity == (4.0, 5.0, 6.0)
    assert sound.direction == (0.0, 0.0, 1.0)
    assert sound.gain == 0.6
    assert sound.pitch == 1.25
    assert sound.looping
    assert sound.relative
    assert sound.min_gain == 0.1
    assert sound.max_gain == 0.9
    assert sound.reference_distance == 2.0
    assert sound.max_distance == 20.0
    assert sound.rolloff_factor == 0.5
    assert sound.cone_inner_angle == 60.0
    assert sound.cone_outer_angle == 180.0
    assert sound.cone_outer_gain == 0.2

    source = default_library.al.sources[100]
    assert source[bindings.AL_REFERENCE_DISTANCE] == 2.0
    assert source[bindings.AL_MAX_DISTANCE] == 20.0
    assert source[bindings.AL_ROLLOFF_FACTOR] == 0.5
    assert source[bindings.AL_CONE_INNER_ANGLE] == 60.0
    assert source[bindings.AL_CONE_OUTER_ANGLE] == 180.0
    assert source[bindings.AL_CONE_OUTER_GAIN] == 0.2

    sound.pitch = 1.5
    sound.position = (-1.0, 0.0, -2.0)
    assert sound.pitch == 1.5
    assert sound.gain == 0.6
    assert source[bindings.AL_PITCH] == 1.5
    assert source[bindings.AL_POSITION] == (-1.0, 0.0, -2.0)

    default_library.al.source_property_calls.clear()
    sound.update(
        position=(3.0, 1.0, -2.0),
        velocity=(-1.0, 0.0, 1.0),
        gain=0.75,
    )
    assert default_library.al.source_property_calls == [
        (100, bindings.AL_POSITION),
        (100, bindings.AL_VELOCITY),
        (100, bindings.AL_GAIN),
    ]

    sound.seek(0.00075)
    assert sound.offset_seconds == pytest.approx(0.00075)
    sound.pause()
    sound.rewind()
    assert sound.state is VoiceState.INITIAL
    assert sound.offset_seconds == 0.0
    sound.seek_frames(6)
    assert sound.state is VoiceState.INITIAL
    assert sound.offset_frames == 6
    sound.restart()
    assert sound.playing
    assert sound.offset_seconds == 0.0


def test_playing_sound_validates_seeks_and_can_restart_after_completion(
    tmp_path: Path,
    default_library: FakeLibrary,
) -> None:
    path = tmp_path / "restart.wav"
    _write_wave(path)
    sound = play(
        path,
        gain=0.4,
        filter=LowPassFilter(high_frequency_gain=0.5),
    )

    with pytest.raises(ValueError, match="at least 0.0"):
        sound.seek(-0.1)
    with pytest.raises(ValueError, match="less than the sound duration"):
        sound.seek(sound.duration_seconds)

    default_library.al.states[100] = bindings.AL_STOPPED
    assert sound.finished
    assert sound.end_reason is SoundEndReason.FINISHED
    assert sound.done
    assert sound.stopped
    assert sound.offset_seconds == sound.duration_seconds
    assert default_library.al.allocated_filters == set()

    high_pass = HighPassFilter(low_frequency_gain=0.25)
    sound.update(
        gain=0.8,
        position=(2.0, 0.0, -1.0),
        filter=high_pass,
    )
    assert sound.gain == 0.8
    assert sound.config.filter == high_pass
    assert default_library.al.sources == {}

    sound.rewind()
    assert sound.state is VoiceState.INITIAL
    assert sound.end_reason is None
    assert default_library.al.sources[101][bindings.AL_GAIN] == 0.8
    assert default_library.al.allocated_filters == {301}

    sound.restart()

    assert sound.playing
    assert not sound.finished
    assert sound.gain == 0.8
    assert set(default_library.al.sources) == {101}
    assert default_library.al.sources[101][bindings.AL_GAIN] == 0.8


def test_playing_sound_exposes_live_efx_controls(
    tmp_path: Path,
    default_library: FakeLibrary,
) -> None:
    path = tmp_path / "efx.wav"
    _write_wave(path)
    room = Reverb(gain=0.2, decay_time=0.6)
    send = EffectSend(effect=room)
    low_pass = LowPassFilter(high_frequency_gain=0.1)

    sound = play(
        path,
        looping=True,
        filter=low_pass,
        effect_sends=(send,),
    )

    assert sound.effect_sends == (send,)
    assert default_library.al.allocated_filters == {300}
    assert default_library.al.allocated_effects == {200}
    assert default_library.al.allocated_effect_slots == {400}

    sound.update(gain=0.5)
    assert sound.filter == low_pass
    assert default_library.al.allocated_filters == {300}
    assert default_library.al.allocated_effects == {200}
    assert default_library.al.allocated_effect_slots == {400}

    high_pass = HighPassFilter(low_frequency_gain=0.25)
    sound.update(filter=high_pass)
    assert sound.config.filter == high_pass
    assert default_library.al.allocated_filters == {301}
    assert default_library.al.allocated_effects == {200}
    assert default_library.al.allocated_effect_slots == {400}

    sound.update(filter=None)
    assert sound.filter is None
    assert default_library.al.allocated_filters == set()
    assert default_library.al.allocated_effects == {200}
    assert default_library.al.allocated_effect_slots == {400}

    sound.effect_sends = ()
    assert sound.effect_sends == ()
    assert default_library.al.allocated_effects == set()
    assert default_library.al.allocated_effect_slots == set()
    sound.stop()


def test_playing_sound_exposes_all_advanced_source_controls(
    default_library: FakeLibrary,
) -> None:
    resampler = Resampler(0, "Nearest")
    sound = play(
        PCM(bytes(32), channels=2, sample_rate=8),
        config=VoiceConfig(
            distance_model=DistanceModel.LINEAR,
            stereo_angles=(0.5, -0.5),
            resampler=resampler,
        ),
    )

    sound.update(
        radius=2.0,
        spatialization=SpatializationMode.DISABLED,
        air_absorption_factor=1.0,
        room_rolloff_factor=0.5,
    )
    assert sound.radius == 2.0
    assert sound.spatialization is SpatializationMode.DISABLED
    assert sound.direct_channels is DirectChannelsMode.OFF
    assert sound.air_absorption_factor == 1.0
    assert sound.room_rolloff_factor == 0.5

    sound.distance_model = None
    sound.stereo_angles = None
    sound.resampler = None
    assert sound.distance_model is None
    assert sound.stereo_angles is None
    assert sound.resampler is None

    source = default_library.al.sources[100]
    assert source[bindings.AL_SOURCE_RADIUS] == 2.0
    assert source[bindings.AL_SOURCE_SPATIALIZE_SOFT] == bindings.AL_FALSE
    assert source[bindings.AL_AIR_ABSORPTION_FACTOR] == 1.0
    assert source[bindings.AL_ROOM_ROLLOFF_FACTOR] == 0.5
    assert source[bindings.AL_STEREO_ANGLES] == pytest.approx(
        (0.5235987755982988, -0.5235987755982988)
    )
    assert source[bindings.AL_SOURCE_RESAMPLER_SOFT] == 1

    sound.stop()
    sound.stereo_mode = StereoMode.SUPER_STEREO
    sound.super_stereo_width = 0.6
    sound.restart()
    assert sound.stereo_mode is StereoMode.SUPER_STEREO
    assert sound.super_stereo_width == 0.6
    assert default_library.al.sources[101][bindings.AL_SUPER_STEREO_WIDTH_SOFT] == 0.6
    sound.super_stereo_width = None
    assert sound.super_stereo_width is None
    assert default_library.al.sources[101][bindings.AL_SUPER_STEREO_WIDTH_SOFT] == 0.46

    with pytest.raises(InvalidVoiceStateError, match="playing or paused"):
        sound.stereo_mode = StereoMode.NORMAL
    sound.stop()
    sound.stereo_mode = StereoMode.NORMAL
    sound.restart()
    sound.direct_channels = DirectChannelsMode.DROP_UNMATCHED
    assert sound.stereo_mode is StereoMode.NORMAL
    assert sound.direct_channels is DirectChannelsMode.DROP_UNMATCHED
    assert (
        default_library.al.sources[102][bindings.AL_DIRECT_CHANNELS_SOFT]
        == bindings.AL_DROP_UNMATCHED_SOFT
    )
