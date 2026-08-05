# PyALSoft

PyALSoft provides automatically generated Python bindings for OpenAL Soft, including core OpenAL, ALC, EFX, and supported extensions.

## Installation

PyALSoft requires Python 3.12 or later.

```console
python3 -m pip install pyalsoft
```

## Platforms

PyALSoft supports Windows x86-64, macOS x86-64 and ARM64, and Linux x86-64
and ARM64. Platform wheels bundle OpenAL Soft. `pyalsoft.load()` uses the
bundled library when available, then falls back to a system installation. Pass
an explicit library path to `pyalsoft.load(path)` to override discovery.

## Disclaimer

> PyALSoft is an independent project and is not affiliated with or endorsed by the OpenAL Soft project.

## License

PyALSoft's original Python code is available under the MIT License. Bundled
OpenAL Soft and other third-party components remain under their respective
licenses. The distribution includes the complete license texts and a
third-party notice.
