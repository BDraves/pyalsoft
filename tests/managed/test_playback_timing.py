"""Tests for delayed, scheduled, and transactionally updated playback."""

from __future__ import annotations

from threading import Thread

import pytest

from pyalsoft import (
    PCM,
    AudioBackendError,
    Listener,
    bindings,
    defer_updates,
    get_playback_clock,
    open_playback,
    open_stream,
    play,
    restart,
    set_listener,
    start_stream,
    try_write_stream,
    upload,
)
from tests._support.managed_backend import FakeLibrary, as_library


def test_voices_and_streams_support_delayed_and_scheduled_starts() -> None:
    library = FakeLibrary()
    with open_playback(library=as_library(library)) as playback:
        clip = upload(playback, PCM(bytes(32), channels=1, sample_rate=8))
        by_seconds = play(playback, clip, delay_seconds=0.25)
        by_frames = play(playback, clip, delay_frames=3)
        start_time = get_playback_clock(playback).device_time_ns + 500_000_000
        scheduled = play(playback, clip, start_time_ns=start_time)
        stream = open_stream(playback, channels=1, sample_rate=8)
        assert try_write_stream(playback, stream, bytes(16))
        start_stream(
            playback,
            stream,
            delay_seconds=0.125,
            start_time_ns=start_time + 250_000_000,
        )

        assert library.al.sources[100][bindings.AL_SEC_OFFSET] == -0.25
        assert library.al.sources[101][bindings.AL_SAMPLE_OFFSET] == -3
        assert library.al.sources[103][bindings.AL_SEC_OFFSET] == -0.125
        assert library.al.scheduled_play_calls == [
            (102, start_time),
            (103, start_time + 250_000_000),
        ]

        restart(
            playback,
            by_seconds,
            delay_frames=2,
            start_time_ns=start_time + 1_000_000_000,
        )
        assert library.al.sources[100][bindings.AL_SAMPLE_OFFSET] == -2
        assert library.al.scheduled_play_calls[-1] == (
            100,
            start_time + 1_000_000_000,
        )
        assert by_frames is not None
        assert scheduled is not None


@pytest.mark.parametrize(
    ("arguments", "error", "message"),
    [
        ({"delay_seconds": -0.1}, ValueError, "delay_seconds cannot be negative"),
        ({"delay_frames": 1.5}, TypeError, "delay_frames must be an integer"),
        (
            {"delay_frames": (1 << 31) + 1},
            ValueError,
            "delay_frames cannot exceed",
        ),
        (
            {"delay_seconds": 0.5, "delay_frames": 1},
            ValueError,
            "delay_seconds and delay_frames cannot both be set",
        ),
        (
            {"offset_seconds": 0.5, "delay_seconds": 0.5},
            ValueError,
            "initial offset and playback delay cannot both be set",
        ),
        (
            {"offset_seconds": 0.5, "delay_frames": 0},
            ValueError,
            "initial offset and playback delay cannot both be set",
        ),
        ({"start_time_ns": True}, TypeError, "start_time_ns must be an integer"),
        ({"start_time_ns": -1}, ValueError, "start_time_ns must be between"),
    ],
)
def test_playback_timing_arguments_are_validated(
    arguments: dict[str, object],
    error: type[Exception],
    message: str,
) -> None:
    library = FakeLibrary()
    with open_playback(library=as_library(library)) as playback:
        clip = upload(playback, PCM(bytes(32), channels=1, sample_rate=8))

        with pytest.raises(error, match=message):
            play(playback, clip, **arguments)  # type: ignore[call-overload]

        assert library.al.sources == {}


@pytest.mark.parametrize(
    "arguments",
    [{"delay_seconds": 0.25}, {"delay_frames": 2}, {"start_time_ns": 1}],
)
def test_timed_playback_requires_source_start_delay_extension(
    arguments: dict[str, object],
) -> None:
    library = FakeLibrary()
    library.al_extensions.discard("AL_SOFT_source_start_delay")
    with open_playback(library=as_library(library)) as playback:
        clip = upload(playback, PCM(bytes(16), channels=1, sample_rate=8))

        with pytest.raises(AudioBackendError, match="AL_SOFT_source_start_delay"):
            play(playback, clip, **arguments)  # type: ignore[call-overload]

        assert library.al.sources == {}


def test_timed_stream_start_requires_source_start_delay_extension() -> None:
    library = FakeLibrary()
    library.al_extensions.discard("AL_SOFT_source_start_delay")
    with open_playback(library=as_library(library)) as playback:
        stream = open_stream(playback, channels=1, sample_rate=8)
        assert try_write_stream(playback, stream, bytes(16))

        with pytest.raises(AudioBackendError, match="AL_SOFT_source_start_delay"):
            start_stream(playback, stream, start_time_ns=1)

        assert library.al.states[100] == bindings.AL_INITIAL


def test_deferred_updates_commit_once_for_nested_blocks_and_exceptions() -> None:
    library = FakeLibrary()
    with open_playback(library=as_library(library)) as playback:
        with defer_updates(playback):
            assert library.al.updates_deferred
            set_listener(playback, Listener(position=(1.0, 2.0, 3.0)))
            with defer_updates(playback):
                assert library.al.defer_update_calls == 1
                assert library.al.process_update_calls == 0

        assert not library.al.updates_deferred
        assert library.al.defer_update_calls == 1
        assert library.al.process_update_calls == 1

        with (
            pytest.raises(RuntimeError, match="body failed"),
            defer_updates(playback),
        ):
            raise RuntimeError("body failed")

        assert not library.al.updates_deferred
        assert library.al.process_update_calls == 2


def test_deferred_updates_do_not_hold_playback_locks_across_the_body() -> None:
    library = FakeLibrary()
    errors: list[BaseException] = []

    with open_playback(library=as_library(library)) as playback:

        def update_listener() -> None:
            try:
                set_listener(playback, Listener(position=(1.0, 2.0, 3.0)))
            except BaseException as error:
                errors.append(error)

        with defer_updates(playback):
            worker = Thread(target=update_listener)
            worker.start()
            worker.join(timeout=1.0)
            completed_while_deferred = not worker.is_alive()

        worker.join()

    assert completed_while_deferred
    assert not errors


def test_deferred_updates_require_extension() -> None:
    library = FakeLibrary()
    library.al_extensions.discard("AL_SOFT_deferred_updates")
    with open_playback(library=as_library(library)) as playback:
        with (
            pytest.raises(AudioBackendError, match="AL_SOFT_deferred_updates"),
            defer_updates(playback),
        ):
            pass

        assert library.al.defer_update_calls == 0
        assert library.al.process_update_calls == 0


def test_deferred_update_commit_errors_preserve_a_body_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library = FakeLibrary()
    process_updates = library.al.process_updates_soft

    def fail_process_updates() -> None:
        process_updates()
        library.al.error = bindings.AL_INVALID_OPERATION

    with open_playback(library=as_library(library)) as playback:
        monkeypatch.setattr(library.al, "process_updates_soft", fail_process_updates)

        body_error = RuntimeError("body failed")
        with (
            pytest.raises(RuntimeError, match="body failed") as caught,
            defer_updates(playback),
        ):
            raise body_error

        assert caught.value is body_error
        assert caught.value.__notes__ is not None
        assert (
            "processing deferred playback updates also failed"
            in caught.value.__notes__[0]
        )


def test_convenience_playback_exposes_timing_and_deferred_updates(
    default_library: FakeLibrary,
) -> None:
    start_time = 43_000_000_000
    sound = play(
        PCM(bytes(32), channels=1, sample_rate=8),
        delay_frames=2,
        start_time_ns=start_time,
    )

    assert default_library.al.sources[100][bindings.AL_SAMPLE_OFFSET] == -2
    assert default_library.al.scheduled_play_calls == [(100, start_time)]

    with defer_updates():
        sound.gain = 0.5
        set_listener(Listener(position=(3.0, 2.0, 1.0)))
        assert default_library.al.updates_deferred

    assert not default_library.al.updates_deferred
    assert default_library.al.defer_update_calls == 1
    assert default_library.al.process_update_calls == 1

    sound.stop()
    sound.restart(delay_seconds=0.125, start_time_ns=start_time + 1_000_000_000)
    assert default_library.al.sources[101][bindings.AL_SEC_OFFSET] == -0.125
    assert default_library.al.scheduled_play_calls[-1] == (
        101,
        start_time + 1_000_000_000,
    )


def test_convenience_deferred_updates_do_not_hold_the_runtime_lock(
    default_library: FakeLibrary,
) -> None:
    errors: list[BaseException] = []

    def update_listener() -> None:
        try:
            set_listener(Listener(position=(1.0, 2.0, 3.0)))
        except BaseException as error:
            errors.append(error)

    with defer_updates():
        worker = Thread(target=update_listener)
        worker.start()
        worker.join(timeout=1.0)
        completed_while_deferred = not worker.is_alive()

    worker.join()

    assert completed_while_deferred
    assert not errors
    assert default_library.al.defer_update_calls == 1
    assert default_library.al.process_update_calls == 1
