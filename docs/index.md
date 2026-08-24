# PyALSoft

PyALSoft provides a function-oriented audio API for common playback and
recording tasks, along with typed low-level OpenAL Soft bindings. Wheels include
the native OpenAL Soft runtime and static-audio decoder, so separate native
installations are not normally required.

## Installation

PyALSoft requires Python 3.12 or later. Install it from PyPI:

```console
python -m pip install pyalsoft
```

## Play an audio file

[`play()`][pyalsoft.play] begins playback immediately and returns a
[`PlayingSound`][pyalsoft.PlayingSound] handle:

```python
from pyalsoft import play

sound = play("sound.wav")
sound.wait()
```

WAV, FLAC, MP3, and Ogg Vorbis assets are detected from their contents, so the
filename extension does not control decoding.

Playback is asynchronous. If you do not need to control the sound, you can
ignore the return value without stopping playback:

```python
play("notification.wav")
```

## Documentation

- [Playback and spatial audio](playback.md) covers sound controls, timelines,
  caching, listeners, and distance behavior.
- [Effects and filters](effects.md) covers EFX configuration and live updates.
- [Recording](recording.md) covers managed capture and in-memory PCM playback.
- [Explicit playback sessions](sessions.md) covers generated audio, streaming,
  device selection, live reconfiguration, HRTF, and deterministic resource
  lifetimes.
- The [API reference](api.md) documents every public class and function in the
  managed API.
- [Owned backend handles](backend.md), the
  [owned backend API reference](backend-api.md), and the
  [low-level bindings reference](reference.md) document direct OpenAL access.

Complete runnable programs are available in the
[examples directory](https://github.com/BDraves/pyalsoft/tree/development/examples).
