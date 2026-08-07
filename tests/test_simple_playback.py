"""Tests for convenient file playback and its implicit runtime."""

from __future__ import annotations

import wave
from collections.abc import Iterator
from pathlib import Path

import pytest

import pyalsoft._playback as playback
from pyalsoft import (
    AudioFileError,
    InvalidVoiceStateError,
    PlayingSound,
    VoiceConfig,
    VoiceState,
    bindings,
    open_playback,
    play,
    shutdown,
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

    sound.seek(0.00075)
    assert sound.offset_seconds == pytest.approx(0.00075)
    sound.pause()
    sound.rewind()
    assert sound.paused
    assert sound.offset_seconds == 0.0
    sound.restart()
    assert sound.playing
    assert sound.offset_seconds == 0.0


def test_playing_sound_validates_seeks_and_can_restart_after_completion(
    tmp_path: Path,
    default_library: FakeLibrary,
) -> None:
    path = tmp_path / "restart.wav"
    _write_wave(path)
    sound = play(path, gain=0.4)

    with pytest.raises(ValueError, match="at least 0.0"):
        sound.seek(-0.1)
    with pytest.raises(ValueError, match="less than the sound duration"):
        sound.seek(sound.duration_seconds)

    default_library.al.states[100] = bindings.AL_STOPPED
    assert sound.finished
    assert sound.done
    assert sound.stopped
    assert sound.offset_seconds == sound.duration_seconds

    sound.restart()

    assert sound.playing
    assert not sound.finished
    assert sound.gain == 0.4
    assert set(default_library.al.sources) == {101}
    assert default_library.al.sources[101][bindings.AL_GAIN] == 0.4


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
    sound.stop()


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
