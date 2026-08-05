# PyALSoft

[![CI status](https://github.com/BDraves/pyalsoft/actions/workflows/ci.yml/badge.svg?branch=development)](https://github.com/BDraves/pyalsoft/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/pyalsoft.svg)](https://pypi.org/project/pyalsoft/)
[![Supported Python versions](https://img.shields.io/pypi/pyversions/pyalsoft.svg)](https://pypi.org/project/pyalsoft/)
<!-- openal-soft-version-badge:start -->
[![OpenAL Soft 1.25.2](https://img.shields.io/badge/OpenAL_Soft-1.25.2-557C94)](https://github.com/kcat/openal-soft/releases/tag/1.25.2)
<!-- openal-soft-version-badge:end -->

PyALSoft provides automatically generated Python bindings for OpenAL Soft, including core OpenAL, ALC, EFX, and supported extensions.

> PyALSoft is an independent project and is not affiliated with or endorsed by the OpenAL Soft project.

## Installation

PyALSoft requires Python 3.12 or later.

```console
python3 -m pip install pyalsoft
```

## Quick start

This complete example generates a 440 Hz sine wave, plays it, and releases the
source, buffer, context, and device:

```python
import math
import time
from array import array

import pyalsoft

sample_rate = 44_100
duration = 0.5
pcm = array(
    "h",
    (
        round(((1 << 14) - 1) * math.sin(math.tau * 440 * frame / sample_rate))
        for frame in range(round(sample_rate * duration))
    ),
).tobytes()

library = pyalsoft.load()
device = library.alc.open_device(None)
if not device:
    raise RuntimeError("could not open the default OpenAL device")

context = library.alc.create_context(device, None)
if not context:
    library.alc.close_device(device)
    raise RuntimeError("could not create an OpenAL context")

buffer_id = None
source_id = None
try:
    if not library.alc.make_context_current(context):
        raise RuntimeError("could not make the OpenAL context current")

    (buffer_id,) = library.al.gen_buffers()
    library.al.buffer_data(
        buffer_id,
        pyalsoft.enums.ALFormat.FORMAT_MONO16,
        pcm,
        sample_rate,
    )

    (source_id,) = library.al.gen_sources()
    source = library.al.source(source_id)
    source.buffer = library.al.buffer(buffer_id)
    library.al.source_play(source_id)

    while source.state == pyalsoft.enums.ALSourceState.PLAYING:
        time.sleep(0.01)
finally:
    if source_id is not None:
        library.al.source_stop(source_id)
        library.al.delete_sources([source_id])
    if buffer_id is not None:
        library.al.delete_buffers([buffer_id])
    library.alc.make_context_current(None)
    library.alc.destroy_context(context)
    library.alc.close_device(device)
```

The same example is available as [`examples/play_sine.py`](examples/play_sine.py)
and can be run from a source checkout with:

```console
uv run python examples/play_sine.py
```

## Platforms

PyALSoft supports Windows x86-64, macOS x86-64 and ARM64, and Linux x86-64
and ARM64. Platform wheels bundle OpenAL Soft. `pyalsoft.load()` uses the
bundled library when available, then falls back to a system installation. Pass
an explicit library path to `pyalsoft.load(path)` to override discovery.

## API layers

The recommended `library.al` and `library.alc` namespaces use snake-case names,
accept Python strings and sequences, infer array lengths, allocate output
parameters, and return normal Python values. Generated object handles such as
`library.al.source(identifier)` expose typed properties including `gain`,
`position`, `buffer`, and `state`.

Exact C entry points remain available when low-level control is needed. For
example, `library.alGenSources` is the generated `ctypes` binding for
`alGenSources`, while `library.al.gen_sources()` is its Python-value wrapper.

Extensions are discoverable by registry name or generated attribute. Check an
extension against its device or current context before using its commands:

```python
efx = library.extensions.alc_ext_efx
if efx.is_present(device):
    print(efx.commands)
```

Unavailable libraries, missing contexts, and unsupported extensions raise
`LibraryNotFoundError`, `ContextRequiredError`, and
`ExtensionUnavailableError`, respectively. See the
[`generated API reference`](docs/reference.md) for the complete command,
property, and extension surface.

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
