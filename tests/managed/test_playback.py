"""Tests for explicit managed playback sessions."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event

import pytest

import pyalsoft._managed.playback as playback_module
from pyalsoft import (
    PCM,
    Acoustics,
    AudioBackendError,
    Clip,
    DistanceModel,
    EffectSend,
    HighPassFilter,
    InvalidHandleError,
    InvalidVoiceStateError,
    Listener,
    LowPassFilter,
    PlaybackClosedError,
    PlaybackOpenError,
    ResourceInUseError,
    Reverb,
    VoiceConfig,
    VoiceState,
    VoiceStatus,
    bindings,
    close_playback,
    get_acoustics,
    get_listener,
    get_voice_status,
    open_playback,
    open_stream,
    pause,
    play,
    release,
    release_finished,
    restart,
    resume,
    rewind,
    seek,
    seek_frames,
    set_acoustics,
    set_listener,
    set_voice_config,
    stop,
    upload,
)
from tests._support.managed_backend import FakeLibrary, as_library


def test_managed_playback_applies_data_and_controls_lifecycle() -> None:
    library = FakeLibrary()
    pcm = PCM(b"\0\0" * 10, channels=1, sample_rate=10)
    config = VoiceConfig(position=(1.0, 2.0, 3.0), gain=0.5)
    listener = Listener(position=(4.0, 5.0, 6.0))

    with open_playback(library=as_library(library)) as playback:
        clip = upload(playback, pcm)
        voice = play(playback, clip, config)

        set_listener(playback, listener)
        acoustics = Acoustics(
            distance_model=DistanceModel.LINEAR_CLAMPED,
            doppler_factor=0.5,
            speed_of_sound=300.0,
        )
        set_acoustics(playback, acoustics)
        status = get_voice_status(playback, voice)
        assert status.state is VoiceState.PLAYING
        assert status.offset_seconds == 0.25
        assert library.al.sources[100][bindings.AL_POSITION] == (
            1.0,
            2.0,
            3.0,
        )
        assert library.al.sources[100][bindings.AL_GAIN] == 0.5
        assert clip.info == pcm.info
        assert clip.duration_seconds == 1.0
        assert clip.frame_count == 10
        assert get_listener(playback) == listener
        assert get_acoustics(playback) == acoustics
        assert library.al.listener[bindings.AL_ORIENTATION] == (
            0.0,
            0.0,
            -1.0,
            0.0,
            1.0,
            0.0,
        )

        set_voice_config(playback, voice, replace(config, position=(7.0, 8.0, 9.0)))
        assert library.al.sources[100][bindings.AL_POSITION] == (
            7.0,
            8.0,
            9.0,
        )

        pause(playback, voice)
        assert get_voice_status(playback, voice).state is VoiceState.PAUSED
        resume(playback, voice)
        assert get_voice_status(playback, voice).state is VoiceState.PLAYING
        with pytest.raises(InvalidVoiceStateError, match="playing"):
            resume(playback, voice)
        stop(playback, voice)
        assert get_voice_status(playback, voice).state is VoiceState.STOPPED
        with pytest.raises(InvalidVoiceStateError, match="stopped"):
            resume(playback, voice)

        with pytest.raises(ResourceInUseError):
            release(playback, clip)
        release(playback, voice)
        with pytest.raises(InvalidHandleError, match="released"):
            get_voice_status(playback, voice)
        release(playback, clip)

    assert library.al.sources == {}
    assert library.al.buffers == {}
    assert library.alc.destroyed_contexts == [library.alc.context]
    assert library.alc.closed_devices == [library.alc.device]
    assert library.alc.current_context is library.alc.previous_context


def test_failed_clip_upload_releases_the_native_buffer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library = FakeLibrary()
    failure = RuntimeError("upload failed")

    def fail_upload(
        _identifier: int,
        _format_name: int,
        _data: bytes,
        _sample_rate: int,
    ) -> None:
        raise failure

    with open_playback(library=as_library(library)) as playback:
        monkeypatch.setattr(library.al, "buffer_data", fail_upload)

        with pytest.raises(RuntimeError, match="upload failed") as caught:
            upload(playback, PCM(b"\0\0", channels=1, sample_rate=1))

        assert caught.value is failure
        assert library.al.allocated_buffers == set()
        assert playback._clips == {}


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
            playback_module,
            "_apply_voice_config",
            fail_configuration,
        )

        with pytest.raises(RuntimeError, match="stream configuration failed") as caught:
            open_stream(playback, channels=1, sample_rate=8_000)

        assert caught.value is failure
        assert library.al.sources == {}
        assert library.al.allocated_buffers == set()
        assert playback._streams == {}


def test_voice_efx_are_created_replaced_and_released_with_the_voice() -> None:
    library = FakeLibrary()
    reverb = Reverb(gain=0.2, decay_time=0.6, high_frequency_decay_ratio=0.8)
    with open_playback(library=as_library(library)) as playback:
        clip = upload(playback, PCM(b"\0\0" * 10, channels=1, sample_rate=10))
        voice = play(
            playback,
            clip,
            filter=LowPassFilter(high_frequency_gain=0.1),
            effect_sends=(
                EffectSend(
                    effect=reverb,
                    filter=HighPassFilter(low_frequency_gain=0.25),
                ),
            ),
        )

        assert library.al.sources[100][bindings.AL_DIRECT_FILTER] == 300
        assert library.al.filters[300] == {
            bindings.AL_FILTER_TYPE: bindings.AL_FILTER_LOWPASS,
            bindings.AL_LOWPASS_GAIN: 1.0,
            bindings.AL_LOWPASS_GAINHF: 0.1,
        }
        assert library.al.effects[200][bindings.AL_EFFECT_TYPE] == (
            bindings.AL_EFFECT_REVERB
        )
        assert library.al.effects[200][bindings.AL_REVERB_GAIN] == 0.2
        assert library.al.effects[200][bindings.AL_REVERB_DECAY_TIME] == 0.6
        assert library.al.effect_slots[400] == {bindings.AL_EFFECTSLOT_EFFECT: 200}
        assert library.al.source_sends[(100, 0)] == (400, 301)
        assert library.al.filters[301][bindings.AL_FILTER_TYPE] == (
            bindings.AL_FILTER_HIGHPASS
        )

        set_voice_config(
            playback,
            voice,
            VoiceConfig(filter=HighPassFilter(low_frequency_gain=0.1)),
        )

        assert library.al.sources[100][bindings.AL_DIRECT_FILTER] == 302
        assert library.al.source_sends[(100, 0)] == (
            bindings.AL_EFFECTSLOT_NULL,
            bindings.AL_FILTER_NULL,
        )
        assert library.al.allocated_effects == set()
        assert library.al.allocated_effect_slots == set()
        assert library.al.allocated_filters == {302}

        set_voice_config(playback, voice, VoiceConfig())
        assert library.al.sources[100][bindings.AL_DIRECT_FILTER] == (
            bindings.AL_FILTER_NULL
        )
        assert library.al.allocated_filters == set()
        release(playback, voice)
        release(playback, clip)

    assert library.al.allocated_effects == set()
    assert library.al.allocated_effect_slots == set()
    assert library.al.allocated_filters == set()


def test_failed_voice_efx_update_restores_config_and_native_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library = FakeLibrary()
    previous = VoiceConfig(
        gain=0.75,
        effect_sends=(EffectSend(effect=Reverb(decay_time=0.5)),),
    )
    replacement = VoiceConfig(
        gain=0.25,
        effect_sends=(EffectSend(effect=Reverb(decay_time=1.0)),),
    )
    with open_playback(library=as_library(library)) as playback:
        clip = upload(playback, PCM(b"\0\0" * 10, channels=1, sample_rate=10))
        voice = play(playback, clip, previous)
        original_source3i = library.al.source3i
        fail_next_attachment = True

        def fail_one_attachment(
            identifier: int,
            parameter: int,
            value1: int,
            value2: int,
            value3: int,
        ) -> None:
            nonlocal fail_next_attachment
            original_source3i(identifier, parameter, value1, value2, value3)
            if fail_next_attachment:
                fail_next_attachment = False
                library.al.error = bindings.AL_INVALID_OPERATION

        monkeypatch.setattr(library.al, "source3i", fail_one_attachment)

        with pytest.raises(AudioBackendError, match="configure voice EFX routing"):
            set_voice_config(playback, voice, replacement)

        assert library.al.sources[100][bindings.AL_GAIN] == 0.75
        assert library.al.source_sends[(100, 0)] == (400, bindings.AL_FILTER_NULL)
        assert library.al.allocated_effects == {200}
        assert library.al.allocated_effect_slots == {400}
        assert library.al.allocated_filters == set()
        assert playback._voice_configs[voice._token] == previous


def test_voice_efx_require_device_support_and_available_send_slots() -> None:
    library = FakeLibrary()
    library.alc.extensions.remove("ALC_EXT_EFX")
    with open_playback(library=as_library(library)) as playback:
        clip = upload(playback, PCM(b"\0\0", channels=1, sample_rate=1))
        with pytest.raises(AudioBackendError, match="does not support EFX"):
            play(playback, clip, filter=LowPassFilter())
        assert library.al.sources == {}
        assert library.al.allocated_filters == set()

    library = FakeLibrary()
    library.alc.max_auxiliary_sends = 1
    sends = (
        EffectSend(effect=Reverb(decay_time=0.5)),
        EffectSend(effect=Reverb(decay_time=1.0)),
    )
    with open_playback(library=as_library(library)) as playback:
        clip = upload(playback, PCM(b"\0\0", channels=1, sample_rate=1))
        voice = play(playback, clip)
        with pytest.raises(AudioBackendError, match="at most 1"):
            set_voice_config(
                playback,
                voice,
                VoiceConfig(gain=0.25, effect_sends=sends),
            )
        assert library.al.sources[100][bindings.AL_GAIN] == 1.0
        assert playback._voice_configs[voice._token] == VoiceConfig()
        release(playback, voice)

        with pytest.raises(AudioBackendError, match="at most 1"):
            play(playback, clip, effect_sends=sends)
        assert library.al.sources == {}
        assert library.al.allocated_effects == set()


def test_static_voice_can_seek_rewind_and_restart() -> None:
    library = FakeLibrary()
    with open_playback(library=as_library(library)) as playback:
        clip = upload(playback, PCM(b"\0\0" * 10, channels=1, sample_rate=10))
        voice = play(playback, clip)

        pause(playback, voice)
        seek(playback, voice, 0.5)
        assert get_voice_status(playback, voice) == VoiceStatus(
            state=VoiceState.PAUSED,
            offset_seconds=0.5,
            offset_frames=5,
        )

        seek_frames(playback, voice, 7)
        assert get_voice_status(playback, voice) == VoiceStatus(
            state=VoiceState.PAUSED,
            offset_seconds=0.7,
            offset_frames=7,
        )

        rewind(playback, voice)
        assert get_voice_status(playback, voice) == VoiceStatus(
            state=VoiceState.INITIAL,
            offset_seconds=0.0,
            offset_frames=0,
        )

        restart(playback, voice)
        assert get_voice_status(playback, voice).state is VoiceState.PLAYING

        release(playback, voice)
        release(playback, clip)


def test_release_finished_collects_only_stopped_voices() -> None:
    library = FakeLibrary()
    with open_playback(library=as_library(library)) as playback:
        clip = upload(playback, PCM(b"\0\0", channels=1, sample_rate=1))
        finished = play(
            playback,
            clip,
            filter=LowPassFilter(high_frequency_gain=0.5),
        )
        paused = play(playback, clip)
        library.al.states[100] = bindings.AL_STOPPED
        pause(playback, paused)

        assert release_finished(playback) == 1
        assert release_finished(playback) == 0
        with pytest.raises(InvalidHandleError, match="released"):
            get_voice_status(playback, finished)
        assert library.al.allocated_filters == set()
        assert get_voice_status(playback, paused).state is VoiceState.PAUSED

        release(playback, paused)
        release(playback, clip)


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
