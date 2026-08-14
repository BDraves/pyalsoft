"""Tests for managed audio values and validation."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from pyalsoft import (
    PCM,
    Listener,
    PlaybackConfig,
    PlaybackDevice,
    SampleType,
    SoundInfo,
    VoiceConfig,
)


def test_pcm_and_configuration_are_immutable_data() -> None:
    pcm = PCM(
        samples=b"\x00\x00\x01\x00",
        channels=1,
        sample_rate=2,
        sample_type=SampleType.INT16,
    )
    config = VoiceConfig(position=(1, 2, 3))
    playback_config = PlaybackConfig(hrtf=True)
    device = PlaybackDevice("USB Headset", is_default=True)

    assert pcm.frame_count == 2
    assert pcm.duration == 1.0
    assert config.position == (1.0, 2.0, 3.0)
    assert playback_config.hrtf is True
    assert device.is_default
    assert replace(config, position=(4.0, 5.0, 6.0)).position == (
        4.0,
        5.0,
        6.0,
    )
    with pytest.raises(FrozenInstanceError):
        pcm.channels = 2  # type: ignore[misc]
    with pytest.raises(TypeError):
        VoiceConfig((1.0, 2.0, 3.0))  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        Listener((1.0, 2.0, 3.0))  # type: ignore[call-arg]
    with pytest.raises(FrozenInstanceError):
        playback_config.hrtf = False  # type: ignore[misc]
    with pytest.raises(TypeError, match="boolean or None"):
        PlaybackConfig(hrtf=1)  # type: ignore[arg-type]
    assert pcm.info == SoundInfo(
        channels=1,
        sample_rate=2,
        sample_type=SampleType.INT16,
        frame_count=2,
    )
    assert pcm.info.duration_seconds == 1.0
    assert pcm.info.bit_depth == 16
    assert pcm.info.byte_count == 4


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"samples": b"", "channels": 1, "sample_rate": 1}, "cannot be empty"),
        ({"samples": b"\0\0", "channels": 3, "sample_rate": 1}, "channels"),
        ({"samples": b"\0\0", "channels": 1, "sample_rate": 0}, "positive"),
        (
            {"samples": b"\0", "channels": 1, "sample_rate": 1},
            "whole number of frames",
        ),
    ],
)
def test_pcm_rejects_invalid_layouts(
    arguments: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        PCM(**arguments)  # type: ignore[arg-type]


@pytest.mark.parametrize("channels", [True, 1.0])
def test_pcm_and_sound_info_require_integer_channels(channels: object) -> None:
    with pytest.raises(TypeError, match="channels must be an integer"):
        PCM(b"\0\0", channels=channels, sample_rate=1)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="channels must be an integer"):
        SoundInfo(
            channels=channels,  # type: ignore[arg-type]
            sample_rate=1,
            sample_type=SampleType.INT16,
            frame_count=1,
        )
