# Recording

The managed capture API collects audio in memory while your application does
other work. Native capture buffers are drained on a background thread, so the
common API returns one [`PCM`][pyalsoft.PCM] value instead of exposing chunks:

```python
from pyalsoft import start_recording, stop_recording

recording = start_recording()
input("Speak now, then press Enter to stop... ")
captured = stop_recording(recording)
```

[`start_recording()`][pyalsoft.start_recording] uses the default input device
and records 48 kHz, mono, 16-bit PCM unless told otherwise. Use
[`list_capture_devices()`][pyalsoft.list_capture_devices] to select a specific
input. The bundled OpenAL Soft runtime accepts unsigned 8-bit, signed 16-bit,
and float32 capture in mono, stereo, quad, 5.1, 6.1, and 7.1 layouts; the
selected capture backend or hardware can still reject a requested combination.
For a known duration, [`record()`][pyalsoft.record] is the blocking equivalent.

## Bounded incremental capture

Long-running capture and real-time analysis should use a bounded
[`CaptureStream`][pyalsoft.CaptureStream]. Readers consume frames incrementally;
if they fall behind, the oldest frames are discarded and reported through
[`get_capture_stream_status()`][pyalsoft.get_capture_stream_status]:

```python
from pyalsoft import (
    get_capture_stream_status,
    read_capture_stream,
    start_capture_stream,
)

with start_capture_stream(capacity_frames=48_000) as stream:
    while should_continue:
        chunk = read_capture_stream(stream, max_frames=4_800, timeout=0.25)
        if chunk is not None:
            process(chunk)

    print("dropped frames:", get_capture_stream_status(stream).overrun_count)
```

The default capacity is one second at the default sample rate. This API never
grows its managed buffer beyond `capacity_frames`; the original `Recording` API
continues to retain every frame for callers that want one complete `PCM` value.

Captured and generated `PCM` values can be passed directly to
[`play()`][pyalsoft.play]:

```python
from pyalsoft import play

sound = play(captured)
```

See the runnable
[`record_and_play.py`](https://github.com/BDraves/pyalsoft/blob/development/examples/record_and_play.py)
example for the complete flow.
