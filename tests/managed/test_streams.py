"""Tests for managed streaming playback."""

from __future__ import annotations

import pytest

import pyalsoft._managed.playback.streams as streams_module
from pyalsoft import (
    AmbisonicLayout,
    BufferFormat,
    EffectSend,
    HighPassFilter,
    InvalidHandleError,
    InvalidVoiceStateError,
    PlaybackConfig,
    Reverb,
    SampleType,
    StereoMode,
    StreamState,
    VoiceConfig,
    bindings,
    close_playback,
    finish_stream,
    get_voice_config,
    open_playback,
    open_stream,
    pause,
    reconfigure_playback,
    release,
    release_finished,
    resume,
    set_voice_config,
    start_stream,
    stop,
    try_write_stream,
    update_stream,
    wait,
    write_stream,
)
from tests._support.managed_backend import FakeLibrary, as_library


def test_stream_remains_usable_across_playback_reconfiguration() -> None:
    library = FakeLibrary()

    with open_playback(library=as_library(library)) as playback:
        stream = open_stream(
            playback,
            channels=1,
            sample_rate=10,
            buffer_count=2,
        )
        assert try_write_stream(playback, stream, b"\0\0" * 10)
        start_stream(playback, stream)

        reconfigure_playback(playback, PlaybackConfig(sample_rate=44_100))

        assert try_write_stream(playback, stream, b"\0\0" * 5)
        status = update_stream(playback, stream)
        assert status.state is StreamState.PLAYING
        assert status.queued_chunks == 2
        release(playback, stream)


def test_stream_uses_bounded_reusable_buffers_and_drains_finished_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library = FakeLibrary()
    with open_playback(library=as_library(library)) as playback:
        stream = open_stream(
            playback,
            channels=1,
            sample_rate=10,
            buffer_count=2,
            config=VoiceConfig(
                gain=0.5,
                filter=HighPassFilter(low_frequency_gain=0.5),
                effect_sends=(EffectSend(effect=Reverb(decay_time=0.5)),),
            ),
        )
        assert len(library.al.allocated_buffers) == 2
        assert library.al.sources[100][bindings.AL_GAIN] == 0.5
        assert get_voice_config(playback, stream).gain == 0.5

        first = bytearray(b"\0\0" * 10)
        assert try_write_stream(playback, stream, first)
        first[:] = b"\xff" * len(first)
        assert library.al.buffers[1][1] == b"\0\0" * 10
        assert try_write_stream(playback, stream, b"\0\0" * 5)

        def fail_copy(samples: object) -> bytes:
            del samples
            raise AssertionError("backpressure copied a rejected chunk")

        monkeypatch.setattr(streams_module, "_copy_stream_samples", fail_copy)
        assert not try_write_stream(playback, stream, b"\0\0")

        start_stream(playback, stream)
        status = update_stream(playback, stream)
        assert status.state is StreamState.PLAYING
        assert status.queued_chunks == 2
        assert status.queued_seconds == pytest.approx(1.5)

        finish_stream(playback, stream)
        library.al.processed[100] = 2
        library.al.states[100] = bindings.AL_STOPPED
        status = update_stream(playback, stream)
        assert status.state is StreamState.FINISHED
        assert status.input_finished
        assert status.queued_chunks == 0
        assert status.queued_seconds == 0.0
        assert status.underrun_count == 0

        assert release_finished(playback) == 1
        with pytest.raises(InvalidHandleError, match="released"):
            update_stream(playback, stream)

    assert library.al.allocated_buffers == set()
    assert library.al.allocated_effects == set()
    assert library.al.allocated_effect_slots == set()
    assert library.al.allocated_filters == set()


def test_blocking_stream_write_and_wait_support_timeouts() -> None:
    library = FakeLibrary()
    with open_playback(library=as_library(library)) as playback:
        stream = open_stream(
            playback,
            channels=1,
            sample_rate=10,
            buffer_count=1,
        )
        assert write_stream(playback, stream, b"\0\0" * 2, timeout=0.0)
        assert not write_stream(playback, stream, b"\0\0", timeout=0.0)
        start_stream(playback, stream)
        library.al.processed[100] = 1
        library.al.states[100] = bindings.AL_STOPPED
        assert write_stream(playback, stream, b"\0\0", timeout=0.0)

        finish_stream(playback, stream)
        library.al.processed[100] = 1
        library.al.states[100] = bindings.AL_STOPPED
        assert wait(playback, stream, timeout=0.0)


def test_stream_update_reclaims_offsets_and_counts_underrun_episodes() -> None:
    library = FakeLibrary()
    with open_playback(library=as_library(library)) as playback:
        stream = open_stream(
            playback,
            channels=1,
            sample_rate=10,
            buffer_count=1,
        )
        assert try_write_stream(playback, stream, b"\0\0" * 10)
        start_stream(playback, stream)
        library.al.offsets[100] = 0.25
        assert update_stream(playback, stream).queued_seconds == pytest.approx(0.75)

        library.al.processed[100] = 1
        library.al.states[100] = bindings.AL_STOPPED
        status = update_stream(playback, stream)
        assert status.state is StreamState.PLAYING
        assert status.queued_chunks == 0
        assert status.underrun_count == 1
        assert update_stream(playback, stream).underrun_count == 1

        assert try_write_stream(playback, stream, b"\0\0" * 2)
        assert library.al.play_calls == [100, 100]
        library.al.processed[100] = 1
        library.al.states[100] = bindings.AL_STOPPED
        assert update_stream(playback, stream).underrun_count == 2

        finish_stream(playback, stream)
        status = update_stream(playback, stream)
        assert status.state is StreamState.FINISHED
        assert status.underrun_count == 2
        release(playback, stream)


def test_stream_write_restarts_without_replaying_unreclaimed_chunks() -> None:
    library = FakeLibrary()
    with open_playback(library=as_library(library)) as playback:
        stream = open_stream(
            playback,
            channels=1,
            sample_rate=10,
            buffer_count=2,
        )
        assert try_write_stream(playback, stream, b"\0\0" * 10)
        start_stream(playback, stream)

        library.al.processed[100] = 1
        library.al.states[100] = bindings.AL_STOPPED
        assert try_write_stream(playback, stream, b"\0\0" * 2)

        assert library.al.play_calls == [100, 100]
        assert library.al.sample_offset_calls == [(100, 10)]
        release(playback, stream)


def test_stream_pause_resume_stop_and_looping_rules() -> None:
    library = FakeLibrary()
    with open_playback(library=as_library(library)) as playback:
        with pytest.raises(ValueError, match="cannot loop"):
            open_stream(
                playback,
                channels=2,
                sample_rate=44_100,
                config=VoiceConfig(looping=True),
            )

        stream = open_stream(
            playback,
            channels=2,
            sample_rate=44_100,
            sample_type=SampleType.UINT8,
            buffer_count=2,
        )
        pause(playback, stream)
        assert try_write_stream(playback, stream, b"\0\0")
        start_stream(playback, stream)
        pause(playback, stream)
        assert update_stream(playback, stream).state is StreamState.PAUSED
        finish_stream(playback, stream)
        assert update_stream(playback, stream).state is StreamState.PAUSED
        with pytest.raises(ValueError, match="cannot loop"):
            set_voice_config(playback, stream, VoiceConfig(looping=True))

        resume(playback, stream)
        assert update_stream(playback, stream).state is StreamState.PLAYING
        stop(playback, stream)
        status = update_stream(playback, stream)
        assert status.state is StreamState.STOPPED
        assert status.input_finished
        assert status.queued_chunks == 0
        stop(playback, stream)
        pause(playback, stream)
        with pytest.raises(InvalidVoiceStateError, match="stopped"):
            resume(playback, stream)
        with pytest.raises(InvalidVoiceStateError, match="stopped"):
            finish_stream(playback, stream)
        with pytest.raises(InvalidVoiceStateError, match="stopped"):
            try_write_stream(playback, stream, b"\0\0")
        release(playback, stream)


@pytest.mark.parametrize(
    ("arguments", "exception", "message"),
    [
        ({"channels": 3, "sample_rate": 1}, ValueError, "channels"),
        ({"channels": True, "sample_rate": 1}, TypeError, "integer"),
        ({"channels": 1.0, "sample_rate": 1}, TypeError, "integer"),
        ({"channels": 1, "sample_rate": 0}, ValueError, "positive"),
        ({"channels": 1, "sample_rate": True}, TypeError, "integer"),
        ({"channels": 1, "sample_rate": 1, "buffer_count": 0}, ValueError, "positive"),
        (
            {"channels": 1, "sample_rate": 1, "buffer_count": True},
            TypeError,
            "integer",
        ),
    ],
)
def test_open_stream_rejects_invalid_layouts(
    arguments: dict[str, object], exception: type[Exception], message: str
) -> None:
    library = FakeLibrary()
    with (
        open_playback(library=as_library(library)) as playback,
        pytest.raises(exception, match=message),
    ):
        open_stream(playback, **arguments)  # type: ignore[arg-type]


def test_stream_rejects_invalid_chunks_and_invalid_start_transitions() -> None:
    library = FakeLibrary()
    with open_playback(library=as_library(library)) as playback:
        stream = open_stream(
            playback,
            channels=2,
            sample_rate=1,
            buffer_count=1,
        )
        with pytest.raises(InvalidVoiceStateError, match="without a queued chunk"):
            start_stream(playback, stream)
        with pytest.raises(TypeError, match="bytes-like"):
            try_write_stream(playback, stream, "audio")  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="empty"):
            try_write_stream(playback, stream, b"")
        with pytest.raises(ValueError, match="whole number"):
            try_write_stream(playback, stream, b"\0\0")

        assert try_write_stream(playback, stream, b"\0" * 4)
        assert not try_write_stream(playback, stream, b"\0")
        start_stream(playback, stream)
        with pytest.raises(InvalidVoiceStateError, match="playing"):
            start_stream(playback, stream)
        finish_stream(playback, stream)
        finish_stream(playback, stream)
        with pytest.raises(InvalidVoiceStateError, match="end-of-input"):
            try_write_stream(playback, stream, b"\0" * 4)
        release(playback, stream)


def test_stream_supports_fixed_width_extension_formats() -> None:
    library = FakeLibrary()
    with open_playback(library=as_library(library)) as playback:
        stream = open_stream(
            playback,
            sample_rate=48_000,
            format=BufferFormat.SURROUND_7_1_FLOAT32,
            buffer_count=1,
        )

        assert try_write_stream(playback, stream, bytes(8 * 4 * 2))
        assert library.al.buffers[1][0] == bindings.AL_FORMAT_71CHN32
        assert update_stream(playback, stream).queued_seconds == pytest.approx(
            2 / 48_000
        )
        release(playback, stream)


def test_two_channel_uhj_stream_is_not_treated_as_stereo() -> None:
    library = FakeLibrary()

    with open_playback(library=as_library(library)) as playback:
        with pytest.raises(ValueError, match="Super Stereo processing"):
            open_stream(
                playback,
                sample_rate=48_000,
                format=BufferFormat.UHJ_2_INT16,
                config=VoiceConfig(stereo_mode=StereoMode.SUPER_STEREO),
            )

        assert library.al.sources == {}
        assert library.al.allocated_buffers == set()


def test_encoded_stream_chunks_require_decoded_frame_count() -> None:
    library = FakeLibrary()
    with open_playback(library=as_library(library)) as playback:
        stream = open_stream(
            playback,
            channels=2,
            sample_rate=48_000,
            format=BufferFormat.VORBIS,
            buffer_count=1,
        )

        with pytest.raises(ValueError, match="frame_count is required"):
            try_write_stream(playback, stream, b"OggSencoded")
        assert try_write_stream(
            playback,
            stream,
            b"OggSencoded",
            frame_count=48_000,
        )
        assert update_stream(playback, stream).queued_seconds == 1.0
        release(playback, stream)


def test_stream_configures_ambisonic_properties_on_every_buffer() -> None:
    library = FakeLibrary()
    with open_playback(library=as_library(library)) as playback:
        stream = open_stream(
            playback,
            sample_rate=48_000,
            format=BufferFormat.BFORMAT_3D_INT16,
            ambisonic_order=2,
            ambisonic_layout=AmbisonicLayout.ACN,
            buffer_count=2,
        )

        expected = {
            bindings.AL_AMBISONIC_LAYOUT_SOFT: bindings.AL_ACN_SOFT,
            bindings.AL_UNPACK_AMBISONIC_ORDER_SOFT: 2,
        }
        assert library.al.buffer_properties == {1: expected, 2: expected}
        assert try_write_stream(playback, stream, bytes(9 * 2), frame_count=1)
        release(playback, stream)


def test_primed_finished_stream_can_start_and_close_releases_all_buffers() -> None:
    library = FakeLibrary()
    playback = open_playback(library=as_library(library))
    stream = open_stream(
        playback,
        channels=1,
        sample_rate=1,
        buffer_count=3,
    )
    assert try_write_stream(playback, stream, b"\0\0")
    finish_stream(playback, stream)
    start_stream(playback, stream)

    close_playback(playback)

    assert library.al.sources == {}
    assert library.al.allocated_buffers == set()


def test_failed_stream_creation_releases_partial_native_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library = FakeLibrary()
    failure = RuntimeError("stream configuration failed")

    def fail_configuration(
        _playback: object,
        _identifier: int,
        _config: VoiceConfig,
    ) -> None:
        raise failure

    with open_playback(library=as_library(library)) as playback:
        monkeypatch.setattr(
            streams_module,
            "_apply_voice_config",
            fail_configuration,
        )

        with pytest.raises(RuntimeError, match="stream configuration failed") as caught:
            open_stream(playback, channels=1, sample_rate=8_000)

        assert caught.value is failure
        assert library.al.sources == {}
        assert library.al.allocated_buffers == set()
        assert playback._streams == {}
