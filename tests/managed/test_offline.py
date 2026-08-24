"""Tests for managed offline rendering and playback-device control."""

from __future__ import annotations

import pytest

from pyalsoft import (
    PCM,
    AmbisonicLayout,
    AmbisonicScaling,
    AudioBackendError,
    PlaybackOpenError,
    RenderChannelLayout,
    RenderConfig,
    RenderSampleType,
    bindings,
    open_offline_playback,
    open_playback,
    pause_playback_device,
    play,
    render_samples,
    resume_playback_device,
    upload,
)
from tests._support.managed_backend import FakeLibrary, as_library


def test_offline_playback_reuses_managed_resources_and_renders_bytes() -> None:
    library = FakeLibrary()
    config = RenderConfig(
        sample_rate=44_100,
        channels=RenderChannelLayout.STEREO,
        sample_type=RenderSampleType.FLOAT32,
    )

    with open_offline_playback(config, library=as_library(library)) as playback:
        clip = upload(playback, PCM(bytes(200), channels=1, sample_rate=44_100))
        play(playback, clip)
        output = render_samples(playback, 25)

        assert playback.render_config == config
        assert len(output) == 25 * 2 * 4
        assert any(output)
        assert library.alc.rendered_frames == [25]
        assert library.alc.context_attributes == (
            bindings.ALC_FORMAT_CHANNELS_SOFT,
            bindings.ALC_STEREO_SOFT,
            bindings.ALC_FORMAT_TYPE_SOFT,
            bindings.ALC_FLOAT_SOFT,
            bindings.ALC_FREQUENCY,
            44_100,
        )

    assert library.al.sources == {}
    assert library.al.allocated_buffers == set()


def test_offline_bformat_configures_ambisonic_context_attributes() -> None:
    library = FakeLibrary()
    config = RenderConfig(
        channels=RenderChannelLayout.BFORMAT_3D,
        ambisonic_order=5,
        ambisonic_layout=AmbisonicLayout.ACN,
        ambisonic_scaling=AmbisonicScaling.SN3D,
    )

    with open_offline_playback(config, library=as_library(library)) as playback:
        assert len(render_samples(playback, 2)) == 2 * 36 * 2
        assert library.alc.context_attributes is not None
        attributes = dict(
            zip(
                library.alc.context_attributes[::2],
                library.alc.context_attributes[1::2],
                strict=True,
            )
        )
        assert attributes[bindings.ALC_FORMAT_CHANNELS_SOFT] == (
            bindings.ALC_BFORMAT3D_SOFT
        )
        assert attributes[bindings.ALC_AMBISONIC_ORDER_SOFT] == 5
        assert attributes[bindings.ALC_AMBISONIC_LAYOUT_SOFT] == bindings.ALC_ACN_SOFT
        assert attributes[bindings.ALC_AMBISONIC_SCALING_SOFT] == bindings.ALC_SN3D_SOFT


def test_offline_playback_reports_unavailable_or_unsupported_formats() -> None:
    library = FakeLibrary()
    library.alc.extensions.remove("ALC_SOFT_loopback")
    with pytest.raises(PlaybackOpenError, match="ALC_SOFT_loopback"):
        open_offline_playback(library=as_library(library))

    library = FakeLibrary()
    library.alc.render_format_supported = False
    with pytest.raises(PlaybackOpenError, match="requested render format"):
        open_offline_playback(library=as_library(library))


def test_pause_and_resume_playback_device() -> None:
    library = FakeLibrary()
    with open_playback(library=as_library(library)) as playback:
        pause_playback_device(playback)
        resume_playback_device(playback)
        assert library.alc.device_pause_calls == 1
        assert library.alc.device_resume_calls == 1

        library.alc.extensions.remove("ALC_SOFT_pause_device")
        with pytest.raises(AudioBackendError, match="device pause"):
            pause_playback_device(playback)


@pytest.mark.parametrize("frame_count", [0, -1])
def test_render_samples_rejects_non_positive_counts(frame_count: int) -> None:
    library = FakeLibrary()
    with (
        open_offline_playback(library=as_library(library)) as playback,
        pytest.raises(ValueError, match="positive"),
    ):
        render_samples(playback, frame_count)
