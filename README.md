# PyALSoft

[![CI status](https://github.com/BDraves/pyalsoft/actions/workflows/ci.yml/badge.svg?branch=development)](https://github.com/BDraves/pyalsoft/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/pyalsoft.svg)](https://pypi.org/project/pyalsoft/)
[![Supported Python versions](https://img.shields.io/pypi/pyversions/pyalsoft.svg)](https://pypi.org/project/pyalsoft/)
<!-- openal-soft-version-badge:start -->
[![OpenAL Soft 1.25.2](https://img.shields.io/badge/OpenAL_Soft-1.25.2-557C94)](https://github.com/kcat/openal-soft/releases/tag/1.25.2)
<!-- openal-soft-version-badge:end -->

PyALSoft provides function-oriented managed playback and capture APIs, plus typed
bindings for OpenAL Soft, including core OpenAL, ALC, EFX, and supported
extensions. The managed API lives at the package root; the complete low-level
interface remains available through `pyalsoft.bindings`.

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

The returned `PlayingSound` has transport, timeline, gain, pitch, looping, and
spatial controls. The convenience API supports uncompressed mono or stereo WAV
files containing 8-bit unsigned or 16-bit signed PCM. It opens its default audio
session lazily, reuses clips loaded from the same resolved path, and releases it
automatically at process exit. Applications can call `shutdown()` to close it
earlier.

### Controlling one sound

Every `VoiceConfig` field can also be passed directly to `play`. Direct keywords
override the corresponding field when both forms are used:

```python
sound = play(
    "engine.wav",
    gain=0.7,
    pitch=1.1,
    looping=True,
    relative=True,
    position=(-2.0, 0.0, -4.0),
    reference_distance=1.0,
    max_distance=20.0,
    rolloff_factor=1.0,
)

sound.position = (2.0, 0.0, -4.0)
sound.pitch = 1.25
sound.seek(3.0)

# Group changes made in the same application update. Only changed OpenAL
# properties are sent to the backend.
sound.update(
    position=(3.0, 0.0, -2.0),
    velocity=(1.0, 0.0, 0.0),
    gain=0.6,
)
```

`offset_seconds` is the playhead position on the original source-audio
timeline. It is not elapsed wall-clock time. For example, at `pitch=2.0` the
offset advances two source seconds per wall-clock second, while
`duration_seconds` remains unchanged. `remaining_seconds` and `progress` use
the same source timeline. For sample-accurate work, use `offset_frames`,
`remaining_frames`, `frame_count`, and `seek_frames()`. `rewind()` follows
OpenAL behavior by moving to the beginning and entering the `INITIAL` state;
`restart()` moves to the beginning and immediately plays.

Format and length information is available without opening an audio device:

```python
from pyalsoft import get_sound_info

info = get_sound_info("engine.wav")
print(info.duration_seconds, info.frame_count)
print(info.channels, info.sample_rate, info.bit_depth)
```

The same immutable `SoundInfo` is available as `clip.info` and `sound.info`.
`PlayingSound` also exposes `path`, `channels`, `sample_rate`, and `sample_type`.
When a sound ends, `end_reason` distinguishes natural completion, an explicit
`stop()`, runtime shutdown, and a disconnected device when the backend supports
connection reporting. Stopped sounds retain their configuration, so controls
can be changed before `rewind()` or `restart()` creates another native source.

The spatial controls describe a sound relative to the playback listener:

| Control | Meaning |
| --- | --- |
| `position` | The sound's `(x, y, z)` location. By default, +X is right, +Y is up, and -Z is forward. |
| `velocity` | Motion, in coordinate units per second, used for Doppler shift. It does not automatically update `position`. |
| `direction` | The vector the sound's directional cone points along. `(0, 0, 0)` makes it omnidirectional. |
| `relative` | When true, position, velocity, and direction use listener-local coordinates; otherwise they use world coordinates. |
| `reference_distance` | The reference point where distance attenuation has unity gain. Clamped models keep unity distance gain at closer distances. |
| `max_distance` | The outer distance used by clamped distance models; attenuation no longer changes beyond it. |
| `rolloff_factor` | Scales distance attenuation. `0` disables distance rolloff; larger values attenuate more rapidly. |
| `min_gain`, `max_gain` | Lower and upper clamps applied after distance and cone attenuation. |
| `cone_inner_angle` | Full angle, in degrees, inside which direction causes no cone attenuation. |
| `cone_outer_angle` | Full angle beyond which the outer-cone gain is used; OpenAL interpolates between the two angles. |
| `cone_outer_gain` | Gain multiplier used when the listener is outside the outer cone. |

Gain is a linear amplitude multiplier: `1.0` is unchanged, `0.5` is about -6 dB,
and `0.0` is silent. Pitch changes playback rate and audible pitch together;
OpenAL does not perform independent time stretching.

For conventional positional audio, use a mono sound. OpenAL normally plays
stereo sources without applying 3D position or direction.

The convenience runtime's listener and global distance/Doppler behavior can be
configured without opening an explicit `Playback`:

```python
from pyalsoft import (
    Acoustics,
    DistanceModel,
    Listener,
    set_acoustics,
    set_listener,
    update_listener,
)

set_listener(Listener(position=(0.0, 1.7, 0.0)))
set_acoustics(
    Acoustics(
        distance_model=DistanceModel.INVERSE_CLAMPED,
        doppler_factor=1.0,
        speed_of_sound=343.3,
    )
)
update_listener(position=(2.0, 1.7, 0.0))
```

`get_listener()`, `get_acoustics()`, `update_listener()`, and
`update_acoustics()` operate on the convenience runtime by default. Pass an
explicit `Playback` as the first argument to use that session instead.

The complete example is available as [`examples/play_file.py`](examples/play_file.py)
and can be run from a source checkout with:

```console
uv run python examples/play_file.py
```

## Recording

The managed capture API collects audio in memory while your application does
other work. Native capture buffers are drained on a background thread, so the
common API returns one `PCM` value instead of exposing chunks:

```python
from pyalsoft import start_recording, stop_recording

recording = start_recording()
input("Speak now, then press Enter to stop... ")
captured = stop_recording(recording)
```

`start_recording` uses the default input device and records 48 kHz, mono,
16-bit PCM unless told otherwise. Use `list_capture_devices()` to select a
specific input. For a known duration, `record(3.0)` is the blocking equivalent.
Captured and generated `PCM` values can be passed directly to `play`:

```python
sound = play(captured)
```

[`examples/record_and_play.py`](examples/record_and_play.py) records until Enter
is pressed and then plays the complete recording:

```console
uv run python examples/record_and_play.py
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
