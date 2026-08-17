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
[`stream_sine.py`](https://github.com/BDraves/pyalsoft/blob/development/examples/stream_sine.py),
and
[`select_device_hrtf.py`](https://github.com/BDraves/pyalsoft/blob/development/examples/select_device_hrtf.py)
examples.
