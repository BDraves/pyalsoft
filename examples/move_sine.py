"""Move a playing sine wave using immutable voice configuration data."""

from __future__ import annotations

import time
from dataclasses import replace
from typing import TYPE_CHECKING

from pyalsoft import (
    PCM,
    VoiceConfig,
    VoiceState,
    get_voice_status,
    open_playback,
    play,
    release,
    set_voice_config,
    upload,
)
from pyalsoft.bindings import OpenALLibrary

if TYPE_CHECKING:
    from examples.play_sine import SAMPLE_RATE, sine_pcm
elif __package__:
    from .play_sine import SAMPLE_RATE, sine_pcm
else:
    from play_sine import SAMPLE_RATE, sine_pcm


def move_sine(
    library: OpenALLibrary | None = None,
    *,
    duration: float = 2.0,
) -> None:
    """Play a sine wave that travels from left to right."""

    pcm = PCM(
        samples=sine_pcm(frequency=440.0, duration=duration),
        channels=1,
        sample_rate=SAMPLE_RATE,
    )
    config = VoiceConfig(position=(-2.0, 0.0, -1.0))

    with open_playback(library=library) as playback:
        clip = upload(playback, pcm)
        voice = play(playback, clip, config)
        started = time.monotonic()
        deadline = started + duration + 2.0

        while get_voice_status(playback, voice).state is VoiceState.PLAYING:
            now = time.monotonic()
            if now >= deadline:
                raise RuntimeError("timed out waiting for playback to finish")
            progress = min((now - started) / duration, 1.0)
            config = replace(config, position=(-2.0 + 4.0 * progress, 0.0, -1.0))
            set_voice_config(playback, voice, config)
            time.sleep(1 / 60)
        release(playback, voice)
        release(playback, clip)


if __name__ == "__main__":
    move_sine()
