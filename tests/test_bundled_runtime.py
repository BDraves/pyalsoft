"""Integration tests against the checked-in Windows OpenAL Soft runtime."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from examples.move_sine import move_sine
from examples.play_sine import SAMPLE_RATE, play_sine, sine_pcm
from examples.stream_sine import stream_sine
from pyalsoft import (
    PCM,
    EffectSend,
    HighPassFilter,
    LowPassFilter,
    Reverb,
    VoiceConfig,
    bindings,
    open_playback,
    play,
    release,
    set_voice_config,
    upload,
)

ROOT = Path(__file__).resolve().parents[1]
WINDOWS_RUNTIME = (
    ROOT / "vendor" / "openal-soft" / "runtime" / "win_amd64" / "soft_oal.dll"
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        sys.platform != "win32" or not WINDOWS_RUNTIME.is_file(),
        reason="requires the checked-in Windows OpenAL Soft runtime",
    ),
]


def test_bundled_runtime_runs_playback_examples(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALSOFT_DRIVERS", "null")
    library = bindings.load(WINDOWS_RUNTIME)

    play_sine(library, duration=0.05)
    move_sine(library, duration=0.05)
    stream_sine(library, duration=0.05)


def test_bundled_runtime_supports_managed_efx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALSOFT_DRIVERS", "null")
    library = bindings.load(WINDOWS_RUNTIME)
    pcm = PCM(
        sine_pcm(frequency=440.0, duration=0.05),
        channels=1,
        sample_rate=SAMPLE_RATE,
    )

    with open_playback(library=library) as playback:
        clip = upload(playback, pcm)
        voice = play(
            playback,
            clip,
            filter=LowPassFilter(high_frequency_gain=0.1),
            effect_sends=(
                EffectSend(
                    effect=Reverb(decay_time=0.6),
                    filter=HighPassFilter(low_frequency_gain=0.25),
                ),
            ),
        )
        set_voice_config(
            playback,
            voice,
            VoiceConfig(filter=HighPassFilter(low_frequency_gain=0.1)),
        )
        release(playback, voice)
        release(playback, clip)
