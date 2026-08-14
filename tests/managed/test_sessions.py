"""Tests for explicit managed playback sessions."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from pyalsoft import (
    PCM,
    Clip,
    EffectSend,
    InvalidHandleError,
    PlaybackClosedError,
    PlaybackOpenError,
    Reverb,
    bindings,
    close_playback,
    open_playback,
    play,
    stop,
    upload,
)
from tests._support.managed_backend import FakeLibrary, as_library


def test_open_playback_wraps_library_loading_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = bindings.LibraryNotFoundError("missing")

    def fail_to_load() -> bindings.OpenALLibrary:
        raise failure

    monkeypatch.setattr(bindings, "load", fail_to_load)

    with pytest.raises(PlaybackOpenError) as caught:
        open_playback()
    assert caught.value.__cause__ is failure


def test_close_releases_live_resources_and_is_idempotent() -> None:
    library = FakeLibrary()
    playback = open_playback(library=as_library(library))
    clip = upload(playback, PCM(b"\0\0", channels=1, sample_rate=1))
    play(
        playback,
        clip,
        effect_sends=(EffectSend(effect=Reverb(decay_time=0.5)),),
    )

    close_playback(playback)
    close_playback(playback)

    assert library.al.sources == {}
    assert library.al.buffers == {}
    assert library.al.allocated_effects == set()
    assert library.al.allocated_effect_slots == set()
    assert library.alc.destroyed_contexts == [library.alc.context]
    with pytest.raises(PlaybackClosedError):
        upload(playback, PCM(b"\0\0", channels=1, sample_rate=1))


def test_close_finishes_native_teardown_after_resource_cleanup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library = FakeLibrary()
    playback = open_playback(library=as_library(library))
    clip = upload(playback, PCM(b"\0\0", channels=1, sample_rate=1))
    play(playback, clip)
    failure = RuntimeError("source cleanup failed")

    def fail_source_cleanup(_sources: tuple[int, ...]) -> None:
        raise failure

    monkeypatch.setattr(library.al, "source_stopv", fail_source_cleanup)

    with pytest.raises(RuntimeError, match="source cleanup failed") as caught:
        close_playback(playback)

    assert caught.value is failure
    assert playback._closed
    assert playback._voices == {}
    assert playback._clips == {}
    assert library.alc.destroyed_contexts == [library.alc.context]
    assert library.alc.closed_devices == [library.alc.device]
    assert library.alc.current_context is library.alc.previous_context


def test_playback_sessions_can_close_out_of_opening_order() -> None:
    library = FakeLibrary()
    original_context = library.alc.context
    previous_context = library.alc.previous_context
    first = open_playback(library=as_library(library))
    second_context = object()
    library.alc.context = second_context
    second = open_playback(library=as_library(library))

    close_playback(first)

    assert library.alc.current_context is second_context

    close_playback(second)

    assert library.alc.current_context is previous_context
    assert library.alc.destroyed_contexts == [original_context, second_context]


def test_handles_cannot_cross_playback_sessions() -> None:
    first_library = FakeLibrary()
    second_library = FakeLibrary()
    first = open_playback(library=as_library(first_library))
    second = open_playback(library=as_library(second_library))
    try:
        clip = upload(first, PCM(b"\0\0", channels=1, sample_rate=1))
        with pytest.raises(InvalidHandleError, match="does not belong"):
            play(second, clip)
    finally:
        close_playback(second)
        close_playback(first)


def test_playback_does_not_enforce_thread_ownership() -> None:
    library = FakeLibrary()
    playback = open_playback(library=as_library(library))
    pcm = PCM(b"\0\0", channels=1, sample_rate=1)

    with ThreadPoolExecutor(max_workers=1) as executor:
        clip = executor.submit(upload, playback, pcm).result()
        voice = executor.submit(play, playback, clip).result()
        executor.submit(stop, playback, voice).result()
        executor.submit(close_playback, playback).result()

    assert library.al.sources == {}
    assert library.al.buffers == {}


def test_playback_serializes_complete_operations_across_threads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library = FakeLibrary()
    playback = open_playback(library=as_library(library))
    pcm = PCM(b"\0\0", channels=1, sample_rate=1)
    first_upload_entered = Event()
    allow_first_upload = Event()
    second_call_started = Event()
    second_allocation_entered = Event()
    allocation_count = 0
    original_gen_buffers = library.al.gen_buffers
    original_buffer_data = library.al.buffer_data

    def observed_gen_buffers(count: int = 1) -> tuple[int, ...]:
        nonlocal allocation_count
        allocation_count += 1
        if allocation_count == 2:
            second_allocation_entered.set()
        return original_gen_buffers(count)

    def blocking_buffer_data(
        identifier: int,
        format_name: int,
        data: bytes,
        sample_rate: int,
    ) -> None:
        if identifier == 1:
            first_upload_entered.set()
            if not allow_first_upload.wait(2.0):
                raise AssertionError("timed out waiting to finish the first upload")
        original_buffer_data(identifier, format_name, data, sample_rate)

    monkeypatch.setattr(library.al, "gen_buffers", observed_gen_buffers)
    monkeypatch.setattr(library.al, "buffer_data", blocking_buffer_data)

    def upload_second_clip() -> Clip:
        second_call_started.set()
        return upload(playback, pcm)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(upload, playback, pcm)
            assert first_upload_entered.wait(1.0)
            second = executor.submit(upload_second_clip)

            assert second_call_started.wait(1.0)
            assert not second_allocation_entered.wait(0.1)
            allow_first_upload.set()
            first.result()
            second.result()

        assert second_allocation_entered.is_set()
        assert library.al.allocated_buffers == {1, 2}
    finally:
        allow_first_upload.set()
        close_playback(playback)


def test_playback_sessions_sharing_a_library_serialize_context_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library = FakeLibrary()
    first_context = library.alc.context
    first_playback = open_playback(library=as_library(library))
    second_context = object()
    library.alc.context = second_context
    second_playback = open_playback(library=as_library(library))
    pcm = PCM(b"\0\0", channels=1, sample_rate=1)
    first_upload_entered = Event()
    allow_first_upload = Event()
    second_call_started = Event()
    second_context_activated = Event()
    original_buffer_data = library.al.buffer_data
    original_make_context_current = library.alc.make_context_current

    def blocking_buffer_data(
        identifier: int,
        format_name: int,
        data: bytes,
        sample_rate: int,
    ) -> None:
        if identifier == 1:
            first_upload_entered.set()
            if not allow_first_upload.wait(2.0):
                raise AssertionError("timed out waiting to finish the first upload")
        original_buffer_data(identifier, format_name, data, sample_rate)

    def observe_context_activation(context: object | None) -> bool:
        if context is second_context:
            second_context_activated.set()
        return original_make_context_current(context)

    def upload_to_second_playback() -> Clip:
        second_call_started.set()
        return upload(second_playback, pcm)

    monkeypatch.setattr(library.al, "buffer_data", blocking_buffer_data)
    monkeypatch.setattr(
        library.alc,
        "make_context_current",
        observe_context_activation,
    )

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(upload, first_playback, pcm)
            assert first_upload_entered.wait(1.0)
            second = executor.submit(upload_to_second_playback)

            assert second_call_started.wait(1.0)
            assert library.alc.current_context is first_context
            assert not second_context_activated.wait(0.1)
            allow_first_upload.set()
            first.result()
            second.result()

        assert second_context_activated.is_set()
        assert library.alc.current_context is second_context
    finally:
        allow_first_upload.set()
        close_playback(second_playback)
        close_playback(first_playback)
