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

Captured and generated `PCM` values can be passed directly to
[`play()`][pyalsoft.play]:

```python
from pyalsoft import play

sound = play(captured)
```

See the runnable
[`record_and_play.py`](https://github.com/BDraves/pyalsoft/blob/development/examples/record_and_play.py)
example for the complete flow.
