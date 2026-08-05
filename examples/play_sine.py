"""Play a short sine wave with no dependencies beyond PyALSoft."""

from __future__ import annotations

import math
import time
from array import array

from pyalsoft import bindings

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
    library: bindings.OpenALLibrary | None = None,
    *,
    frequency: float = 440.0,
    duration: float = 0.5,
) -> None:
    """Play a sine wave and release every OpenAL resource afterward."""

    library = library or bindings.load()
    device = library.alc.open_device(None)
    if not device:
        raise RuntimeError("could not open the default OpenAL playback device")

    context: object | None = None
    buffer_id: int | None = None
    source_id: int | None = None
    context_is_current = False
    try:
        context = library.alc.create_context(device, None)
        if not context:
            raise RuntimeError("could not create an OpenAL context")
        if not library.alc.make_context_current(context):
            raise RuntimeError("could not make the OpenAL context current")
        context_is_current = True

        (buffer_id,) = library.al.gen_buffers()
        library.al.buffer_data(
            buffer_id,
            bindings.enums.ALFormat.FORMAT_MONO16,
            sine_pcm(frequency=frequency, duration=duration),
            SAMPLE_RATE,
        )

        (source_id,) = library.al.gen_sources()
        source = library.al.source(source_id)
        source.buffer = library.al.buffer(buffer_id)
        library.al.source_play(source_id)

        deadline = time.monotonic() + duration + 2.0
        while source.state == bindings.enums.ALSourceState.PLAYING:
            if time.monotonic() >= deadline:
                raise RuntimeError("timed out waiting for playback to finish")
            time.sleep(0.01)
    finally:
        if context_is_current:
            if source_id is not None:
                library.al.source_stop(source_id)
                library.al.delete_sources([source_id])
            if buffer_id is not None:
                library.al.delete_buffers([buffer_id])
            library.alc.make_context_current(None)
        if context is not None:
            library.alc.destroy_context(context)
        if not library.alc.close_device(device):
            raise RuntimeError("could not close the OpenAL playback device")


if __name__ == "__main__":
    play_sine()
