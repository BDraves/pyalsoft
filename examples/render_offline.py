"""Render managed playback to memory without opening audio hardware."""

from __future__ import annotations

import argparse
import math
import struct
import wave
from pathlib import Path

from pyalsoft import (
    PCM,
    RenderChannelLayout,
    RenderConfig,
    RenderSampleType,
    open_offline_playback,
    play,
    render_samples,
    upload,
)

SAMPLE_RATE = 48_000
DURATION_SECONDS = 1.0


def render_sine() -> bytes:
    """Return one second of stereo signed 16-bit rendered audio."""

    frame_count = round(SAMPLE_RATE * DURATION_SECONDS)
    mono = b"".join(
        struct.pack(
            "<h",
            round(8_000 * math.sin(2 * math.pi * 440 * frame / SAMPLE_RATE)),
        )
        for frame in range(frame_count)
    )
    config = RenderConfig(
        sample_rate=SAMPLE_RATE,
        channels=RenderChannelLayout.STEREO,
        sample_type=RenderSampleType.INT16,
    )
    with open_offline_playback(config) as playback:
        clip = upload(playback, PCM(mono, channels=1, sample_rate=SAMPLE_RATE))
        play(playback, clip, position=(0.0, 0.0, -1.0))
        return render_samples(playback, frame_count)


def write_wave(path: Path, samples: bytes) -> None:
    """Write stereo signed 16-bit output to a WAV file."""

    with wave.open(str(path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)
        output.writeframes(samples)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="optional output WAV path")
    arguments = parser.parse_args()
    samples = render_sine()
    if arguments.output is None:
        print(f"rendered {len(samples)} bytes")
    else:
        write_wave(arguments.output, samples)
        print(f"wrote {arguments.output}")


if __name__ == "__main__":
    main()
