# PyALSoft

[![CI status](https://github.com/BDraves/pyalsoft/actions/workflows/ci.yml/badge.svg?branch=development)](https://github.com/BDraves/pyalsoft/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/pyalsoft.svg)](https://pypi.org/project/pyalsoft/)
[![Supported Python versions](https://img.shields.io/pypi/pyversions/pyalsoft.svg)](https://pypi.org/project/pyalsoft/)
<!-- openal-soft-version-badge:start -->
[![OpenAL Soft 1.25.2](https://img.shields.io/badge/OpenAL_Soft-1.25.2-557C94)](https://github.com/kcat/openal-soft/releases/tag/1.25.2)
<!-- openal-soft-version-badge:end -->

PyALSoft provides a functional, managed playback API and typed bindings for
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

This example generates a 440 Hz sine wave, plays it, and releases every native
resource when the `with` block exits:

```python
import math
import time
from array import array

from pyalsoft import (
    PCM,
    VoiceState,
    get_voice_status,
    open_playback,
    play,
    release,
    upload,
)

sample_rate = 44_100
duration = 0.5
pcm = array(
    "h",
    (
        round(((1 << 14) - 1) * math.sin(math.tau * 440 * frame / sample_rate))
        for frame in range(round(sample_rate * duration))
    ),
).tobytes()

audio = PCM(samples=pcm, channels=1, sample_rate=sample_rate)

with open_playback() as playback:
    clip = upload(playback, audio)
    voice = play(playback, clip)
    while get_voice_status(playback, voice).state is VoiceState.PLAYING:
        time.sleep(0.01)
    release(playback, voice)
    release(playback, clip)
```

The same example is available as [`examples/play_sine.py`](examples/play_sine.py)
and can be run from a source checkout with:

```console
uv run python examples/play_sine.py
```

## API layers

Automatically generated, CType bindings of OpenAL live at `pyalsoft.bindings`. Only experienced users should touch these. Most users can pretend they do not exist.

`pyalsoft` holds the hand authored Python API, intended to make working with the library more Pythonic and manageable.

## Contributing

Create the locked development environment with [uv](https://docs.astral.sh/uv/):

```console
uv sync --locked --python 3.12
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
