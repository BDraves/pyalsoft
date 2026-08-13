# Playback and spatial audio

## The convenience runtime

[`play()`][pyalsoft.play] opens its default audio session lazily. It reuses clips
loaded from the same resolved path and releases the session automatically at
process exit. Call [`shutdown()`][pyalsoft.shutdown] when an application needs
to close it earlier.

File clips use a 64 MiB least-recently-used byte budget. Clips attached to
active sounds remain pinned until those sounds stop. Cache policy and explicit
eviction remain function-oriented:

```python
from pyalsoft import (
    clear_sound_cache,
    get_sound_cache_info,
    set_sound_cache_limit,
)

set_sound_cache_limit(128 * 1024 * 1024)  # None selects an unlimited cache.
print(get_sound_cache_info())
clear_sound_cache("notification.wav")
```

Clearing an active clip marks it for eviction after its final sound stops.

## Control one sound

Every [`VoiceConfig`][pyalsoft.VoiceConfig] field can also be passed directly to
[`play()`][pyalsoft.play]. Direct keywords override the corresponding field when
both forms are used:

```python
from pyalsoft import play

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
sound.update(
    position=(3.0, 0.0, -2.0),
    velocity=(1.0, 0.0, 0.0),
    gain=0.6,
)
```

`offset_seconds` is the playhead position on the original source-audio
timeline, not elapsed wall-clock time. At `pitch=2.0`, for example, the offset
advances two source seconds per wall-clock second while `duration_seconds`
remains unchanged. For sample-accurate work, use `offset_frames`,
`remaining_frames`, `frame_count`, and `seek_frames()`.

`rewind()` follows OpenAL behavior by moving to the beginning and entering the
`INITIAL` state. `restart()` moves to the beginning and immediately plays.

For sounds attached to the player, UI sounds, and other sources that should not
use position, distance attenuation, directional cones, Doppler shift, or HRTF
positioning, use [`play_stationary()`][pyalsoft.play_stationary]:

```python
from pyalsoft import play_stationary

footstep = play_stationary("footstep.wav", gain=0.8)
```

This explicitly disables source spatialization. It does not disable sample-rate
conversion when the source and output-device rates differ. The bundled runtime
supports this behavior; a separately installed OpenAL implementation must expose
`AL_SOFT_source_spatialize`.

## Inspect a sound

Format and length information is available without opening an audio device:

```python
from pyalsoft import get_sound_info

info = get_sound_info("engine.wav")
print(info.duration_seconds, info.frame_count)
print(info.channels, info.sample_rate, info.bit_depth)
```

The same immutable [`SoundInfo`][pyalsoft.SoundInfo] is available as `clip.info`
and `sound.info`. When a sound ends, `end_reason` distinguishes natural
completion, an explicit `stop()`, runtime shutdown, and a disconnected device
when the backend supports connection reporting.

## Spatial controls

The spatial controls describe a sound relative to the playback listener:

| Control | Meaning |
| --- | --- |
| `position` | The sound's `(x, y, z)` location. By default, +X is right, +Y is up, and -Z is forward. |
| `velocity` | Motion used for Doppler shift. It does not automatically update `position`. |
| `direction` | The vector the sound's directional cone points along. `(0, 0, 0)` makes it omnidirectional. |
| `relative` | Selects listener-local coordinates instead of world coordinates. |
| `reference_distance` | The reference point where distance attenuation has unity gain. |
| `max_distance` | The outer distance used by clamped distance models. |
| `rolloff_factor` | Scales distance attenuation; `0` disables distance rolloff. |
| `min_gain`, `max_gain` | Lower and upper gain clamps. |
| `cone_inner_angle`, `cone_outer_angle` | The full angles defining the directional cone. |
| `cone_outer_gain` | Gain multiplier outside the outer cone. |

Gain is a linear amplitude multiplier: `1.0` is unchanged, `0.5` is about
-6 dB, and `0.0` is silent. Pitch changes playback rate and audible pitch
together; OpenAL does not perform independent time stretching. Prefer mono
sounds for positional audio because OpenAL normally plays stereo sources
without applying 3D position or direction.

## Configure the listener

The default runtime's listener and global distance and Doppler behavior can be
configured without opening an explicit playback session:

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
explicit [`Playback`][pyalsoft.Playback] as the first argument to use that
session instead.

See the runnable
[`play_file.py`](https://github.com/BDraves/pyalsoft/blob/development/examples/play_file.py)
and
[`move_sine.py`](https://github.com/BDraves/pyalsoft/blob/development/examples/move_sine.py)
examples.
