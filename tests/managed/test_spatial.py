"""Tests for managed audio values and validation."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from pyalsoft import (
    BandPassFilter,
    EffectSend,
    HighPassFilter,
    LowPassFilter,
    Reverb,
    VoiceConfig,
)


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"min_gain": -0.1}, "min_gain must be between"),
        ({"max_gain": 1.1}, "max_gain must be between"),
        ({"min_gain": 0.8, "max_gain": 0.2}, "min_gain cannot exceed"),
        ({"reference_distance": -1.0}, "reference_distance cannot be negative"),
        ({"max_distance": -1.0}, "max_distance cannot be negative"),
        ({"rolloff_factor": -1.0}, "rolloff_factor cannot be negative"),
        ({"cone_inner_angle": 361.0}, "cone_inner_angle must be between"),
        ({"cone_outer_angle": -1.0}, "cone_outer_angle must be between"),
        ({"cone_outer_gain": 1.1}, "cone_outer_gain must be between"),
    ],
)
def test_voice_config_rejects_invalid_spatial_controls(
    arguments: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        VoiceConfig(**arguments)  # type: ignore[arg-type]


def test_efx_descriptions_are_validated_immutable_values() -> None:
    reverb = Reverb(decay_time=2, high_frequency_decay_ratio=0.5)
    low_pass = LowPassFilter(gain=1, high_frequency_gain=0.25)
    high_pass = HighPassFilter(gain=1, low_frequency_gain=0.4)
    band_pass = BandPassFilter(
        gain=0.8,
        low_frequency_gain=0.4,
        high_frequency_gain=0.25,
    )
    send = EffectSend(effect=reverb, filter=band_pass)
    config = VoiceConfig(filter=low_pass, effect_sends=(send,))

    assert reverb.decay_time == 2.0
    assert low_pass.high_frequency_gain == 0.25
    assert high_pass.low_frequency_gain == 0.4
    assert band_pass.gain == 0.8
    assert band_pass.low_frequency_gain == 0.4
    assert band_pass.high_frequency_gain == 0.25
    assert config.effect_sends == (send,)
    assert replace(reverb, decay_time=3.0).decay_time == 3.0
    with pytest.raises(FrozenInstanceError):
        reverb.gain = 0.5  # type: ignore[misc]
    with pytest.raises(ValueError, match="decay_time must be between"):
        Reverb(decay_time=20.1)
    with pytest.raises(ValueError, match="high_frequency_gain must be between"):
        LowPassFilter(high_frequency_gain=-0.1)
    with pytest.raises(ValueError, match="low_frequency_gain must be between"):
        HighPassFilter(low_frequency_gain=1.1)
    with pytest.raises(ValueError, match="gain must be between"):
        BandPassFilter(gain=1.1)
    with pytest.raises(ValueError, match="low_frequency_gain must be between"):
        BandPassFilter(low_frequency_gain=-0.1)
    with pytest.raises(ValueError, match="high_frequency_gain must be between"):
        BandPassFilter(high_frequency_gain=1.1)
    with pytest.raises(TypeError, match="high_frequency_decay_limit"):
        Reverb(high_frequency_decay_limit=1)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="effect must be a Reverb"):
        EffectSend(effect=low_pass)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="effect_sends must contain"):
        VoiceConfig(effect_sends=(reverb,))  # type: ignore[arg-type]
