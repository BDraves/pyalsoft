"""Play a sound file while a simple application loop keeps running."""

from __future__ import annotations

import time
from pathlib import Path

from pyalsoft import play

EXAMPLE_SOUND = Path(__file__).with_name("example.wav")


def main() -> None:
    """Play the example chime and do fake application work until it finishes."""

    sound = play(EXAMPLE_SOUND)
    while sound.playing:
        time.sleep(0.1)


if __name__ == "__main__":
    main()
