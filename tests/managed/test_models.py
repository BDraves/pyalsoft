"""Tests for managed audio values and validation."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from pyalsoft import (
    PCM,
    AudioBackendError,
    EffectSend,
    HighPassFilter,
    HRTFStatus,
    Listener,
    LowPassFilter,
    PlaybackConfig,
    PlaybackDevice,
    PlaybackOpenError,
    Reverb,
    SampleType,
    SoundInfo,
    VoiceConfig,
    bindings,
    get_playback_info,
    list_playback_devices,
    open_playback,
)
from tests._support.managed_backend import FakeLibrary, as_library


def test_pcm_and_configuration_are_immutable_data() -> None:
    pcm = PCM(
        samples=b"\x00\x00\x01\x00",
        channels=1,
        sample_rate=2,
        sample_type=SampleType.INT16,
    )
    config = VoiceConfig(position=(1, 2, 3))
    playback_config = PlaybackConfig(hrtf=True)
    device = PlaybackDevice("USB Headset", is_default=True)

    assert pcm.frame_count == 2
    assert pcm.duration == 1.0
    assert config.position == (1.0, 2.0, 3.0)
    assert playback_config.hrtf is True
    assert device.is_default
    assert replace(config, position=(4.0, 5.0, 6.0)).position == (
        4.0,
        5.0,
        6.0,
    )
    with pytest.raises(FrozenInstanceError):
        pcm.channels = 2  # type: ignore[misc]
    with pytest.raises(TypeError):
        VoiceConfig((1.0, 2.0, 3.0))  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        Listener((1.0, 2.0, 3.0))  # type: ignore[call-arg]
    with pytest.raises(FrozenInstanceError):
        playback_config.hrtf = False  # type: ignore[misc]
    with pytest.raises(TypeError, match="boolean or None"):
        PlaybackConfig(hrtf=1)  # type: ignore[arg-type]
    assert pcm.info == SoundInfo(
        channels=1,
        sample_rate=2,
        sample_type=SampleType.INT16,
        frame_count=2,
    )
    assert pcm.info.duration_seconds == 1.0
    assert pcm.info.bit_depth == 16
    assert pcm.info.byte_count == 4


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
    send = EffectSend(effect=reverb, filter=high_pass)
    config = VoiceConfig(filter=low_pass, effect_sends=(send,))

    assert reverb.decay_time == 2.0
    assert low_pass.high_frequency_gain == 0.25
    assert high_pass.low_frequency_gain == 0.4
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
    with pytest.raises(TypeError, match="high_frequency_decay_limit"):
        Reverb(high_frequency_decay_limit=1)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="effect must be a Reverb"):
        EffectSend(effect=low_pass)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="effect_sends must contain"):
        VoiceConfig(effect_sends=(reverb,))  # type: ignore[arg-type]


def test_devices_are_enumerated_and_consumed_by_open_playback() -> None:
    library = FakeLibrary()

    devices = list_playback_devices(library=as_library(library))

    assert devices == (
        PlaybackDevice("Speakers", is_default=True),
        PlaybackDevice("USB Headset"),
    )
    assert library.alc.string_list_queries == [bindings.ALC_ALL_DEVICES_SPECIFIER]

    with open_playback(
        devices[1],
        config=PlaybackConfig(hrtf=True),
        library=as_library(library),
    ) as playback:
        assert library.alc.opened_device_name == "USB Headset"
        assert library.alc.context_attributes == (bindings.ALC_HRTF_SOFT, 1)
        assert get_playback_info(playback).device_name == "USB Headset"


def test_device_enumeration_falls_back_to_core_specifiers() -> None:
    library = FakeLibrary()
    library.alc.extensions.remove("ALC_ENUMERATE_ALL_EXT")

    devices = list_playback_devices(library=as_library(library))

    assert devices[0].is_default
    assert library.alc.string_list_queries == [bindings.ALC_DEVICE_SPECIFIER]


def test_device_enumeration_reports_alc_errors() -> None:
    library = FakeLibrary()
    library.alc.string_list_error = bindings.ALC_INVALID_ENUM

    with pytest.raises(AudioBackendError, match="ALC INVALID_ENUM"):
        list_playback_devices(library=as_library(library))


def test_device_enumeration_clears_stale_alc_errors() -> None:
    library = FakeLibrary()
    library.alc.error = bindings.ALC_INVALID_VALUE

    assert list_playback_devices(library=as_library(library))


@pytest.mark.parametrize(("enabled", "native"), [(True, 1), (False, 0)])
def test_playback_config_requests_hrtf_when_supported(
    enabled: bool, native: int
) -> None:
    library = FakeLibrary()

    with open_playback(
        config=PlaybackConfig(hrtf=enabled), library=as_library(library)
    ):
        assert library.alc.context_attributes == (bindings.ALC_HRTF_SOFT, native)


def test_open_playback_reports_a_refused_native_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library = FakeLibrary()
    monkeypatch.setattr(library.alc, "open_device", lambda _name: None)

    with pytest.raises(PlaybackOpenError, match="open.*playback device"):
        open_playback(library=as_library(library))

    assert library.alc.destroyed_contexts == []
    assert library.alc.closed_devices == []


def test_open_playback_closes_device_when_context_creation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library = FakeLibrary()

    def refuse_context(
        _device: object,
        _attributes: tuple[int, ...] | None,
    ) -> None:
        return None

    monkeypatch.setattr(library.alc, "create_context", refuse_context)

    with pytest.raises(PlaybackOpenError, match="create.*context"):
        open_playback(library=as_library(library))

    assert library.alc.destroyed_contexts == []
    assert library.alc.closed_devices == [library.alc.device]
    assert library.alc.current_context is library.alc.previous_context


def test_open_playback_destroys_context_when_activation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library = FakeLibrary()

    def refuse_context(context: object | None) -> bool:
        if context is library.alc.context:
            return False
        library.alc.current_context = context
        return True

    monkeypatch.setattr(library.alc, "make_context_current", refuse_context)

    with pytest.raises(PlaybackOpenError, match="make.*context current"):
        open_playback(library=as_library(library))

    assert library.alc.destroyed_contexts == [library.alc.context]
    assert library.alc.closed_devices == [library.alc.device]
    assert library.alc.current_context is library.alc.previous_context


def test_playback_info_reports_backend_result_and_unavailable_hrtf() -> None:
    library = FakeLibrary()
    with open_playback(library=as_library(library)) as playback:
        info = get_playback_info(playback)

        assert info.renderer == "Fake OpenAL Renderer"
        assert info.version == "1.1 Fake OpenAL"
        assert info.hrtf_status is HRTFStatus.ENABLED
        assert info.hrtf_name == "Built-in HRTF"

    library = FakeLibrary()
    library.alc.extensions.remove("ALC_SOFT_HRTF")
    with open_playback(
        config=PlaybackConfig(hrtf=True), library=as_library(library)
    ) as playback:
        info = get_playback_info(playback)

        assert library.alc.context_attributes is None
        assert info.hrtf_status is HRTFStatus.UNAVAILABLE
        assert info.hrtf_name is None


def test_playback_info_preserves_unknown_future_hrtf_status() -> None:
    library = FakeLibrary()
    library.alc.hrtf_status = 0x7FFF

    with open_playback(library=as_library(library)) as playback:
        assert get_playback_info(playback).hrtf_status is HRTFStatus.UNKNOWN


def test_playback_info_reports_alc_errors() -> None:
    library = FakeLibrary()
    library.alc.hrtf_query_error = bindings.ALC_INVALID_ENUM

    with (
        open_playback(library=as_library(library)) as playback,
        pytest.raises(AudioBackendError, match="ALC INVALID_ENUM"),
    ):
        get_playback_info(playback)


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"samples": b"", "channels": 1, "sample_rate": 1}, "cannot be empty"),
        ({"samples": b"\0\0", "channels": 3, "sample_rate": 1}, "channels"),
        ({"samples": b"\0\0", "channels": 1, "sample_rate": 0}, "positive"),
        (
            {"samples": b"\0", "channels": 1, "sample_rate": 1},
            "whole number of frames",
        ),
    ],
)
def test_pcm_rejects_invalid_layouts(
    arguments: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        PCM(**arguments)  # type: ignore[arg-type]


@pytest.mark.parametrize("channels", [True, 1.0])
def test_pcm_and_sound_info_require_integer_channels(channels: object) -> None:
    with pytest.raises(TypeError, match="channels must be an integer"):
        PCM(b"\0\0", channels=channels, sample_rate=1)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="channels must be an integer"):
        SoundInfo(
            channels=channels,  # type: ignore[arg-type]
            sample_rate=1,
            sample_type=SampleType.INT16,
            frame_count=1,
        )
