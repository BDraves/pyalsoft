"""Tests for convenient file playback and its implicit runtime."""

from __future__ import annotations

from pathlib import Path

import pytest

from pyalsoft import (
    PCM,
    Acoustics,
    AudioError,
    DistanceModel,
    EffectSend,
    Listener,
    LowPassFilter,
    Reverb,
    SoundCacheInfo,
    SoundEndReason,
    VoiceState,
    bindings,
    clear_sound_cache,
    get_acoustics,
    get_listener,
    get_sound_cache_info,
    play,
    set_acoustics,
    set_listener,
    set_sound_cache_limit,
    shutdown,
    update_acoustics,
    update_listener,
)
from tests._support.managed_backend import FakeLibrary
from tests.managed._sound_support import _assert_state, _write_wave


def test_default_runtime_plays_in_memory_pcm(
    default_library: FakeLibrary,
) -> None:
    pcm = PCM(
        samples=b"\x01\x00\x02\x00\x03\x00\x04\x00",
        channels=1,
        sample_rate=8_000,
    )

    sound = play(pcm)

    assert sound.playing
    assert sound.info == pcm.info
    assert default_library.al.buffers[1] == (
        bindings.AL_FORMAT_MONO16,
        pcm.samples,
        8_000,
    )
    with pytest.raises(AudioError, match="no source path"):
        _ = sound.path

    sound.stop()

    assert default_library.al.allocated_buffers == set()
    assert sound.info == pcm.info

    sound.restart()

    assert sound.playing
    assert default_library.al.buffers[2] == (
        bindings.AL_FORMAT_MONO16,
        pcm.samples,
        8_000,
    )
    default_library.al.states[101] = bindings.AL_STOPPED
    assert sound.finished
    assert default_library.al.allocated_buffers == set()


def test_direct_channels_rejects_surround_pcm(
    default_library: FakeLibrary,
) -> None:
    pcm = PCM(samples=bytes(4 * 2), channels=4, sample_rate=8_000)

    with pytest.raises(ValueError, match="direct_channels requires mono or stereo"):
        play(pcm, direct_channels=True)

    assert default_library.al.allocated_buffers == set()


def test_default_runtime_releases_pcm_when_voice_creation_fails(
    default_library: FakeLibrary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pcm = PCM(samples=b"\x01\x00\x02\x00", channels=1, sample_rate=8_000)

    def fail_to_play(identifier: int) -> None:
        del identifier
        raise RuntimeError("could not play")

    monkeypatch.setattr(default_library.al, "source_play", fail_to_play)

    with pytest.raises(RuntimeError, match="could not play"):
        play(
            pcm,
            filter=LowPassFilter(high_frequency_gain=0.5),
            effect_sends=(EffectSend(effect=Reverb(decay_time=0.5)),),
        )

    assert default_library.al.sources == {}
    assert default_library.al.allocated_buffers == set()
    assert default_library.al.allocated_effects == set()
    assert default_library.al.allocated_effect_slots == set()
    assert default_library.al.allocated_filters == set()


def test_default_runtime_exposes_listener_and_acoustics_controls(
    default_library: FakeLibrary,
) -> None:
    listener = Listener(
        position=(1.0, 2.0, 3.0),
        velocity=(0.0, 0.0, -1.0),
        gain=0.8,
    )
    acoustics = Acoustics(
        distance_model=DistanceModel.EXPONENT,
        doppler_factor=0.75,
        speed_of_sound=300.0,
    )

    set_listener(listener)
    set_acoustics(acoustics)

    assert get_listener() == listener
    assert get_acoustics() == acoustics
    assert default_library.al.listener[bindings.AL_POSITION] == (1.0, 2.0, 3.0)
    assert default_library.al.distance_model_value == bindings.AL_EXPONENT_DISTANCE

    assert update_listener(position=(-1.0, 0.0, -2.0), gain=0.5) == Listener(
        position=(-1.0, 0.0, -2.0),
        velocity=(0.0, 0.0, -1.0),
        gain=0.5,
    )
    assert update_acoustics(doppler_factor=0.25) == Acoustics(
        distance_model=DistanceModel.EXPONENT,
        doppler_factor=0.25,
        speed_of_sound=300.0,
    )


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


def test_sound_cache_configuration_is_validated_and_observable(
    default_library: FakeLibrary,
) -> None:
    del default_library

    assert get_sound_cache_info() == SoundCacheInfo(
        max_bytes=64 * 1024 * 1024,
        current_bytes=0,
        clip_count=0,
        active_clip_count=0,
        pending_eviction_count=0,
    )
    with pytest.raises(TypeError, match="integer or None"):
        set_sound_cache_limit(True)
    with pytest.raises(TypeError, match="integer or None"):
        set_sound_cache_limit(1.0)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="cannot be negative"):
        set_sound_cache_limit(-1)
    with pytest.raises(TypeError, match="path to a supported audio file or None"):
        clear_sound_cache(1)  # type: ignore[arg-type]

    set_sound_cache_limit(None)

    assert get_sound_cache_info().max_bytes is None


def test_sound_cache_temporarily_exceeds_budget_for_active_clips(
    tmp_path: Path,
    default_library: FakeLibrary,
) -> None:
    first_path = tmp_path / "first.wav"
    second_path = tmp_path / "second.wav"
    _write_wave(first_path)
    _write_wave(second_path)
    set_sound_cache_limit(16)

    first = play(first_path)
    second = play(second_path)

    assert get_sound_cache_info() == SoundCacheInfo(
        max_bytes=16,
        current_bytes=32,
        clip_count=2,
        active_clip_count=2,
        pending_eviction_count=0,
    )

    first.stop()

    assert get_sound_cache_info() == SoundCacheInfo(
        max_bytes=16,
        current_bytes=16,
        clip_count=1,
        active_clip_count=1,
        pending_eviction_count=0,
    )
    assert default_library.al.allocated_buffers == {2}
    second.stop()


def test_sound_cache_separates_normal_and_direct_channel_variants(
    tmp_path: Path,
    default_library: FakeLibrary,
) -> None:
    path = tmp_path / "footstep.wav"
    _write_wave(path)

    positional = play(path)
    direct = play(path, direct_channels=True)

    assert default_library.al.buffers[1][0] == bindings.AL_FORMAT_MONO16
    assert default_library.al.buffers[2][0] == bindings.AL_FORMAT_STEREO16
    assert get_sound_cache_info() == SoundCacheInfo(
        max_bytes=64 * 1024 * 1024,
        current_bytes=48,
        clip_count=2,
        active_clip_count=2,
        pending_eviction_count=0,
    )
    assert positional.channels == direct.channels == 1

    positional.stop()
    direct.stop()

    assert clear_sound_cache(path) == 2
    assert default_library.al.allocated_buffers == set()


def test_sound_cache_uses_least_recently_used_eviction(
    tmp_path: Path,
    default_library: FakeLibrary,
) -> None:
    first_path = tmp_path / "first.wav"
    second_path = tmp_path / "second.wav"
    _write_wave(first_path)
    _write_wave(second_path)
    set_sound_cache_limit(32)

    play(first_path).stop()
    play(second_path).stop()
    play(first_path).stop()
    set_sound_cache_limit(16)

    assert get_sound_cache_info().clip_count == 1
    assert default_library.al.allocated_buffers == {1}
    assert clear_sound_cache() == 1
    assert get_sound_cache_info().current_bytes == 0
    assert default_library.al.allocated_buffers == set()


def test_clearing_an_active_sound_cache_entry_defers_eviction(
    tmp_path: Path,
    default_library: FakeLibrary,
) -> None:
    path = tmp_path / "active.wav"
    _write_wave(path)
    sound = play(path)

    assert clear_sound_cache(path) == 0
    assert get_sound_cache_info().pending_eviction_count == 1
    assert default_library.al.allocated_buffers == {1}

    sound.stop()

    assert get_sound_cache_info() == SoundCacheInfo(
        max_bytes=64 * 1024 * 1024,
        current_bytes=0,
        clip_count=0,
        active_clip_count=0,
        pending_eviction_count=0,
    )
    assert default_library.al.allocated_buffers == set()


def test_disconnected_device_is_not_reported_as_natural_completion(
    tmp_path: Path,
    default_library: FakeLibrary,
) -> None:
    path = tmp_path / "disconnected.wav"
    _write_wave(path)
    sound = play(path)
    default_library.alc.extensions.add("ALC_EXT_disconnect")
    default_library.alc.connected = False
    default_library.al.offsets[100] = 0.0005
    default_library.al.frame_offsets[100] = 4
    default_library.al.states[100] = bindings.AL_STOPPED

    assert sound.end_reason is SoundEndReason.DEVICE_LOST
    assert not sound.finished
    assert sound.offset_frames < sound.frame_count


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
    assert sound.end_reason is SoundEndReason.SHUTDOWN
    sound.stop()


def test_shutdown_preserves_natural_completion(
    tmp_path: Path,
    default_library: FakeLibrary,
) -> None:
    path = tmp_path / "finished.wav"
    _write_wave(path)
    sound = play(path)
    default_library.al.states[100] = bindings.AL_STOPPED

    shutdown()

    assert sound.state is VoiceState.STOPPED
    assert sound.end_reason is SoundEndReason.FINISHED
    assert sound.offset_seconds == sound.duration_seconds
    assert sound.offset_frames == sound.frame_count
