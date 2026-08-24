# Explicit playback sessions

Applications that generate audio, stream it, select a device, or require fully
explicit resource lifetimes can use the managed playback API directly:

```python
from pyalsoft import PCM, open_playback, play, release, upload

pcm = PCM(
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

Calls using an explicit [`Playback`][pyalsoft.Playback] are thread-safe.
PyALSoft serializes each complete operation for that session, including context
activation and managed state changes. Sessions backed by the same loaded OpenAL
library are also serialized because they share process-wide current-context
state; sessions on independent library instances may proceed concurrently.

[`close_playback()`][pyalsoft.close_playback] waits for an operation already in
progress and makes later operations fail with
[`PlaybackClosedError`][pyalsoft.PlaybackClosedError].
Use [`get_voice_config()`][pyalsoft.get_voice_config] to inspect the immutable
configuration currently retained for an explicit voice or stream.

Supported WAV, FLAC, MP3, and Ogg Vorbis files can be decoded and uploaded
directly while retaining the same explicit resource ownership:

```python
from pyalsoft import open_playback, play, upload, wait

with open_playback() as playback:
    clip = upload(playback, "notification.wav")
    voice = play(playback, clip)
    wait(playback, voice)
```

[`load_audio()`][pyalsoft.load_audio] exposes the same static decoder when an
application needs the intermediate [`PCM`][pyalsoft.PCM] value. Decoding is
whole-file and preserves the source sample rate. AAC/M4A, Opus, AIFF, metadata
tags, and decoder plugins are not part of this API.

## Loop regions

Pass a frame range to [`upload()`][pyalsoft.upload] to repeat only part of a
static clip. The start frame is inclusive and the end frame is exclusive:

```python
from pyalsoft import PCM, open_playback, play, upload

pcm = PCM(samples, channels=1, sample_rate=44_100)

with open_playback() as playback:
    clip = upload(
        playback,
        pcm,
        loop_points=(44_100, 88_200),
    )
    voice = play(playback, clip, looping=True)
```

Here the first second plays once, then frames 44,100 through 88,199 repeat.
Loop points affect only voices with looping enabled. Omitting them loops the
complete clip as before. A requested range must satisfy
`0 <= start < end <= pcm.frame_count` and requires `AL_SOFT_loop_points`.

## Extension buffer formats

[`PCM`][pyalsoft.PCM] supports unsigned 8-bit, signed 16-bit, float32, and
float64 samples. Standard interleaved mono, stereo, quad, 5.1, 6.1, and 7.1
layouts are accepted where OpenAL defines the combination. The managed upload
checks the required float or multichannel extension before allocating a clip:

```python
import array

from pyalsoft import PCM, SampleType, open_playback, upload

samples = array.array("f", [0.0] * (48_000 * 6)).tobytes()
pcm = PCM(samples, channels=6, sample_rate=48_000, sample_type=SampleType.FLOAT32)

with open_playback() as playback:
    clip = upload(playback, pcm)
```

Use [`BufferData`][pyalsoft.BufferData] when a payload needs an exact extension
format rather than a plain PCM layout. [`BufferFormat`][pyalsoft.BufferFormat]
covers the generated binding formats for float and double PCM, LOKI and EXT
IMA ADPCM, Microsoft ADPCM, mu-law and A-law, multichannel audio, Vorbis,
native WAVE, B-format ambisonics, and UHJ. Encoded data includes its decoded
`frame_count`, so managed duration, seeking, and stream queue timing do not
depend on the compressed byte length:

```python
from pyalsoft import BufferData, BufferFormat, open_playback, upload

data = BufferData(
    samples=ima_adpcm_bytes,
    format=BufferFormat.MONO_IMA4,
    sample_rate=48_000,
    frame_count=decoded_frame_count,
    block_alignment=samples_per_block,
)

with open_playback() as playback:
    clip = upload(playback, data)
```

The bundled OpenAL Soft runtime supports the EXT IMA4 format used above. The
legacy Vorbis, native WAVE, and LOKI formats are available through this API only
when a separately installed OpenAL implementation advertises their corresponding
extensions; the bundled runtime does not advertise them.

`block_alignment` configures `AL_SOFT_block_alignment`. B-format data also
accepts `ambisonic_order`, [`AmbisonicLayout`][pyalsoft.AmbisonicLayout], and
[`AmbisonicScaling`][pyalsoft.AmbisonicScaling]; PyALSoft applies the associated
buffer properties before upload. Orders above 3 require explicit ACN layout
and either SN3D or N3D scaling; FuMa layout and scaling remain valid through
order 3. `open_stream()` accepts the same `format` and property options. Pass
`frame_count=` to `try_write_stream()` for each encoded chunk; fixed-width
chunks infer their frame count from their byte length.

[`write_stream()`][pyalsoft.write_stream] is the blocking counterpart to
`try_write_stream()`. It reclaims processed buffers while waiting and returns
`False` if its optional timeout expires. After `finish_stream()`, use
[`wait()`][pyalsoft.wait] to block until queued audio drains. `wait()` also
supports static voices and uses portable state polling rather than requiring an
optional native event extension.

These are input-buffer capabilities. `ALC_LOKI_audio_channel` contributes
legacy channel constants but declares no callable selection function in the
OpenAL registry; those constants remain available through `pyalsoft.bindings`.

## Offline rendering

[`open_offline_playback()`][pyalsoft.open_offline_playback] creates a managed
session that renders deterministically into memory instead of opening audio
hardware. Clips, voices, streams, listeners, and effects use the same operations
as an ordinary [`Playback`][pyalsoft.Playback]:

```python
from pyalsoft import (
    RenderChannelLayout,
    RenderConfig,
    RenderSampleType,
    open_offline_playback,
    play,
    render_samples,
    upload,
)

config = RenderConfig(
    sample_rate=48_000,
    channels=RenderChannelLayout.STEREO,
    sample_type=RenderSampleType.INT16,
)
with open_offline_playback(config) as playback:
    clip = upload(playback, pcm)
    play(playback, clip)
    output = render_samples(playback, 48_000)
```

`render_samples()` advances the audio device by exactly the requested number of
frames and returns interleaved bytes in the configured format. Mono, stereo,
quad, 5.1, 6.1, 7.1, and 3D B-format output are supported when the backend
accepts them. B-format output additionally configures ambisonic order, layout,
and scaling through `ALC_SOFT_loopback_bformat`.

See the runnable
[`render_offline.py`](https://github.com/BDraves/pyalsoft/blob/development/examples/render_offline.py)
example, which can optionally write the result to a WAV file.

## Device and context configuration

Playback devices can be enumerated and passed to
[`open_playback()`][pyalsoft.open_playback]. Device and context preferences are
requested with [`PlaybackConfig`][pyalsoft.PlaybackConfig]; query
[`PlaybackInfo`][pyalsoft.PlaybackInfo] to see what the audio backend actually
enabled. Requests are hints, so the backend may select a different effective
value. Leaving a field as `None` preserves its default.

```python
from pyalsoft import (
    PlaybackConfig,
    PlaybackOutputMode,
    get_playback_config,
    get_playback_info,
    list_hrtf_profiles,
    list_playback_devices,
    open_playback,
    reconfigure_playback,
)

devices = list_playback_devices()
selected = next((device for device in devices if device.is_default), None)
profiles = list_hrtf_profiles(selected)

config = PlaybackConfig(
    sample_rate=48_000,
    mono_sources=128,
    stereo_sources=8,
    max_auxiliary_sends=2,
    hrtf=True,
    hrtf_name=profiles[0] if profiles else None,
    output_limiter=True,
    output_mode=PlaybackOutputMode.STEREO_HRTF,
)
with open_playback(selected, config=config) as playback:
    info = get_playback_info(playback)
    print(info.device_name, info.sample_rate, info.output_mode)
    print(info.hrtf_status.value, info.hrtf_name)

    # Change selected settings while clips, voices, and streams remain valid.
    reconfigure_playback(
        playback,
        PlaybackConfig(sample_rate=44_100, hrtf=False),
    )
    print(get_playback_config(playback))

    # Replace the complete request, returning omitted fields to backend defaults.
    reconfigure_playback(
        playback,
        PlaybackConfig(output_mode=PlaybackOutputMode.STEREO_BASIC),
        replace=True,
    )
```

[`reconfigure_playback()`][pyalsoft.reconfigure_playback] resets the live
device without closing its context or invalidating managed resources. In a
reconfiguration, each `None` field is an omitted update that preserves the
session's previous request. Pass `replace=True` to treat the supplied
configuration as the complete new request; its `None` fields then return to
backend-selected behavior. [`get_playback_config()`][pyalsoft.get_playback_config]
reports the retained request, while
[`get_playback_info()`][pyalsoft.get_playback_info] reports the effective values
negotiated by the backend. The reset may briefly interrupt output. Live
reconfiguration requires `ALC_SOFT_HRTF`, which is provided by the bundled
OpenAL Soft runtime.

[`reopen_playback()`][pyalsoft.reopen_playback] instead migrates a live session
to another output device while retaining clips, voices, streams, and effect
buses. It preserves the requested configuration except for `hrtf_name`, which
is cleared because profiles are device-specific. Enumerate the new device's
profiles and call `reconfigure_playback()` afterward when a named profile is
required.

Connection state is exposed as `PlaybackInfo.connected` and through
[`is_playback_connected()`][pyalsoft.is_playback_connected]. Both return `None`
when the selected backend lacks `ALC_EXT_disconnect`.

## Device-list events

[`subscribe_device_events()`][pyalsoft.subscribe_device_events] creates a
bounded queue of playback and capture device additions, removals, and default
changes. OpenAL's native callback only enqueues immutable events; application
code calls `subscription.next()` from its own thread:

```python
from pyalsoft import subscribe_device_events

with subscribe_device_events() as events:
    event = events.next(timeout=5.0)
    if event is not None:
        print(event.type.value, event.device_kind.value, event.name)
```

Only one system-event owner can use a loaded native OpenAL library at a time.
The queue discards its oldest event when full and exposes the loss count as
`dropped_count`.

The available requests and observations are:

| Configuration | Playback information | Availability |
| --- | --- | --- |
| `sample_rate` | `sample_rate` | OpenAL core |
| `refresh_rate` | `refresh_rate` | OpenAL core; accepted but ignored by OpenAL Soft |
| `synchronous` | `synchronous` | OpenAL core; accepted but ignored by OpenAL Soft |
| `mono_sources` | `mono_sources` | OpenAL core |
| `stereo_sources` | `stereo_sources` | OpenAL core |
| `max_auxiliary_sends` | `max_auxiliary_sends` | `ALC_EXT_EFX` |
| `hrtf` and `hrtf_name` | `hrtf_status` and `hrtf_name` | `ALC_SOFT_HRTF` |
| `output_limiter` | `output_limiter` | `ALC_SOFT_output_limiter` |
| `output_mode` | `output_mode` | `ALC_SOFT_output_mode` |

Optional-extension requests are omitted when the selected device does not
support the corresponding extension. Their observed values are `None`, except
for `hrtf_status`, which is `HRTFStatus.UNAVAILABLE`. HRTF profile names come
from [`list_hrtf_profiles()`][pyalsoft.list_hrtf_profiles]; names are resolved to
the backend's transient numeric identifiers on the same device used to create
the context.

See the runnable
[`play_sine.py`](https://github.com/BDraves/pyalsoft/blob/development/examples/play_sine.py),
[`loop_points.py`](https://github.com/BDraves/pyalsoft/blob/development/examples/loop_points.py),
[`stream_sine.py`](https://github.com/BDraves/pyalsoft/blob/development/examples/stream_sine.py),
and
[`select_device_hrtf.py`](https://github.com/BDraves/pyalsoft/blob/development/examples/select_device_hrtf.py)
examples.
