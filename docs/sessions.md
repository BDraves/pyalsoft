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

## Device selection and HRTF

Playback devices can be enumerated and passed to
[`open_playback()`][pyalsoft.open_playback]. Context preferences such as HRTF
are requested with [`PlaybackConfig`][pyalsoft.PlaybackConfig]; query
[`PlaybackInfo`][pyalsoft.PlaybackInfo] to see what the audio backend actually
enabled:

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

See the runnable
[`play_sine.py`](https://github.com/BDraves/pyalsoft/blob/development/examples/play_sine.py),
[`stream_sine.py`](https://github.com/BDraves/pyalsoft/blob/development/examples/stream_sine.py),
and
[`select_device_hrtf.py`](https://github.com/BDraves/pyalsoft/blob/development/examples/select_device_hrtf.py)
examples.
