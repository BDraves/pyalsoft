# PyALSoft

[![CI status](https://github.com/BDraves/pyalsoft/actions/workflows/ci.yml/badge.svg?branch=development)](https://github.com/BDraves/pyalsoft/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/pyalsoft.svg)](https://pypi.org/project/pyalsoft/)
[![Supported Python versions](https://img.shields.io/pypi/pyversions/pyalsoft.svg)](https://pypi.org/project/pyalsoft/)
<!-- openal-soft-version-badge:start -->
[![OpenAL Soft 1.25.2](https://img.shields.io/badge/OpenAL_Soft-1.25.2-557C94)](https://github.com/kcat/openal-soft/releases/tag/1.25.2)
<!-- openal-soft-version-badge:end -->

PyALSoft provides a function-oriented, managed playback API and typed bindings for
OpenAL Soft, including core OpenAL, ALC, EFX, and supported extensions. The
managed API lives at the package root; the complete low-level interface remains
available through `pyalsoft.bindings`.

> PyALSoft is an independent project and is not affiliated with or endorsed by the OpenAL Soft project.

## Installation

PyALSoft requires Python 3.12 or later.

```console
python3 -m pip install pyalsoft
```

## Quick start

`play` starts a WAV file immediately and returns an optional control handle:

```python
import time

from pyalsoft import play

sound = play("sound.wav")
while sound.playing:
    time.sleep(0.1)
```

Playback is asynchronous. If no control is needed, the return value can be
ignored without stopping the sound:

```python
play("notification.wav")
```

The returned `PlayingSound` has `pause()`, `resume()`, `stop()`, and
`set_config()` methods, plus `playing`, `state`, and `status` properties. The
convenience API supports uncompressed mono or stereo WAV files containing 8-bit
unsigned or 16-bit signed PCM. It opens its default audio session lazily, reuses
clips loaded from the same resolved path, and releases it automatically at
process exit. Applications can call `shutdown()` to close it earlier.

The complete example is available as [`examples/play_file.py`](examples/play_file.py)
and can be run from a source checkout with:

```console
uv run python examples/play_file.py
```

## Explicit playback sessions

Applications that generate audio, stream it, select a device, or require fully
explicit resource lifetimes can use the underlying managed API directly:

```python
from pyalsoft import PCM, open_playback, play, release, upload

pcm = PCM(
    # Half a second of mono silence; replace with your application's PCM bytes.
    samples=b"\0\0" * 22_050,
    channels=1,
    sample_rate=44_100,
)

with open_playback() as playback:
    clip = upload(playback, pcm)
    voice = play(playback, clip)
    # Query or control the voice here.
    release(playback, voice)
    release(playback, clip)
```

### Device selection and HRTF

Playback devices can be enumerated and passed to `open_playback`. Context
preferences such as HRTF are requested with `PlaybackConfig`; query
`PlaybackInfo` to see what the audio backend actually enabled:

```python
from pyalsoft import (
    PlaybackConfig,
    get_playback_info,
    list_playback_devices,
    open_playback,
)

devices = list_playback_devices()
selected = next((device for device in devices if device.is_default), None)

with open_playback(selected, config=PlaybackConfig(hrtf=True)) as playback:
    info = get_playback_info(playback)
    print(info.device_name, info.hrtf_status.value, info.hrtf_name)
```

See [`examples/play_sine.py`](examples/play_sine.py),
[`examples/move_sine.py`](examples/move_sine.py), and
[`examples/stream_sine.py`](examples/stream_sine.py) for complete explicit API
examples. Device selection and HRTF are demonstrated in
[`examples/select_device_hrtf.py`](examples/select_device_hrtf.py).

## API layers

Automatically generated ctypes bindings for OpenAL live at
`pyalsoft.bindings`. The same namespace also provides owned playback, capture,
loopback, and context handles for deterministic native resource lifetimes. See
the [owned backend handle guide](docs/backend.md) and the generated
[bindings reference](docs/reference.md).

`pyalsoft` holds the hand authored Python API, intended to make working with the library more Pythonic and manageable.

## Contributing

Direct development occurs on `development`, the base branch of the repository. Pull requests from there to master represent official releases, signified by a version increase in `pyproject.toml`. Version increases should only be done from `development`. If implementing your own feature, it is requested that you fork this repository and make your own feature branch, and then merge into `development`.

Create the locked development environment with [uv](https://docs.astral.sh/uv/):

```console
uv sync --python 3.12
```

Before submitting a change, run the same core checks as CI:

```console
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run python tools/generate_bindings.py --check
uv run python tools/sync_openal_soft.py --check
```

Bindings and [`docs/reference.md`](docs/reference.md) are generated from the
vendored OpenAL registry plus reviewed corrections in
[`tools/semantic_overrides.toml`](tools/semantic_overrides.toml). After changing
the generator, registry, or overrides, regenerate them with:

```console
uv run python tools/generate_bindings.py
```

See the [repository tool guide](tools/README.md) for the purpose and structure
of each development and release command.

## License

PyALSoft's original Python code is available under the MIT License. Bundled
OpenAL Soft and other third-party components remain under their respective
licenses. The distribution includes the complete license texts and a
third-party notice.
