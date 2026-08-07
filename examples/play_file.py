"""Play a file and change one sound's timeline and spatial controls."""

from __future__ import annotations

import time
from pathlib import Path

from pyalsoft import play

EXAMPLE_SOUND = Path(__file__).with_name("example.wav")
DEMO_STEP_SECONDS = 0.35


def show_status(label: str, sound_offset: float, duration: float) -> None:
    """Print the source-audio timeline rather than wall-clock playback time."""

    print(f"{label:>16}: {sound_offset:.2f}s / {duration:.2f}s")


def main() -> None:
    """Loop the example while demonstrating live per-sound changes."""

    sound = play(
        EXAMPLE_SOUND,
        # Basic controls. Pitch changes speed and pitch together.
        gain=0.65,
        pitch=1.0,
        looping=True,
        # Coordinates are listener-relative here. The default listener faces -Z.
        relative=True,
        position=(-2.0, 0.0, -2.0),
        velocity=(0.0, 0.0, 0.0),
        direction=(1.0, 0.0, 1.0),
        # Distance attenuation controls.
        reference_distance=1.0,
        max_distance=10.0,
        rolloff_factor=1.0,
        min_gain=0.0,
        max_gain=1.0,
        # Directional-cone controls. The source initially points at the listener.
        cone_inner_angle=60.0,
        cone_outer_angle=180.0,
        cone_outer_gain=0.15,
    )

    print(
        f"{sound.path.name}: {sound.channels} channel, {sound.sample_rate} Hz, "
        f"{sound.frame_count} frames"
    )
    show_status("started left", sound.offset_seconds, sound.duration_seconds)
    time.sleep(DEMO_STEP_SECONDS)

    # Move from the listener's left to right and describe motion toward them.
    sound.update(
        position=(2.0, 0.0, -2.0),
        velocity=(-1.0, 0.0, 1.0),
        direction=(-1.0, 0.0, 1.0),
    )
    show_status("moved right", sound.offset_seconds, sound.duration_seconds)
    time.sleep(DEMO_STEP_SECONDS)

    # Turn the source away, lower its gain, and increase playback rate/pitch.
    sound.update(direction=(1.0, 0.0, -1.0), gain=0.4, pitch=1.35)
    show_status("turned away", sound.offset_seconds, sound.duration_seconds)
    time.sleep(DEMO_STEP_SECONDS)

    # Offset is the playhead's position on the original audio timeline. Seeking
    # while paused preserves the paused state; pitch does not alter duration.
    sound.pause()
    sound.seek_frames(sound.frame_count // 2)
    show_status("seeked halfway", sound.offset_seconds, sound.duration_seconds)
    sound.resume()

    # Stop looping and allow the last pass to finish.
    sound.update(looping=False)
    while sound.playing:
        time.sleep(0.05)
    show_status("finished", sound.offset_seconds, sound.duration_seconds)
    end_reason = sound.end_reason
    print(f"end reason: {end_reason.value if end_reason else 'active'}")


if __name__ == "__main__":
    main()
