"""Record from the default input until Enter, then play the result."""

from __future__ import annotations

from contextlib import suppress

from pyalsoft import play, start_recording, stop_recording


def main() -> None:
    print("Recording from the default input device.")
    recording = start_recording()
    try:
        input("Speak now, then press Enter to stop... ")
    except BaseException:
        # Always release the native capture device, even on Ctrl+C or EOF.
        with suppress(Exception):
            stop_recording(recording)
        raise

    captured = stop_recording(recording)
    print(f"Captured {captured.duration:.2f} seconds. Playing it back...")
    sound = play(captured)
    sound.wait()
    print("Done.")


if __name__ == "__main__":
    main()
