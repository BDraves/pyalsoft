"""Exercise the bundled runtime from an installed or frozen PyALSoft build."""

from __future__ import annotations

import sys
from pathlib import Path

from pyalsoft import bindings, get_sound_info, load_audio
from pyalsoft._managed.sound import decoder
from pyalsoft.bindings import _library as runtime

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "audio"


def main(fixture_root: Path = DEFAULT_FIXTURE_ROOT) -> None:
    bundled = runtime._bundled_library_path()
    if bundled is None:
        raise RuntimeError("the platform wheel did not contain an OpenAL Soft runtime")
    decoder_path = decoder._decoder_library_path()
    if decoder_path is None:
        raise RuntimeError("the platform wheel did not contain an audio decoder")
    decoder._NativeDecoder(decoder_path)

    for filename in ("tone-s16.wav", "tone-s16.flac", "tone.mp3", "tone.ogg"):
        path = fixture_root / filename
        pcm = load_audio(path)
        if pcm.info != get_sound_info(path):
            raise RuntimeError(
                f"decoder probe disagrees with decoded PCM for {filename}"
            )

    library = bindings.load()
    if library.library_name != bundled:
        raise RuntimeError(f"loaded {library.library_name!r} instead of {bundled!r}")

    device = library.alcOpenDevice(None)
    if not device:
        raise RuntimeError("could not open the OpenAL null device")
    context = library.alcCreateContext(device, None)
    if not context:
        library.alcCloseDevice(device)
        raise RuntimeError("could not create an OpenAL context")
    try:
        if not library.alcMakeContextCurrent(context):
            raise RuntimeError("could not make the OpenAL context current")
    finally:
        library.alcMakeContextCurrent(None)
        library.alcDestroyContext(context)
        library.alcCloseDevice(device)

    print(f"loaded bundled OpenAL Soft runtime: {bundled}")
    print(f"loaded bundled static audio decoder: {decoder_path}")


if __name__ == "__main__":
    if len(sys.argv) > 2:
        raise SystemExit("usage: smoke_test_runtime.py [audio-fixture-directory]")
    main(Path(sys.argv[1]).resolve() if len(sys.argv) == 2 else DEFAULT_FIXTURE_ROOT)
