"""Replace one auxiliary EFX effect while a sound is playing."""

from __future__ import annotations

import time
from pathlib import Path

from pyalsoft import Chorus, Echo, EffectSend, Reverb, play

EXAMPLE_SOUND = Path(__file__).with_name("example.wav")
DEMO_STEP_SECONDS = 1.0


def main() -> None:
    """Cycle one looping sound through effects backed by the same send API."""

    sound = play(
        EXAMPLE_SOUND,
        looping=True,
        effect_sends=(EffectSend(effect=Reverb(decay_time=0.6)),),
    )
    time.sleep(DEMO_STEP_SECONDS)

    sound.effect_sends = (EffectSend(effect=Chorus(rate=0.8, depth=0.3)),)
    time.sleep(DEMO_STEP_SECONDS)

    sound.update(
        effect_sends=(EffectSend(effect=Echo(delay=0.15, feedback=0.35)),),
        looping=False,
    )
    while sound.playing:
        time.sleep(0.05)


if __name__ == "__main__":
    main()
