"""Replace and remove direct low/high/band-pass filters on a playing sound."""

from __future__ import annotations

import time
from pathlib import Path

from pyalsoft import BandPassFilter, HighPassFilter, LowPassFilter, play

EXAMPLE_SOUND = Path(__file__).with_name("example.wav")
DEMO_STEP_SECONDS = 1.0


def main() -> None:
    """Replace and remove the direct filter on one looping sound."""

    sound = play(
        EXAMPLE_SOUND,
        looping=True,
        # EFX filters specify gain, not a cutoff frequency. This preserves the
        # full low-frequency signal while attenuating high frequencies.
        filter=LowPassFilter(gain=1.0, high_frequency_gain=0.1),
    )
    time.sleep(DEMO_STEP_SECONDS)

    # A source has one direct filter, so setting another replaces the first.
    # This keeps the hardware constraint visible instead of implying a chain.
    sound.update(
        filter=HighPassFilter(gain=1.0, low_frequency_gain=0.1),
    )
    time.sleep(DEMO_STEP_SECONDS)

    # Band-pass controls the low- and high-frequency attenuation independently.
    sound.update(
        filter=BandPassFilter(
            gain=1.0,
            low_frequency_gain=0.1,
            high_frequency_gain=0.1,
        ),
    )
    time.sleep(DEMO_STEP_SECONDS)

    # None removes the direct filter and restores the unfiltered dry signal.
    sound.update(filter=None, looping=False)
    while sound.playing:
        time.sleep(0.05)


if __name__ == "__main__":
    main()
