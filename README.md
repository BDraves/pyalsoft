# PyALSoft

[![CI status](https://github.com/BDraves/pyalsoft/actions/workflows/ci.yml/badge.svg?branch=development)](https://github.com/BDraves/pyalsoft/actions/workflows/ci.yml)
[![Documentation](https://img.shields.io/badge/docs-GitHub_Pages-4051B5)](https://bdraves.github.io/pyalsoft/)
[![PyPI version](https://img.shields.io/pypi/v/pyalsoft.svg)](https://pypi.org/project/pyalsoft/)
[![Supported Python versions](https://img.shields.io/pypi/pyversions/pyalsoft.svg)](https://pypi.org/project/pyalsoft/)
<!-- openal-soft-version-badge:start -->
[![OpenAL Soft 1.25.2](https://img.shields.io/badge/OpenAL_Soft-1.25.2-557C94)](https://github.com/kcat/openal-soft/releases/tag/1.25.2)
<!-- openal-soft-version-badge:end -->

PyALSoft provides function-oriented playback and capture APIs, plus typed
bindings for OpenAL Soft. Wheels include the native OpenAL Soft runtime.

**[Read the documentation](https://bdraves.github.io/pyalsoft/)** ·
[Browse the API reference](https://bdraves.github.io/pyalsoft/api/) ·
[View the examples](https://github.com/BDraves/pyalsoft/tree/development/examples)

> PyALSoft is an independent project and is not affiliated with or endorsed by
> the OpenAL Soft project.

## Installation

PyALSoft requires Python 3.12 or later.

```console
python -m pip install pyalsoft
```

## Quick start

`play` starts a WAV file immediately and returns a control handle:

```python
import time

from pyalsoft import play

sound = play("sound.wav")
while sound.playing:
    time.sleep(0.1)
```

Use `play("sound.wav", spatialize=False)` for player-attached or UI audio that
should not receive positional, distance, Doppler, or HRTF processing.

The managed API also supports spatial audio, effects and filters, recording,
streaming, typed device and context configuration, live device
reconfiguration, HRTF profile selection, and explicit playback sessions. See the
[documentation](https://bdraves.github.io/pyalsoft/) for guides and the complete
API reference.

The lower-level `pyalsoft.bindings` namespace exposes typed OpenAL, ALC, EFX,
and extension bindings, together with owned device and context handles.

## Contributing

Development occurs on the `development` branch. Create the locked environment
with [uv](https://docs.astral.sh/uv/):

```console
uv sync --python 3.12
```

Before submitting a change, run the core checks:

```console
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run python tools/generate_bindings.py --check
uv run python tools/sync_openal_soft.py --check
uv run mkdocs build --strict
```

Bindings and `docs/reference.md` are generated from the vendored OpenAL
registry. See the [repository tool guide](tools/README.md) for development and
release commands.

Add an entry under `Unreleased` in [the changelog](CHANGELOG.md) for every
notable user-facing change. Group entries under `Added`, `Changed`,
`Deprecated`, `Removed`, `Fixed`, or `Security`, and describe the effect from
the user's perspective. Internal refactors, tests, and formatting do not need
entries.

For example:

```markdown
## Unreleased

### Added

- Added reverse playback support to `PlayingSound`. (@BDraves)

### Fixed

- Prevented audio frames from shifting left when replacing a sound's filter.
  (@BDraves)
```

## License

PyALSoft's original Python code is available under the MIT License. Bundled
OpenAL Soft and other third-party components remain under their respective
licenses. The distribution includes their license texts and a third-party
notice.
