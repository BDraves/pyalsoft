"""Play an intro once, repeat a selected region, then continue to the outro."""

from __future__ import annotations

import math
import time
from array import array

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

SAMPLE_RATE = 44_100
SEGMENT_SECONDS = 0.5


def looping_tone_pcm() -> tuple[PCM, tuple[int, int]]:
    """Return an intro/loop/outro tone and its loop-frame range."""

    samples = array("h")
    amplitude = (1 << 14) - 1
    segment_frames = round(SAMPLE_RATE * SEGMENT_SECONDS)

    def append_tone(frequency: float) -> None:
        angular_step = math.tau * frequency / SAMPLE_RATE
        samples.extend(
            round(amplitude * math.sin(frame * angular_step))
            for frame in range(segment_frames)
        )

    append_tone(330.0)
    loop_start = len(samples)
    append_tone(660.0)
    loop_end = len(samples)
    append_tone(440.0)
    return (
        PCM(samples.tobytes(), channels=1, sample_rate=SAMPLE_RATE),
        (loop_start, loop_end),
    )


def demonstrate_loop_points(
    library: OpenALLibrary | None = None,
    *,
    repeat_seconds: float = 2.0,
) -> None:
    """Repeat only the middle tone before allowing playback to finish."""

    if repeat_seconds < 0:
        raise ValueError("repeat_seconds cannot be negative")
    pcm, loop_points = looping_tone_pcm()

    with open_playback(library=library) as playback:
        clip = upload(playback, pcm, loop_points=loop_points)
        voice = play(playback, clip, VoiceConfig(looping=True))

        time.sleep(repeat_seconds)
        set_voice_config(playback, voice, VoiceConfig(looping=False))

        deadline = time.monotonic() + pcm.duration + 2.0
        while get_voice_status(playback, voice).state is VoiceState.PLAYING:
            if time.monotonic() >= deadline:
                raise RuntimeError("timed out waiting for playback to finish")
            time.sleep(0.01)

        release(playback, voice)
        release(playback, clip)


if __name__ == "__main__":
    demonstrate_loop_points()
