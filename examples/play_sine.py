"""Play a short sine wave with no dependencies beyond PyALSoft."""

from __future__ import annotations

import math
import time
from array import array

from pyalsoft import (
    PCM,
    SampleType,
    VoiceState,
    get_voice_status,
    open_playback,
    play,
    upload,
)
from pyalsoft.bindings import OpenALLibrary

SAMPLE_RATE = 44_100


def sine_pcm(*, frequency: float, duration: float) -> bytes:
    """Return mono 16-bit PCM samples for a sine wave."""

    if frequency <= 0:
        raise ValueError("frequency must be positive")
    if duration <= 0:
        raise ValueError("duration must be positive")

    frame_count = round(SAMPLE_RATE * duration)
    amplitude = (1 << 14) - 1
    angular_step = math.tau * frequency / SAMPLE_RATE
    samples = array(
        "h",
        (
            round(amplitude * math.sin(frame * angular_step))
            for frame in range(frame_count)
        ),
    )
    return samples.tobytes()


def play_sine(
    library: OpenALLibrary | None = None,
    *,
    frequency: float = 440.0,
    duration: float = 0.5,
) -> None:
    """Play a sine wave and release every audio resource afterward."""

    pcm = PCM(
        samples=sine_pcm(frequency=frequency, duration=duration),
        channels=1,
        sample_rate=SAMPLE_RATE,
        sample_type=SampleType.INT16,
    )
    with open_playback(library=library) as playback:
        clip = upload(playback, pcm)
        voice = play(playback, clip)
        deadline = time.monotonic() + duration + 2.0
        while get_voice_status(playback, voice).state is VoiceState.PLAYING:
            if time.monotonic() >= deadline:
                raise RuntimeError("timed out waiting for playback to finish")
            time.sleep(0.01)


if __name__ == "__main__":
    play_sine()
