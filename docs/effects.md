# Effects and filters

EFX configuration uses immutable values like the other managed playback
controls. Reverb is routed through an auxiliary
[`EffectSend`][pyalsoft.EffectSend]; `filter` is the sound's direct filter:

```python
from pyalsoft import EffectSend, LowPassFilter, Reverb, play

room = Reverb(
    gain=0.2,
    decay_time=0.6,
    high_frequency_decay_ratio=0.8,
)
sound = play(
    "voice.wav",
    filter=LowPassFilter(high_frequency_gain=0.1),
    effect_sends=(EffectSend(effect=room),),
)
```

[`LowPassFilter`][pyalsoft.LowPassFilter],
[`HighPassFilter`][pyalsoft.HighPassFilter], and
[`BandPassFilter`][pyalsoft.BandPassFilter] expose EFX gain controls rather than
cutoff frequencies. Band-pass filters provide independent low- and
high-frequency gains. A filter may also be placed on an `EffectSend` to shape
only the wet signal. Send tuple order determines the native auxiliary-send
index, and the playback device determines how many simultaneous sends it
supports.

Live sounds accept replacement values through `update`. Pass `filter=None` or
an empty `effect_sends` tuple to restore the dry, unfiltered signal:

```python
from pyalsoft import HighPassFilter

sound.update(filter=HighPassFilter(low_frequency_gain=0.1))
sound.update(filter=None)
sound.effect_sends = ()
```

The same fields are available on [`VoiceConfig`][pyalsoft.VoiceConfig] for
explicit voices and streams. PyALSoft owns their native filters, effects, and
auxiliary slots and releases them with the voice. Configuring EFX raises
[`AudioBackendError`][pyalsoft.AudioBackendError] when the selected device does
not expose EFX or cannot provide the requested number of sends.

See the runnable
[`play_with_reverb.py`](https://github.com/BDraves/pyalsoft/blob/development/examples/play_with_reverb.py)
and
[`filter_sound.py`](https://github.com/BDraves/pyalsoft/blob/development/examples/filter_sound.py)
examples.
