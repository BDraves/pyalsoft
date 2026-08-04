# PyALSoft

PyALSoft provides automatically generated Python bindings for OpenAL Soft, including core OpenAL, ALC, EFX, and supported extensions.

## Installation

PyALSoft requires Python 3.12 or later.

```console
python -m pip install uv
uv sync
```

## Platforms

PyALSoft supports Windows x86-64, macOS x86-64 and ARM64, and Linux x86-64
and ARM64. Platform wheels bundle OpenAL Soft. `pyalsoft.load()` uses the
bundled library when available, then falls back to a system installation. Pass
an explicit library path to `pyalsoft.load(path)` to override discovery.

## Disclaimer

> PyALSoft is an independent project and is not affiliated with or endorsed by the OpenAL Soft project.
