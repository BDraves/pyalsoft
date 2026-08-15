"""Integration tests against a real platform OpenAL runtime."""

from __future__ import annotations

import os
from typing import cast

import pytest

from pyalsoft import (
    PCM,
    Acoustics,
    AutoWah,
    Chorus,
    Compressor,
    DistanceModel,
    Distortion,
    EAXReverb,
    Echo,
    Effect,
    EffectSend,
    Equalizer,
    Flanger,
    FrequencyShifter,
    Listener,
    PitchShifter,
    Reverb,
    RingModulator,
    VocalMorpher,
    VoiceState,
    bindings,
    get_acoustics,
    get_listener,
    get_voice_status,
    open_playback,
    pause,
    play,
    release,
    restart,
    rewind,
    seek_frames,
    set_acoustics,
    set_listener,
    upload,
)
from tools.conformance_test_runtime import run as run_backend_conformance

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("PYALSOFT_RUN_SYSTEM_TESTS") != "1",
        reason="set PYALSOFT_RUN_SYSTEM_TESTS=1 to test a native OpenAL runtime",
    ),
]


def test_system_runtime_can_create_a_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Load OpenAL Soft and exercise its device and context lifecycle."""

    monkeypatch.setenv("ALSOFT_DRIVERS", "null")
    path = os.environ.get("PYALSOFT_TEST_LIBRARY")
    library = bindings.load(path)

    device = cast(object | None, library.alcOpenDevice(None))
    assert device, f"could not open the null device with {library.library_name!r}"

    context: object | None = None
    try:
        context = cast(object | None, library.alcCreateContext(device, None))
        assert context, f"could not create a context with {library.library_name!r}"
        assert library.alcMakeContextCurrent(context)

        version = cast(bytes | None, library.alGetString(bindings.AL_VERSION))
        assert version
    finally:
        library.alcMakeContextCurrent(None)
        if context:
            library.alcDestroyContext(context)
        assert library.alcCloseDevice(device)


def test_system_runtime_passes_backend_conformance() -> None:
    """Exercise generated marshalling and owned handles with loopback output."""

    path = os.environ.get("PYALSOFT_TEST_LIBRARY")
    result = run_backend_conformance(bindings.load(path))

    assert result.startswith("backend conformance passed:")


def test_system_runtime_supports_managed_sound_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise v0.7 static-sound controls against a native backend."""

    monkeypatch.setenv("ALSOFT_DRIVERS", "null")
    path = os.environ.get("PYALSOFT_TEST_LIBRARY")
    with open_playback(library=bindings.load(path)) as playback:
        clip = upload(
            playback,
            PCM(bytes(48_000 * 2), channels=1, sample_rate=48_000),
        )
        voice = play(playback, clip, offset_frames=1_000)
        pause(playback, voice)
        seek_frames(playback, voice, 12_000)
        status = get_voice_status(playback, voice)
        assert status.state is VoiceState.PAUSED
        assert status.offset_frames == 12_000
        assert status.offset_seconds == pytest.approx(0.25)

        listener = Listener(position=(1.0, 2.0, 3.0), gain=0.5)
        acoustics = Acoustics(
            distance_model=DistanceModel.LINEAR_CLAMPED,
            doppler_factor=0.5,
            speed_of_sound=300.0,
        )
        set_listener(playback, listener)
        set_acoustics(playback, acoustics)
        assert get_listener(playback) == listener
        assert get_acoustics(playback) == acoustics

        rewind(playback, voice)
        assert get_voice_status(playback, voice).state is VoiceState.INITIAL
        restart(playback, voice)
        assert get_voice_status(playback, voice).state is VoiceState.PLAYING

        release(playback, voice)
        release(playback, clip)


@pytest.mark.parametrize(
    "effect",
    [
        Reverb(),
        EAXReverb(),
        Chorus(),
        Distortion(),
        Echo(),
        Flanger(),
        FrequencyShifter(),
        VocalMorpher(),
        PitchShifter(),
        RingModulator(),
        AutoWah(),
        Compressor(),
        Equalizer(),
    ],
)
def test_system_runtime_supports_every_core_efx_effect(
    monkeypatch: pytest.MonkeyPatch,
    effect: Effect,
) -> None:
    monkeypatch.setenv("ALSOFT_DRIVERS", "null")
    path = os.environ.get("PYALSOFT_TEST_LIBRARY")
    with open_playback(library=bindings.load(path)) as playback:
        clip = upload(playback, PCM(b"\0\0" * 10, channels=1, sample_rate=10))
        voice = play(playback, clip, effect_sends=(EffectSend(effect=effect),))
        release(playback, voice)
        release(playback, clip)
