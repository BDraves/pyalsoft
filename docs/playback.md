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
The budget measures decoded OpenAL buffer bytes, not compressed file size. A
compressed asset can therefore occupy much more cache memory than its file size,
and an active sound is intentionally allowed to keep the cache over budget.

## Static audio files

Convenience playback, [`load_audio()`][pyalsoft.load_audio],
[`get_sound_info()`][pyalsoft.get_sound_info], and explicit-session
[`upload()`][pyalsoft.upload] accept WAV, FLAC, MP3, and Ogg Vorbis files.
Formats are detected from file signatures. Ogg Opus and other Ogg codecs are
reported as unsupported rather than being treated as Vorbis.

| Input | Supported channels | Decoded sample type |
| --- | --- | --- |
| WAV PCM 8-bit | mono through 7.1 standard layouts | UINT8 |
| WAV PCM 16-bit | mono through 7.1 standard layouts | INT16 |
| WAV PCM 24/32-bit or IEEE float | mono through 7.1 standard layouts | FLOAT32 |
| FLAC through 16-bit | mono or stereo | INT16 |
| FLAC above 16-bit | mono or stereo | FLOAT32 |
| MP3 and Ogg Vorbis | mono or stereo | FLOAT32 |

Multichannel WAV files must use `WAVE_FORMAT_EXTENSIBLE` with an explicit
speaker mask matching OpenAL's quad, 5.1 rear, 6.1, or 7.1 channel order.
Ambiguous layouts and alternative masks such as 5.1 side are rejected instead
of being uploaded with incorrect speaker assignments.

Source sample rates are preserved. `SoundInfo.sample_type` and `bit_depth`
describe this decoded PCM representation, not a compressed bitrate. Static
files are decoded completely into memory and uploaded to static OpenAL buffers;
encoded input and decoded PCM are each limited to 512 MiB. This path is intended
for sound assets, not memory-bounded or gapless music streaming. WAV and FLAC
inspection reads container metadata without loading sample data. MP3 and Ogg
Vorbis inspection must scan the complete encoded file to determine exact frame
counts. Use [`open_stream()`][pyalsoft.open_stream] with application-provided PCM
chunks when bounded streaming is required.

## Control one sound

Every [`VoiceConfig`][pyalsoft.VoiceConfig] field can also be passed
directly to [`play()`][pyalsoft.play]. Direct keywords override the
corresponding field when both forms are used:

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

Use [`PlayingSound.wait()`][pyalsoft.PlayingSound.wait] when a thread should
block until playback ends. It returns `False` instead of raising when an
optional timeout expires:

```python
sound = play("notification.wav")
if not sound.wait(timeout=5.0):
    sound.stop()
```

For sounds attached to the player, UI sounds, and other sources that should not
use position, distance attenuation, directional cones, Doppler shift, or
positional panning, pass `spatialize=False` to [`play()`][pyalsoft.play]:

```python
from pyalsoft import play

footstep = play("footstep.wav", gain=0.8, spatialize=False)
```

This explicitly disables source spatialization. It does not disable sample-rate
conversion when the source and output-device rates differ. On an HRTF output,
OpenAL Soft can still render the resulting fixed local source through a
front-center HRTF response. The bundled runtime supports this behavior; a
separately installed OpenAL implementation must expose
`AL_SOFT_source_spatialize`.

When `spatialize` is omitted or `None`, OpenAL chooses automatically based on the
source format. Pass `True` or `False` only to override that backend decision.

To bypass HRTF virtualization completely, use direct-channel playback:

```python
from pyalsoft import play

footstep = play("footstep.wav", gain=0.8, direct_channels=True)
```

Direct-channel playback routes stereo channels to the matching outputs without
virtual-speaker rendering. For convenience playback, mono file and
[`PCM`][pyalsoft.PCM] sample frames are duplicated into identical left and
right channels before upload; the returned sound continues to report the
original source format. The normal and stereo-expanded forms of a cached file
occupy separate cache entries. Surround sources are rejected rather than
implicitly downmixed. With an explicit playback session, the
[`Clip`][pyalsoft.Clip] passed to `play()` must already be stereo. A separately
installed OpenAL implementation must expose `AL_SOFT_direct_channels`.

## Inspect a sound

Format and length information is available without opening an audio device:

```python
from pyalsoft import get_sound_info

info = get_sound_info("engine.wav")
print(info.duration_seconds, info.frame_count)
print(info.channels, info.sample_rate, info.bit_depth)
```

The same immutable [`SoundInfo`][pyalsoft.SoundInfo] is available as `sound.info`
and for clips uploaded from [`PCM`][pyalsoft.PCM]. Clips uploaded from
[`BufferData`][pyalsoft.BufferData] instead expose
[`BufferInfo`][pyalsoft.BufferInfo] as `clip.info`. When a sound ends,
`end_reason` distinguishes natural completion, an explicit `stop()`, runtime
shutdown, and a disconnected device when the backend supports connection
reporting.

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

## Advanced source controls

Extension-backed controls live on [`VoiceConfig`][pyalsoft.VoiceConfig] so they
work with explicit voices, streams, and convenience sounds. Defaults do not
require optional extensions; PyALSoft checks a capability only when its control
is requested.

They are also accepted directly by [`play()`][pyalsoft.play] and exposed as
properties and [`PlayingSound.update()`][pyalsoft.PlayingSound.update]
keywords. For nullable controls such as `distance_model`, `stereo_angles`,
`resampler`, and `super_stereo_width`, omission preserves the base or current
configuration while an explicit `None` clears the override.

```python
from pyalsoft import (
    DistanceModel,
    SpatializationMode,
    VoiceConfig,
    list_resamplers,
    open_playback,
    play,
    upload,
)

with open_playback() as playback:
    clip = upload(playback, mono_pcm)
    resampler = next(value for value in list_resamplers(playback) if value.is_default)
    voice = play(
        playback,
        clip,
        VoiceConfig(
            position=(8.0, 0.0, -12.0),
            distance_model=DistanceModel.EXPONENT_CLAMPED,
            radius=2.5,
            spatialization=SpatializationMode.ENABLED,
            resampler=resampler,
            air_absorption_factor=1.0,
            room_rolloff_factor=0.7,
        ),
    )
```

`distance_model=None` inherits the context model and follows later
[`set_acoustics()`][pyalsoft.set_acoustics] changes. `radius` describes the
emitter's apparent physical size; it does not replace `reference_distance` or
`max_distance`. Stereo angles are ordered left then right and expressed in
radians.

[`DirectChannelsMode`][pyalsoft.DirectChannelsMode] provides three routing
choices: normal virtualization, direct routing that drops unmatched channels,
and direct routing that remixes unmatched channels to available outputs. Direct
routing requires stereo managed audio and cannot be combined with forced
spatialization, stereo angles, or Super Stereo processing. The legacy
`direct_channels=True` keyword selects `DROP_UNMATCHED`.

Convenience playback transparently rebuilds mono audio as stereo when direct
routing is enabled, and restores the mono form when it is disabled. This works
for active sounds and for configuration changes stored until a later restart.

[`StereoMode.SUPER_STEREO`][pyalsoft.StereoMode] converts an ordinary stereo
signal to an orientable UHJ-derived soundfield. `super_stereo_width` ranges from
`0.0` for a focused front image through `1.0` for the widest supported image.
The stereo mode cannot be changed while its source is playing or paused.

Resamplers are implementation-provided values. Obtain them from
[`list_resamplers()`][pyalsoft.list_resamplers] for the active playback session
instead of constructing a [`Resampler`][pyalsoft.Resampler] directly. Air
absorption adds distance-dependent high-frequency attenuation. Room rolloff
attenuates auxiliary effect paths, so it is normally paired with a reverb
[`EffectSend`][pyalsoft.EffectSend]. Both factors range from `0.0` through
`10.0` and require EFX when nonzero.

The complete runnable example is
[`advanced_sources.py`](https://github.com/BDraves/pyalsoft/blob/development/examples/advanced_sources.py).

## Pause a playback device

[`pause_playback_device()`][pyalsoft.pause_playback_device] suspends device
processing without changing individual voice states. Use
[`resume_playback_device()`][pyalsoft.resume_playback_device] to continue. This
is useful while an application is in the background and requires
`ALC_SOFT_pause_device`. These operations are distinct from `pause()` and
`resume()`, which control one voice or stream.

## Precise playback timing

Three managed queries expose atomic timing pairs for synchronization work:

```python
from pyalsoft import get_playback_clock, get_voice_clock, get_voice_latency

latency = get_voice_latency(playback, voice)
print(latency.offset_seconds, latency.output_latency_ns)

source_clock = get_voice_clock(playback, voice)
print(source_clock.offset_seconds, source_clock.device_time_ns)

device_clock = get_playback_clock(playback)
print(device_clock.device_time_ns, device_clock.output_latency_ns)
```

The ordinary `VoiceStatus` offset reports where OpenAL is processing source
audio. `VoiceLatency` additionally reports how long audio at that offset will
take to reach the physical output. `VoiceClock` relates a source offset to the
audio device's clock, while `PlaybackClock` atomically measures that clock and
the device's current output latency. Source offsets retain OpenAL's exact 32.32
fixed-point value as `offset_frames_fixed`; clock and latency values remain
integer nanoseconds. The `offset_frames`, `offset_seconds`, and corresponding
`*_seconds` properties provide convenient floating-point conversions.

Use `delay_seconds` or `delay_frames` to prepend source-timeline silence:

```python
delayed = play(playback, clip, delay_seconds=0.25)
```

This delay is processed as silent source samples, so pitch and Doppler change
its real-time duration. While the silence is consumed, the voice is `PLAYING`
and its offset is negative. A delay cannot be combined with `offset_seconds` or
`offset_frames`. Both delay values must be non-negative, and `delay_frames`
cannot exceed `2**31`.

For a precise wall-clock start, schedule against the audio-device clock:

```python
clock = get_playback_clock(playback)
start_time_ns = clock.device_time_ns + 500_000_000
scheduled = play(playback, clip, start_time_ns=start_time_ns)
```

`start_time_ns` is an absolute device-clock value. A timestamp that has already
passed starts immediately. `restart()`, `PlayingSound.restart()`, and
`start_stream()` accept the same delay and scheduling keywords. These operations
require `AL_SOFT_source_start_delay`; clock queries additionally require
`ALC_SOFT_device_clock`.

## Apply playback changes together

Use `defer_updates()` when several listener, source, effect, play, or pause
changes must become audible together:

```python
from pyalsoft import Listener, VoiceConfig, defer_updates, set_listener
from pyalsoft import set_voice_config

with defer_updates(playback):
    set_listener(playback, Listener(position=(1.0, 0.0, 0.0)))
    set_voice_config(playback, voice, VoiceConfig(gain=0.5))
```

Audio continues rendering with the previous state inside the block, then all
pending changes are processed when the outermost block exits. Nested blocks are
safe. Omit `playback` to batch changes made through the convenience runtime.
This feature requires `AL_SOFT_deferred_updates`.

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
