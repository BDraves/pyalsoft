"""Share one managed reverb bus between multiple sounds."""

from __future__ import annotations

import math
import struct
import time

from pyalsoft import (
    PCM,
    EffectBusConfig,
    EffectSend,
    Reverb,
    create_effect_bus,
    open_playback,
    play,
    upload,
)

SAMPLE_RATE = 48_000
DURATION_SECONDS = 1.0


def sine_pcm(frequency: float) -> PCM:
    """Build one second of mono signed 16-bit sine-wave audio."""

    frames = round(SAMPLE_RATE * DURATION_SECONDS)
    samples = b"".join(
        struct.pack(
            "<h",
            round(8_000 * math.sin(2 * math.pi * frequency * frame / SAMPLE_RATE)),
        )
        for frame in range(frames)
    )
    return PCM(samples, channels=1, sample_rate=SAMPLE_RATE)


def main() -> None:
    """Play two positioned tones through one reusable reverb bus."""

    with open_playback() as playback:
        room = create_effect_bus(
            playback,
            EffectBusConfig(
                effect=Reverb(gain=0.25, decay_time=1.8),
                gain=0.8,
            ),
        )
        send = EffectSend(bus=room)
        low = upload(playback, sine_pcm(330.0))
        high = upload(playback, sine_pcm(550.0))
        play(playback, low, position=(-1.5, 0.0, -3.0), effect_sends=(send,))
        play(playback, high, position=(1.5, 0.0, -3.0), effect_sends=(send,))
        time.sleep(DURATION_SECONDS)


if __name__ == "__main__":
    main()
