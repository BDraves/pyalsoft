"""Tests for convenient file playback and its implicit runtime."""

from __future__ import annotations

import wave
from collections.abc import Iterator
from pathlib import Path

import pytest

import pyalsoft._playback as playback
from pyalsoft import (
    PCM,
    Acoustics,
    AudioError,
    AudioFileError,
    DistanceModel,
    EffectSend,
    HighPassFilter,
    InvalidVoiceStateError,
    Listener,
    LowPassFilter,
    PlayingSound,
    Reverb,
    SampleType,
    SoundEndReason,
    VoiceConfig,
    VoiceState,
    bindings,
    get_acoustics,
    get_listener,
    get_sound_info,
    open_playback,
    play,
    set_acoustics,
    set_listener,
    shutdown,
    update_acoustics,
    update_listener,
)
from tests.test_playback import FakeLibrary, as_library


def _write_wave(
    path: Path,
    *,
    channels: int = 1,
    sample_width: int = 2,
    sample_rate: int = 8_000,
) -> None:
    with wave.open(str(path), "wb") as destination:
        destination.setnchannels(channels)
        destination.setsampwidth(sample_width)
        destination.setframerate(sample_rate)
        destination.writeframes(b"\0" * channels * sample_width * 8)


def _assert_state(sound: PlayingSound, expected: VoiceState) -> None:
    assert sound.state is expected


@pytest.fixture
def default_library(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeLibrary]:
    shutdown()
    library = FakeLibrary()
    monkeypatch.setattr(
        playback,
        "open_playback",
        lambda: open_playback(library=as_library(library)),
    )
    yield library
    shutdown()


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


def test_default_runtime_plays_in_memory_pcm(
    default_library: FakeLibrary,
) -> None:
    pcm = PCM(
        samples=b"\x01\x00\x02\x00\x03\x00\x04\x00",
        channels=1,
        sample_rate=8_000,
    )

    sound = play(pcm)

    assert sound.playing
    assert sound.info == pcm.info
    assert default_library.al.buffers[1] == (
        bindings.AL_FORMAT_MONO16,
        pcm.samples,
        8_000,
    )
    with pytest.raises(AudioError, match="no source path"):
        _ = sound.path

    sound.stop()

    assert default_library.al.allocated_buffers == set()
    assert sound.info == pcm.info

    sound.restart()

    assert sound.playing
    assert default_library.al.buffers[2] == (
        bindings.AL_FORMAT_MONO16,
        pcm.samples,
        8_000,
    )
    default_library.al.states[101] = bindings.AL_STOPPED
    assert sound.finished
    assert default_library.al.allocated_buffers == set()


def test_default_runtime_releases_pcm_when_voice_creation_fails(
    default_library: FakeLibrary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pcm = PCM(samples=b"\x01\x00\x02\x00", channels=1, sample_rate=8_000)

    def fail_to_play(identifier: int) -> None:
        del identifier
        raise RuntimeError("could not play")

    monkeypatch.setattr(default_library.al, "source_play", fail_to_play)

    with pytest.raises(RuntimeError, match="could not play"):
        play(
            pcm,
            filter=LowPassFilter(high_frequency_gain=0.5),
            effect_sends=(EffectSend(effect=Reverb(decay_time=0.5)),),
        )

    assert default_library.al.sources == {}
    assert default_library.al.allocated_buffers == set()
    assert default_library.al.allocated_effects == set()
    assert default_library.al.allocated_effect_slots == set()
    assert default_library.al.allocated_filters == set()


def test_default_runtime_exposes_listener_and_acoustics_controls(
    default_library: FakeLibrary,
) -> None:
    listener = Listener(
        position=(1.0, 2.0, 3.0),
        velocity=(0.0, 0.0, -1.0),
        gain=0.8,
    )
    acoustics = Acoustics(
        distance_model=DistanceModel.EXPONENT,
        doppler_factor=0.75,
        speed_of_sound=300.0,
    )

    set_listener(listener)
    set_acoustics(acoustics)

    assert get_listener() == listener
    assert get_acoustics() == acoustics
    assert default_library.al.listener[bindings.AL_POSITION] == (1.0, 2.0, 3.0)
    assert default_library.al.distance_model_value == bindings.AL_EXPONENT_DISTANCE

    assert update_listener(position=(-1.0, 0.0, -2.0), gain=0.5) == Listener(
        position=(-1.0, 0.0, -2.0),
        velocity=(0.0, 0.0, -1.0),
        gain=0.5,
    )
    assert update_acoustics(doppler_factor=0.25) == Acoustics(
        distance_model=DistanceModel.EXPONENT,
        doppler_factor=0.25,
        speed_of_sound=300.0,
    )


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


def test_explicit_none_filter_overrides_a_filtered_play_config(
    tmp_path: Path,
    default_library: FakeLibrary,
) -> None:
    path = tmp_path / "filter-override.wav"
    _write_wave(path)
    filtered_config = VoiceConfig(
        filter=LowPassFilter(high_frequency_gain=0.1),
    )

    filtered_sound = play(path, config=filtered_config)
    assert filtered_sound.filter == filtered_config.filter
    assert default_library.al.allocated_filters == {300}
    filtered_sound.stop()

    sound = play(
        path,
        config=filtered_config,
        filter=None,
    )

    assert sound.filter is None
    assert default_library.al.allocated_filters == set()
    sound.stop()


def test_frame_offsets_are_validated_before_opening_the_default_device(
    tmp_path: Path,
    default_library: FakeLibrary,
) -> None:
    path = tmp_path / "frames.wav"
    _write_wave(path)

    with pytest.raises(ValueError, match="less than the sound frame count"):
        play(path, offset_frames=8)
    assert default_library.alc.current_context is default_library.alc.previous_context

    with pytest.raises(ValueError, match="cannot both be set"):
        play(path, offset_seconds=0.00025, offset_frames=2)
    assert default_library.alc.current_context is default_library.alc.previous_context

    sound = play(path, offset_frames=3)
    assert sound.offset_frames == 3
    assert sound.offset_seconds == pytest.approx(3 / 8_000)


def test_get_sound_info_reads_only_the_wave_header(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "metadata.wav"
    _write_wave(path, channels=2, sample_rate=44_100)

    def fail_readframes(source: wave.Wave_read, frame_count: int) -> bytes:
        del source, frame_count
        raise AssertionError("get_sound_info read sample data")

    monkeypatch.setattr(wave.Wave_read, "readframes", fail_readframes)

    info = get_sound_info(path)

    assert info.channels == 2
    assert info.sample_rate == 44_100
    assert info.frame_count == 8
    assert info.sample_type is SampleType.INT16


def test_missing_wave_uses_managed_audio_file_errors(
    tmp_path: Path,
    default_library: FakeLibrary,
) -> None:
    path = tmp_path / "missing.wav"

    with pytest.raises(AudioFileError, match="could not read WAV"):
        get_sound_info(path)
    with pytest.raises(AudioFileError, match="could not read WAV"):
        play(path)

    assert default_library.alc.current_context is default_library.alc.previous_context


def test_truncated_wave_data_is_rejected_before_opening_the_device(
    tmp_path: Path,
    default_library: FakeLibrary,
) -> None:
    path = tmp_path / "truncated.wav"
    _write_wave(path)
    path.write_bytes(path.read_bytes()[:-2])

    with pytest.raises(AudioFileError, match="truncated WAV"):
        play(path)

    assert default_library.alc.current_context is default_library.alc.previous_context


def test_ignored_handle_keeps_playing_and_finished_voices_are_reaped(
    tmp_path: Path,
    default_library: FakeLibrary,
) -> None:
    path = tmp_path / "sound.wav"
    _write_wave(path)

    play(path)

    assert default_library.al.states == {100: bindings.AL_PLAYING}
    default_library.al.states[100] = bindings.AL_STOPPED

    second = play(path)

    assert default_library.al.states == {101: bindings.AL_PLAYING}
    assert second.playing
    assert default_library.al.allocated_buffers == {1}


def test_disconnected_device_is_not_reported_as_natural_completion(
    tmp_path: Path,
    default_library: FakeLibrary,
) -> None:
    path = tmp_path / "disconnected.wav"
    _write_wave(path)
    sound = play(path)
    default_library.alc.extensions.add("ALC_EXT_disconnect")
    default_library.alc.connected = False
    default_library.al.offsets[100] = 0.0005
    default_library.al.frame_offsets[100] = 4
    default_library.al.states[100] = bindings.AL_STOPPED

    assert sound.end_reason is SoundEndReason.DEVICE_LOST
    assert not sound.finished
    assert sound.offset_frames < sound.frame_count


def test_reaped_handle_keeps_terminal_status(
    tmp_path: Path,
    default_library: FakeLibrary,
) -> None:
    path = tmp_path / "sound.wav"
    _write_wave(path)
    first = play(path)
    default_library.al.states[100] = bindings.AL_STOPPED

    play(path)

    _assert_state(first, VoiceState.STOPPED)
    assert not first.playing


def test_shutdown_releases_default_runtime_and_preserves_handle_status(
    tmp_path: Path,
    default_library: FakeLibrary,
) -> None:
    path = tmp_path / "sound.wav"
    _write_wave(path)
    sound = play(path)

    shutdown()

    assert default_library.al.sources == {}
    assert default_library.al.allocated_buffers == set()
    _assert_state(sound, VoiceState.STOPPED)
    assert sound.end_reason is SoundEndReason.SHUTDOWN
    sound.stop()


def test_shutdown_preserves_natural_completion(
    tmp_path: Path,
    default_library: FakeLibrary,
) -> None:
    path = tmp_path / "finished.wav"
    _write_wave(path)
    sound = play(path)
    default_library.al.states[100] = bindings.AL_STOPPED

    shutdown()

    assert sound.state is VoiceState.STOPPED
    assert sound.end_reason is SoundEndReason.FINISHED
    assert sound.offset_seconds == sound.duration_seconds
    assert sound.offset_frames == sound.frame_count


@pytest.mark.parametrize("sample_width", [3, 4])
def test_play_rejects_unsupported_wave_sample_widths(
    tmp_path: Path,
    default_library: FakeLibrary,
    sample_width: int,
) -> None:
    del default_library
    path = tmp_path / "unsupported.wav"
    _write_wave(path, sample_width=sample_width)

    with pytest.raises(AudioFileError, match=rf"{sample_width * 8}-bit"):
        play(path)


@pytest.mark.parametrize("contents", [b"", b"not audio"])
def test_invalid_wave_does_not_open_the_default_device(
    tmp_path: Path,
    default_library: FakeLibrary,
    contents: bytes,
) -> None:
    path = tmp_path / "not-a-wave.wav"
    path.write_bytes(contents)

    with pytest.raises(AudioFileError, match="could not read WAV"):
        play(path)

    assert default_library.alc.current_context is default_library.alc.previous_context
