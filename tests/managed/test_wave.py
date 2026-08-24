"""Tests for convenient file playback and its implicit runtime."""

from __future__ import annotations

import wave
from pathlib import Path

import pytest

from pyalsoft import (
    AudioFileError,
    SampleType,
    get_sound_info,
    load_audio,
    play,
)
from tests._support.managed_backend import FakeLibrary
from tests.managed._sound_support import _write_wave


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


def test_load_audio_decodes_wave_without_opening_a_device(tmp_path: Path) -> None:
    path = tmp_path / "load.wav"
    _write_wave(path, channels=2, sample_rate=44_100)

    pcm = load_audio(path)

    assert pcm.info == get_sound_info(path)
    assert pcm.samples == b"\0\0" * 16


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
