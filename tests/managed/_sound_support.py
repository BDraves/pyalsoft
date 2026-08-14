"""Helpers shared by convenience-playback tests."""

import wave
from pathlib import Path

from pyalsoft import PlayingSound, VoiceState


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
