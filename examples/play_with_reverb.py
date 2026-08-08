"""Play a sound with reverb through the managed EFX API."""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path

from pyalsoft import EffectSend, Reverb, play

EXAMPLE_SOUND = Path(__file__).with_name("example.wav")
DEMO_STEP_SECONDS = 1.0


def main() -> None:
    """Move one looping sound between two immutable room descriptions."""

    small_room = Reverb(
        gain=0.2,
        decay_time=0.6,
        high_frequency_decay_ratio=0.8,
    )
    sound = play(
        EXAMPLE_SOUND,
        looping=True,
        effect_sends=(EffectSend(effect=small_room),),
    )
    time.sleep(DEMO_STEP_SECONDS)

    # EFX effects use an auxiliary send. Replacing the immutable value updates
    # the sound without exposing the native effect and effect-slot objects.
    large_room = replace(
        small_room,
        gain=0.35,
        decay_time=2.8,
        high_frequency_decay_ratio=0.55,
    )
    sound.update(effect_sends=(EffectSend(effect=large_room),))
    time.sleep(DEMO_STEP_SECONDS)

    # An empty tuple disconnects every auxiliary send. The dry signal keeps
    # playing, so effects can be enabled and disabled without restarting it.
    sound.update(effect_sends=(), looping=False)
    while sound.playing:
        time.sleep(0.05)


if __name__ == "__main__":
    main()
