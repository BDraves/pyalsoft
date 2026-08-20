"""Tests for managed audio values and validation."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from pyalsoft import (
    PCM,
    AmbisonicLayout,
    AmbisonicScaling,
    BufferData,
    BufferFormat,
    Listener,
    PlaybackConfig,
    PlaybackDevice,
    PlaybackOutputMode,
    SampleType,
    SoundInfo,
    VoiceConfig,
)


def test_buffer_data_describes_fixed_and_encoded_formats() -> None:
    surround = BufferData(
        samples=bytes(2 * 6 * 4),
        format=BufferFormat.SURROUND_5_1_FLOAT32,
        sample_rate=48_000,
        frame_count=2,
    )
    vorbis = BufferData(
        samples=b"OggSencoded",
        format=BufferFormat.VORBIS,
        channels=2,
        sample_rate=44_100,
        frame_count=44_100,
    )

    assert surround.channels == 6
    assert surround.info.sample_type is SampleType.FLOAT32
    assert surround.info.byte_count == 48
    assert vorbis.duration == 1.0
    assert vorbis.info.sample_type is None


def test_every_managed_buffer_format_maps_to_a_native_format() -> None:
    assert len(BufferFormat) == 67
    assert all(format.native_format for format in BufferFormat)
    assert BufferFormat.MONO_INT16.required_extensions == ()
    assert BufferFormat.MONO_MULAW.required_extensions == (
        "AL_EXT_MULAW",
        "AL_EXT_MULAW_MCFORMATS",
    )


def test_buffer_data_validates_ambisonic_metadata_and_byte_size() -> None:
    data = BufferData(
        samples=bytes(16 * 4 * 4),
        format=BufferFormat.BFORMAT_3D_FLOAT32,
        sample_rate=48_000,
        frame_count=4,
        ambisonic_order=3,
        ambisonic_layout=AmbisonicLayout.ACN,
        ambisonic_scaling=AmbisonicScaling.SN3D,
    )

    assert data.channels == 16
    with pytest.raises(ValueError, match="exactly 2 complete frames"):
        BufferData(
            samples=b"too short",
            format=BufferFormat.STEREO_INT16,
            sample_rate=1,
            frame_count=2,
        )
    with pytest.raises(ValueError, match="channels is required"):
        BufferData(
            samples=b"OggS",
            format=BufferFormat.VORBIS,
            sample_rate=1,
            frame_count=1,
        )
    with pytest.raises(ValueError, match="multiple of 8 plus 1"):
        BufferData(
            samples=bytes(36),
            format=BufferFormat.MONO_IMA4,
            sample_rate=1,
            frame_count=64,
            block_alignment=64,
        )


def test_higher_order_ambisonics_require_explicit_non_fuma_metadata() -> None:
    third_order = BufferData(
        samples=bytes(16 * 2),
        format=BufferFormat.BFORMAT_3D_INT16,
        sample_rate=48_000,
        frame_count=1,
        ambisonic_order=3,
    )

    assert third_order.channels == 16
    for layout in (None, AmbisonicLayout.FUMA):
        with pytest.raises(ValueError, match="orders above 3 require ACN layout"):
            BufferData(
                samples=bytes(9 * 2),
                format=BufferFormat.BFORMAT_2D_INT16,
                sample_rate=48_000,
                frame_count=1,
                ambisonic_order=4,
                ambisonic_layout=layout,
                ambisonic_scaling=AmbisonicScaling.SN3D,
            )
    for scaling in (None, AmbisonicScaling.FUMA):
        with pytest.raises(ValueError, match="orders above 3 require SN3D or N3D"):
            BufferData(
                samples=bytes(9 * 2),
                format=BufferFormat.BFORMAT_2D_INT16,
                sample_rate=48_000,
                frame_count=1,
                ambisonic_order=4,
                ambisonic_layout=AmbisonicLayout.ACN,
                ambisonic_scaling=scaling,
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
    ("channels", "sample_type", "byte_width"),
    [
        (1, SampleType.FLOAT32, 4),
        (2, SampleType.FLOAT64, 8),
        (4, SampleType.INT16, 2),
        (6, SampleType.FLOAT32, 4),
        (7, SampleType.UINT8, 1),
        (8, SampleType.INT16, 2),
    ],
)
def test_pcm_supports_float_and_surround_layouts(
    channels: int, sample_type: SampleType, byte_width: int
) -> None:
    pcm = PCM(
        bytes(channels * byte_width),
        channels=channels,
        sample_rate=48_000,
        sample_type=sample_type,
    )

    assert pcm.frame_count == 1


@pytest.mark.parametrize(
    ("arguments", "error", "message"),
    [
        ({"sample_rate": True}, TypeError, "sample_rate must be an integer"),
        ({"sample_rate": 0}, ValueError, "sample_rate must be between 1"),
        ({"refresh_rate": 2**31}, ValueError, "refresh_rate must be between 1"),
        ({"mono_sources": -1}, ValueError, "mono_sources must be between 0"),
        ({"stereo_sources": 1.0}, TypeError, "stereo_sources must be an integer"),
        (
            {"max_auxiliary_sends": -1},
            ValueError,
            "max_auxiliary_sends must be between 0",
        ),
        ({"hrtf_name": 1}, TypeError, "hrtf_name must be a string"),
        ({"hrtf_name": ""}, ValueError, "hrtf_name cannot be empty"),
        ({"synchronous": 1}, TypeError, "synchronous must be a boolean"),
        ({"output_limiter": 1}, TypeError, "output_limiter must be a boolean"),
        (
            {"output_mode": "stereo"},
            TypeError,
            "output_mode must be a PlaybackOutputMode",
        ),
        (
            {"output_mode": PlaybackOutputMode.UNKNOWN},
            ValueError,
            "output_mode cannot be",
        ),
    ],
)
def test_playback_config_rejects_invalid_requests(
    arguments: dict[str, object],
    error: type[Exception],
    message: str,
) -> None:
    with pytest.raises(error, match=message):
        PlaybackConfig(**arguments)  # type: ignore[arg-type]


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
