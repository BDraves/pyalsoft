# Effects and filters

EFX configuration uses immutable values like the other managed playback
controls. Effects are routed through auxiliary
[`EffectSend`][pyalsoft.EffectSend] values; `filter` is the sound's direct
filter:

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

The managed API covers every core `ALC_EXT_EFX` effect:

- Room and delay: [`Reverb`][pyalsoft.Reverb],
  [`EAXReverb`][pyalsoft.EAXReverb], and [`Echo`][pyalsoft.Echo]
- Modulation: [`Chorus`][pyalsoft.Chorus] and [`Flanger`][pyalsoft.Flanger]
- Pitch and spectral processing: [`FrequencyShifter`][pyalsoft.FrequencyShifter],
  [`VocalMorpher`][pyalsoft.VocalMorpher],
  [`PitchShifter`][pyalsoft.PitchShifter], and
  [`RingModulator`][pyalsoft.RingModulator]
- Tone and dynamics: [`Distortion`][pyalsoft.Distortion],
  [`AutoWah`][pyalsoft.AutoWah], [`Compressor`][pyalsoft.Compressor], and
  [`Equalizer`][pyalsoft.Equalizer]

Discrete controls use managed enums instead of native OpenAL integers. For
example, chorus and flanger share [`ModulationWaveform`][pyalsoft.ModulationWaveform]:

```python
from pyalsoft import Chorus, EffectSend, ModulationWaveform

wide_chorus = EffectSend(
    effect=Chorus(
        waveform=ModulationWaveform.SINUSOID,
        rate=0.8,
        depth=0.3,
    )
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

Live sounds accept replacement values through `update`, including replacement
with a different effect type. Pass `filter=None` or an empty `effect_sends`
tuple to restore the dry, unfiltered signal:

```python
from pyalsoft import Chorus, EffectSend, HighPassFilter

sound.update(filter=HighPassFilter(low_frequency_gain=0.1))
sound.update(effect_sends=(EffectSend(effect=Chorus(rate=0.8)),))
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
[`cycle_effects.py`](https://github.com/BDraves/pyalsoft/blob/development/examples/cycle_effects.py),
as well as the
[`filter_sound.py`](https://github.com/BDraves/pyalsoft/blob/development/examples/filter_sound.py)
examples.
