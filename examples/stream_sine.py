"""Stream a generated sine wave with bounded memory."""

from __future__ import annotations

import math
from array import array
from collections.abc import Iterator

from pyalsoft import (
    SampleType,
    finish_stream,
    open_playback,
    open_stream,
    release,
    start_stream,
    try_write_stream,
    wait,
    write_stream,
)
from pyalsoft.bindings import OpenALLibrary

SAMPLE_RATE = 44_100
CHUNK_FRAMES = 1_024
BUFFER_COUNT = 4


def sine_chunks(*, frequency: float, duration: float) -> Iterator[bytes]:
    """Yield consecutive mono 16-bit chunks of a sine wave."""

    if frequency <= 0:
        raise ValueError("frequency must be positive")
    if duration <= 0:
        raise ValueError("duration must be positive")

    remaining = round(SAMPLE_RATE * duration)
    first_frame = 0
    amplitude = (1 << 14) - 1
    while remaining:
        frame_count = min(remaining, CHUNK_FRAMES)
        samples = array(
            "h",
            (
                round(
                    amplitude
                    * math.sin(
                        math.tau * frequency * (first_frame + frame) / SAMPLE_RATE
                    )
                )
                for frame in range(frame_count)
            ),
        )
        yield samples.tobytes()
        first_frame += frame_count
        remaining -= frame_count


def stream_sine(
    library: OpenALLibrary | None = None,
    *,
    frequency: float = 440.0,
    duration: float = 0.5,
) -> None:
    """Generate and stream a sine wave, releasing every resource afterward."""

    chunks = iter(sine_chunks(frequency=frequency, duration=duration))
    with open_playback(library=library) as playback:
        stream = open_stream(
            playback,
            channels=1,
            sample_rate=SAMPLE_RATE,
            sample_type=SampleType.INT16,
            buffer_count=BUFFER_COUNT,
        )
        queued = 0
        for _ in range(BUFFER_COUNT):
            try:
                chunk = next(chunks)
            except StopIteration:
                break
            if not try_write_stream(playback, stream, chunk):
                raise RuntimeError("stream reported backpressure while being primed")
            queued += 1
        if not queued:
            raise ValueError("duration is too short to contain an audio frame")

        start_stream(playback, stream)
        for chunk in chunks:
            if not write_stream(playback, stream, chunk, timeout=2.0):
                raise RuntimeError("timed out waiting for a stream buffer")

        finish_stream(playback, stream)
        if not wait(playback, stream, timeout=duration + 2.0):
            raise RuntimeError("timed out waiting for the stream to finish")
        release(playback, stream)


if __name__ == "__main__":
    stream_sine()
