"""Tests for the functional managed playback API."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from threading import Event, RLock
from typing import cast

import pytest

import pyalsoft._playback as playback_module
from pyalsoft import (
    PCM,
    Acoustics,
    AudioBackendError,
    Clip,
    DistanceModel,
    EffectSend,
    HighPassFilter,
    HRTFStatus,
    InvalidHandleError,
    InvalidVoiceStateError,
    Listener,
    LowPassFilter,
    PlaybackClosedError,
    PlaybackConfig,
    PlaybackDevice,
    PlaybackOpenError,
    ResourceInUseError,
    Reverb,
    SampleType,
    SoundInfo,
    StreamState,
    VoiceConfig,
    VoiceState,
    VoiceStatus,
    bindings,
    close_playback,
    finish_stream,
    get_acoustics,
    get_listener,
    get_playback_info,
    get_voice_status,
    list_playback_devices,
    open_playback,
    open_stream,
    pause,
    play,
    release,
    release_finished,
    restart,
    resume,
    rewind,
    seek,
    seek_frames,
    set_acoustics,
    set_listener,
    set_voice_config,
    start_stream,
    stop,
    try_write_stream,
    update_stream,
    upload,
)


class FakeAL:
    def __init__(self) -> None:
        self.next_buffer = 1
        self.next_source = 100
        self.next_effect = 200
        self.next_filter = 300
        self.next_effect_slot = 400
        self.allocated_buffers: set[int] = set()
        self.allocated_effects: set[int] = set()
        self.allocated_filters: set[int] = set()
        self.allocated_effect_slots: set[int] = set()
        self.buffers: dict[int, tuple[int, bytes, int]] = {}
        self.effects: dict[int, dict[int, object]] = {}
        self.filters: dict[int, dict[int, object]] = {}
        self.effect_slots: dict[int, dict[int, object]] = {}
        self.sources: dict[int, dict[int, object]] = {}
        self.source_sends: dict[tuple[int, int], tuple[int, int]] = {}
        self.states: dict[int, int] = {}
        self.queues: dict[int, list[int]] = {}
        self.processed: dict[int, int] = {}
        self.offsets: dict[int, float] = {}
        self.frame_offsets: dict[int, int] = {}
        self.play_calls: list[int] = []
        self.sample_offset_calls: list[tuple[int, int]] = []
        self.source_property_calls: list[tuple[int, int]] = []
        self.listener: dict[int, object] = {}
        self.context_floats = {
            bindings.AL_DOPPLER_FACTOR: 1.0,
            bindings.AL_SPEED_OF_SOUND: 343.3,
        }
        self.distance_model_value = bindings.AL_INVERSE_DISTANCE_CLAMPED
        self.error = bindings.AL_NO_ERROR
        self.strings = {
            bindings.AL_RENDERER: "Fake OpenAL Renderer",
            bindings.AL_VERSION: "1.1 Fake OpenAL",
        }

    def get_error(self) -> int:
        error, self.error = self.error, bindings.AL_NO_ERROR
        return error

    def get_string(self, parameter: int) -> str | None:
        return self.strings.get(parameter)

    def gen_buffers(self, count: int = 1) -> tuple[int, ...]:
        identifiers = tuple(range(self.next_buffer, self.next_buffer + count))
        self.next_buffer += count
        self.allocated_buffers.update(identifiers)
        return identifiers

    def delete_buffers(self, buffers: tuple[int, ...]) -> None:
        for identifier in buffers:
            self.allocated_buffers.discard(identifier)
            self.buffers.pop(identifier, None)

    def buffer_data(
        self, identifier: int, format_name: int, data: bytes, sample_rate: int
    ) -> None:
        self.buffers[identifier] = (int(format_name), bytes(data), sample_rate)

    def gen_effects(self, count: int = 1) -> tuple[int, ...]:
        identifiers = tuple(range(self.next_effect, self.next_effect + count))
        self.next_effect += count
        self.allocated_effects.update(identifiers)
        for identifier in identifiers:
            self.effects[identifier] = {}
        return identifiers

    def delete_effects(self, effects: tuple[int, ...]) -> None:
        for identifier in effects:
            self.allocated_effects.discard(identifier)
            self.effects.pop(identifier, None)

    def effecti(self, identifier: int, parameter: int, value: int) -> None:
        self.effects[identifier][parameter] = value

    def effectf(self, identifier: int, parameter: int, value: float) -> None:
        self.effects[identifier][parameter] = value

    def gen_filters(self, count: int = 1) -> tuple[int, ...]:
        identifiers = tuple(range(self.next_filter, self.next_filter + count))
        self.next_filter += count
        self.allocated_filters.update(identifiers)
        for identifier in identifiers:
            self.filters[identifier] = {}
        return identifiers

    def delete_filters(self, filters: tuple[int, ...]) -> None:
        for identifier in filters:
            self.allocated_filters.discard(identifier)
            self.filters.pop(identifier, None)

    def filteri(self, identifier: int, parameter: int, value: int) -> None:
        self.filters[identifier][parameter] = value

    def filterf(self, identifier: int, parameter: int, value: float) -> None:
        self.filters[identifier][parameter] = value

    def gen_auxiliary_effect_slots(self, count: int = 1) -> tuple[int, ...]:
        identifiers = tuple(range(self.next_effect_slot, self.next_effect_slot + count))
        self.next_effect_slot += count
        self.allocated_effect_slots.update(identifiers)
        for identifier in identifiers:
            self.effect_slots[identifier] = {}
        return identifiers

    def delete_auxiliary_effect_slots(self, slots: tuple[int, ...]) -> None:
        for identifier in slots:
            self.allocated_effect_slots.discard(identifier)
            self.effect_slots.pop(identifier, None)

    def auxiliary_effect_sloti(
        self, identifier: int, parameter: int, value: int
    ) -> None:
        self.effect_slots[identifier][parameter] = value

    def gen_sources(self, count: int = 1) -> tuple[int, ...]:
        identifiers = tuple(range(self.next_source, self.next_source + count))
        self.next_source += count
        for identifier in identifiers:
            self.sources[identifier] = {}
            self.states[identifier] = bindings.AL_INITIAL
        return identifiers

    def delete_sources(self, sources: tuple[int, ...]) -> None:
        for identifier in sources:
            self.sources.pop(identifier, None)
            self.states.pop(identifier, None)
            self.queues.pop(identifier, None)
            self.processed.pop(identifier, None)
            self.offsets.pop(identifier, None)
            self.frame_offsets.pop(identifier, None)
            for key in tuple(self.source_sends):
                if key[0] == identifier:
                    del self.source_sends[key]

    def source3f(
        self, identifier: int, parameter: int, x: float, y: float, z: float
    ) -> None:
        self.sources[identifier][parameter] = (x, y, z)
        self.source_property_calls.append((identifier, parameter))

    def sourcef(self, identifier: int, parameter: int, value: float) -> None:
        self.sources[identifier][parameter] = value
        self.source_property_calls.append((identifier, parameter))
        if parameter == bindings.AL_SEC_OFFSET:
            self.offsets[identifier] = value
            self.frame_offsets[identifier] = round(
                value * self._source_sample_rate(identifier)
            )

    def sourcei(self, identifier: int, parameter: int, value: int) -> None:
        self.sources[identifier][parameter] = value
        self.source_property_calls.append((identifier, parameter))
        if parameter == bindings.AL_SAMPLE_OFFSET:
            self.sample_offset_calls.append((identifier, value))
            self.frame_offsets[identifier] = value
            self.offsets[identifier] = value / self._source_sample_rate(identifier)

    def source3i(
        self,
        identifier: int,
        parameter: int,
        value1: int,
        value2: int,
        value3: int,
    ) -> None:
        assert parameter == bindings.AL_AUXILIARY_SEND_FILTER
        self.source_sends[(identifier, value2)] = (value1, value3)
        self.source_property_calls.append((identifier, parameter))

    def _source_sample_rate(self, identifier: int) -> int:
        attached = self.sources[identifier].get(bindings.AL_BUFFER)
        buffer = self.queues[identifier][0] if attached is None else cast(int, attached)
        return self.buffers[buffer][2]

    def source_play(self, identifier: int) -> None:
        self.play_calls.append(identifier)
        if self.states[identifier] == bindings.AL_STOPPED:
            self.processed[identifier] = 0
        self.states[identifier] = bindings.AL_PLAYING

    def source_pause(self, identifier: int) -> None:
        self.states[identifier] = bindings.AL_PAUSED

    def source_stop(self, identifier: int) -> None:
        self.states[identifier] = bindings.AL_STOPPED
        if identifier in self.queues:
            self.processed[identifier] = len(self.queues[identifier])

    def source_rewind(self, identifier: int) -> None:
        self.states[identifier] = bindings.AL_INITIAL
        self.offsets[identifier] = 0.0
        self.frame_offsets[identifier] = 0

    def source_stopv(self, sources: tuple[int, ...]) -> None:
        for identifier in sources:
            self.source_stop(identifier)

    def get_sourcei(self, identifier: int, parameter: int) -> int:
        if parameter == bindings.AL_SOURCE_STATE:
            return self.states[identifier]
        if parameter == bindings.AL_BUFFERS_PROCESSED:
            return self.processed.get(identifier, 0)
        if parameter == bindings.AL_BUFFERS_QUEUED:
            return len(self.queues.get(identifier, ()))
        if parameter == bindings.AL_SAMPLE_OFFSET:
            default = round(
                self.offsets.get(identifier, 0.25)
                * self._source_sample_rate(identifier)
            )
            return self.frame_offsets.get(identifier, default)
        raise AssertionError(f"unexpected integer source parameter {parameter}")

    def get_sourcef(self, identifier: int, parameter: int) -> float:
        assert parameter == bindings.AL_SEC_OFFSET
        if identifier in self.queues:
            return self.offsets.get(identifier, 0.0)
        return self.offsets.get(identifier, 0.25)

    def source_queue_buffers(self, identifier: int, buffers: tuple[int, ...]) -> None:
        self.queues.setdefault(identifier, []).extend(buffers)
        self.processed.setdefault(identifier, 0)

    def source_unqueue_buffers(self, identifier: int, count: int) -> tuple[int, ...]:
        assert count <= self.processed.get(identifier, 0)
        queue = self.queues[identifier]
        returned = tuple(queue[:count])
        del queue[:count]
        self.processed[identifier] -= count
        self.offsets[identifier] = 0.0
        return returned

    def listener3f(self, parameter: int, x: float, y: float, z: float) -> None:
        self.listener[parameter] = (x, y, z)

    def listenerfv(self, parameter: int, values: tuple[float, ...]) -> None:
        self.listener[parameter] = values

    def listenerf(self, parameter: int, value: float) -> None:
        self.listener[parameter] = value

    def get_listener3f(self, parameter: int) -> tuple[float, float, float]:
        value = self.listener.get(parameter, (0.0, 0.0, 0.0))
        return cast(tuple[float, float, float], value)

    def get_listenerfv(self, parameter: int, size: int) -> tuple[float, ...]:
        assert size == 6
        value = self.listener.get(parameter, (0.0, 0.0, -1.0, 0.0, 1.0, 0.0))
        return cast(tuple[float, ...], value)

    def get_listenerf(self, parameter: int) -> float:
        return cast(float, self.listener.get(parameter, 1.0))

    def distance_model(self, value: int) -> None:
        self.distance_model_value = value

    def doppler_factor(self, value: float) -> None:
        self.context_floats[bindings.AL_DOPPLER_FACTOR] = value

    def speed_of_sound(self, value: float) -> None:
        self.context_floats[bindings.AL_SPEED_OF_SOUND] = value

    def get_integer(self, parameter: int) -> int:
        assert parameter == bindings.AL_DISTANCE_MODEL
        return self.distance_model_value

    def get_float(self, parameter: int) -> float:
        return self.context_floats[parameter]


class FakeALC:
    def __init__(self) -> None:
        self.device = object()
        self.context = object()
        self.previous_context = object()
        self.current_context: object | None = self.previous_context
        self.destroyed_contexts: list[object] = []
        self.closed_devices: list[object] = []
        self.device_names = ("Speakers", "USB Headset")
        self.default_device_name = "Speakers"
        self.opened_device_name: str | bytes | None = None
        self.context_attributes: tuple[int, ...] | None = None
        self.extensions = {
            "ALC_ENUMERATE_ALL_EXT",
            "ALC_EXT_EFX",
            "ALC_SOFT_HRTF",
        }
        self.max_auxiliary_sends = 2
        self.string_list_queries: list[int] = []
        self.hrtf_status = bindings.ALC_HRTF_ENABLED_SOFT
        self.hrtf_name: str | None = "Built-in HRTF"
        self.connected = True
        self.error = bindings.ALC_NO_ERROR
        self.string_list_error = bindings.ALC_NO_ERROR
        self.hrtf_query_error = bindings.ALC_NO_ERROR

    def get_current_context(self) -> object | None:
        return self.current_context

    def open_device(self, device_name: str | bytes | None) -> object:
        self.opened_device_name = device_name
        return self.device

    def get_error(self, device: object | None) -> int:
        del device
        error, self.error = self.error, bindings.ALC_NO_ERROR
        return error

    def is_extension_present(
        self, device: object | None, extension: str | bytes | None
    ) -> bool:
        del device
        if isinstance(extension, bytes):
            extension = extension.decode("ascii")
        return extension in self.extensions

    def get_strings(self, device: object | None, parameter: int) -> tuple[str, ...]:
        assert device is None
        self.string_list_queries.append(parameter)
        assert parameter in (
            bindings.ALC_ALL_DEVICES_SPECIFIER,
            bindings.ALC_DEVICE_SPECIFIER,
        )
        self.error = self.string_list_error
        return self.device_names

    def get_string(self, device: object | None, parameter: int) -> str | None:
        if device is None:
            assert parameter in (
                bindings.ALC_DEFAULT_ALL_DEVICES_SPECIFIER,
                bindings.ALC_DEFAULT_DEVICE_SPECIFIER,
            )
            return self.default_device_name
        assert device is self.device
        if parameter == bindings.ALC_DEVICE_SPECIFIER:
            if isinstance(self.opened_device_name, bytes):
                return self.opened_device_name.decode("utf-8")
            return self.opened_device_name or self.default_device_name
        if parameter == bindings.ALC_HRTF_SPECIFIER_SOFT:
            return self.hrtf_name
        raise AssertionError(f"unexpected ALC string parameter {parameter}")

    def get_integerv(
        self, device: object | None, parameter: int, size: int
    ) -> tuple[int, ...]:
        assert device is self.device
        assert size == 1
        if parameter == bindings.ALC_CONNECTED:
            return (int(self.connected),)
        if parameter == bindings.ALC_MAX_AUXILIARY_SENDS:
            return (self.max_auxiliary_sends,)
        assert parameter == bindings.ALC_HRTF_STATUS_SOFT
        self.error = self.hrtf_query_error
        return (self.hrtf_status,)

    def create_context(
        self, device: object, attributes: tuple[int, ...] | None
    ) -> object:
        assert device is self.device
        self.context_attributes = attributes
        return self.context

    def make_context_current(self, context: object | None) -> bool:
        self.current_context = context
        return True

    def destroy_context(self, context: object) -> None:
        self.destroyed_contexts.append(context)

    def close_device(self, device: object) -> bool:
        self.closed_devices.append(device)
        return True


class FakeLibrary:
    def __init__(self) -> None:
        self.al = FakeAL()
        self.alc = FakeALC()
        self._context_lock = RLock()


def as_library(library: FakeLibrary) -> bindings.OpenALLibrary:
    return cast(bindings.OpenALLibrary, library)


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


def test_managed_playback_applies_data_and_controls_lifecycle() -> None:
    library = FakeLibrary()
    pcm = PCM(b"\0\0" * 10, channels=1, sample_rate=10)
    config = VoiceConfig(position=(1.0, 2.0, 3.0), gain=0.5)
    listener = Listener(position=(4.0, 5.0, 6.0))

    with open_playback(library=as_library(library)) as playback:
        clip = upload(playback, pcm)
        voice = play(playback, clip, config)

        set_listener(playback, listener)
        acoustics = Acoustics(
            distance_model=DistanceModel.LINEAR_CLAMPED,
            doppler_factor=0.5,
            speed_of_sound=300.0,
        )
        set_acoustics(playback, acoustics)
        status = get_voice_status(playback, voice)
        assert status.state is VoiceState.PLAYING
        assert status.offset_seconds == 0.25
        assert library.al.sources[100][bindings.AL_POSITION] == (
            1.0,
            2.0,
            3.0,
        )
        assert library.al.sources[100][bindings.AL_GAIN] == 0.5
        assert clip.info == pcm.info
        assert clip.duration_seconds == 1.0
        assert clip.frame_count == 10
        assert get_listener(playback) == listener
        assert get_acoustics(playback) == acoustics
        assert library.al.listener[bindings.AL_ORIENTATION] == (
            0.0,
            0.0,
            -1.0,
            0.0,
            1.0,
            0.0,
        )

        set_voice_config(playback, voice, replace(config, position=(7.0, 8.0, 9.0)))
        assert library.al.sources[100][bindings.AL_POSITION] == (
            7.0,
            8.0,
            9.0,
        )

        pause(playback, voice)
        assert get_voice_status(playback, voice).state is VoiceState.PAUSED
        resume(playback, voice)
        assert get_voice_status(playback, voice).state is VoiceState.PLAYING
        with pytest.raises(InvalidVoiceStateError, match="playing"):
            resume(playback, voice)
        stop(playback, voice)
        assert get_voice_status(playback, voice).state is VoiceState.STOPPED
        with pytest.raises(InvalidVoiceStateError, match="stopped"):
            resume(playback, voice)

        with pytest.raises(ResourceInUseError):
            release(playback, clip)
        release(playback, voice)
        with pytest.raises(InvalidHandleError, match="released"):
            get_voice_status(playback, voice)
        release(playback, clip)

    assert library.al.sources == {}
    assert library.al.buffers == {}
    assert library.alc.destroyed_contexts == [library.alc.context]
    assert library.alc.closed_devices == [library.alc.device]
    assert library.alc.current_context is library.alc.previous_context


def test_voice_efx_are_created_replaced_and_released_with_the_voice() -> None:
    library = FakeLibrary()
    reverb = Reverb(gain=0.2, decay_time=0.6, high_frequency_decay_ratio=0.8)
    with open_playback(library=as_library(library)) as playback:
        clip = upload(playback, PCM(b"\0\0" * 10, channels=1, sample_rate=10))
        voice = play(
            playback,
            clip,
            filter=LowPassFilter(high_frequency_gain=0.1),
            effect_sends=(
                EffectSend(
                    effect=reverb,
                    filter=HighPassFilter(low_frequency_gain=0.25),
                ),
            ),
        )

        assert library.al.sources[100][bindings.AL_DIRECT_FILTER] == 300
        assert library.al.filters[300] == {
            bindings.AL_FILTER_TYPE: bindings.AL_FILTER_LOWPASS,
            bindings.AL_LOWPASS_GAIN: 1.0,
            bindings.AL_LOWPASS_GAINHF: 0.1,
        }
        assert library.al.effects[200][bindings.AL_EFFECT_TYPE] == (
            bindings.AL_EFFECT_REVERB
        )
        assert library.al.effects[200][bindings.AL_REVERB_GAIN] == 0.2
        assert library.al.effects[200][bindings.AL_REVERB_DECAY_TIME] == 0.6
        assert library.al.effect_slots[400] == {bindings.AL_EFFECTSLOT_EFFECT: 200}
        assert library.al.source_sends[(100, 0)] == (400, 301)
        assert library.al.filters[301][bindings.AL_FILTER_TYPE] == (
            bindings.AL_FILTER_HIGHPASS
        )

        set_voice_config(
            playback,
            voice,
            VoiceConfig(filter=HighPassFilter(low_frequency_gain=0.1)),
        )

        assert library.al.sources[100][bindings.AL_DIRECT_FILTER] == 302
        assert library.al.source_sends[(100, 0)] == (
            bindings.AL_EFFECTSLOT_NULL,
            bindings.AL_FILTER_NULL,
        )
        assert library.al.allocated_effects == set()
        assert library.al.allocated_effect_slots == set()
        assert library.al.allocated_filters == {302}

        set_voice_config(playback, voice, VoiceConfig())
        assert library.al.sources[100][bindings.AL_DIRECT_FILTER] == (
            bindings.AL_FILTER_NULL
        )
        assert library.al.allocated_filters == set()
        release(playback, voice)
        release(playback, clip)

    assert library.al.allocated_effects == set()
    assert library.al.allocated_effect_slots == set()
    assert library.al.allocated_filters == set()


def test_failed_voice_efx_update_restores_config_and_native_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library = FakeLibrary()
    previous = VoiceConfig(
        gain=0.75,
        effect_sends=(EffectSend(effect=Reverb(decay_time=0.5)),),
    )
    replacement = VoiceConfig(
        gain=0.25,
        effect_sends=(EffectSend(effect=Reverb(decay_time=1.0)),),
    )
    with open_playback(library=as_library(library)) as playback:
        clip = upload(playback, PCM(b"\0\0" * 10, channels=1, sample_rate=10))
        voice = play(playback, clip, previous)
        original_source3i = library.al.source3i
        fail_next_attachment = True

        def fail_one_attachment(
            identifier: int,
            parameter: int,
            value1: int,
            value2: int,
            value3: int,
        ) -> None:
            nonlocal fail_next_attachment
            original_source3i(identifier, parameter, value1, value2, value3)
            if fail_next_attachment:
                fail_next_attachment = False
                library.al.error = bindings.AL_INVALID_OPERATION

        monkeypatch.setattr(library.al, "source3i", fail_one_attachment)

        with pytest.raises(AudioBackendError, match="configure voice EFX routing"):
            set_voice_config(playback, voice, replacement)

        assert library.al.sources[100][bindings.AL_GAIN] == 0.75
        assert library.al.source_sends[(100, 0)] == (400, bindings.AL_FILTER_NULL)
        assert library.al.allocated_effects == {200}
        assert library.al.allocated_effect_slots == {400}
        assert library.al.allocated_filters == set()
        assert playback._voice_configs[voice._token] == previous


def test_voice_efx_require_device_support_and_available_send_slots() -> None:
    library = FakeLibrary()
    library.alc.extensions.remove("ALC_EXT_EFX")
    with open_playback(library=as_library(library)) as playback:
        clip = upload(playback, PCM(b"\0\0", channels=1, sample_rate=1))
        with pytest.raises(AudioBackendError, match="does not support EFX"):
            play(playback, clip, filter=LowPassFilter())
        assert library.al.sources == {}
        assert library.al.allocated_filters == set()

    library = FakeLibrary()
    library.alc.max_auxiliary_sends = 1
    sends = (
        EffectSend(effect=Reverb(decay_time=0.5)),
        EffectSend(effect=Reverb(decay_time=1.0)),
    )
    with open_playback(library=as_library(library)) as playback:
        clip = upload(playback, PCM(b"\0\0", channels=1, sample_rate=1))
        voice = play(playback, clip)
        with pytest.raises(AudioBackendError, match="at most 1"):
            set_voice_config(
                playback,
                voice,
                VoiceConfig(gain=0.25, effect_sends=sends),
            )
        assert library.al.sources[100][bindings.AL_GAIN] == 1.0
        assert playback._voice_configs[voice._token] == VoiceConfig()
        release(playback, voice)

        with pytest.raises(AudioBackendError, match="at most 1"):
            play(playback, clip, effect_sends=sends)
        assert library.al.sources == {}
        assert library.al.allocated_effects == set()


def test_static_voice_can_seek_rewind_and_restart() -> None:
    library = FakeLibrary()
    with open_playback(library=as_library(library)) as playback:
        clip = upload(playback, PCM(b"\0\0" * 10, channels=1, sample_rate=10))
        voice = play(playback, clip)

        pause(playback, voice)
        seek(playback, voice, 0.5)
        assert get_voice_status(playback, voice) == VoiceStatus(
            state=VoiceState.PAUSED,
            offset_seconds=0.5,
            offset_frames=5,
        )

        seek_frames(playback, voice, 7)
        assert get_voice_status(playback, voice) == VoiceStatus(
            state=VoiceState.PAUSED,
            offset_seconds=0.7,
            offset_frames=7,
        )

        rewind(playback, voice)
        assert get_voice_status(playback, voice) == VoiceStatus(
            state=VoiceState.INITIAL,
            offset_seconds=0.0,
            offset_frames=0,
        )

        restart(playback, voice)
        assert get_voice_status(playback, voice).state is VoiceState.PLAYING

        release(playback, voice)
        release(playback, clip)


def test_release_finished_collects_only_stopped_voices() -> None:
    library = FakeLibrary()
    with open_playback(library=as_library(library)) as playback:
        clip = upload(playback, PCM(b"\0\0", channels=1, sample_rate=1))
        finished = play(
            playback,
            clip,
            filter=LowPassFilter(high_frequency_gain=0.5),
        )
        paused = play(playback, clip)
        library.al.states[100] = bindings.AL_STOPPED
        pause(playback, paused)

        assert release_finished(playback) == 1
        assert release_finished(playback) == 0
        with pytest.raises(InvalidHandleError, match="released"):
            get_voice_status(playback, finished)
        assert library.al.allocated_filters == set()
        assert get_voice_status(playback, paused).state is VoiceState.PAUSED

        release(playback, paused)
        release(playback, clip)


def test_open_playback_wraps_library_loading_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = bindings.LibraryNotFoundError("missing")

    def fail_to_load() -> bindings.OpenALLibrary:
        raise failure

    monkeypatch.setattr(bindings, "load", fail_to_load)

    with pytest.raises(PlaybackOpenError) as caught:
        open_playback()
    assert caught.value.__cause__ is failure


def test_close_releases_live_resources_and_is_idempotent() -> None:
    library = FakeLibrary()
    playback = open_playback(library=as_library(library))
    clip = upload(playback, PCM(b"\0\0", channels=1, sample_rate=1))
    play(
        playback,
        clip,
        effect_sends=(EffectSend(effect=Reverb(decay_time=0.5)),),
    )

    close_playback(playback)
    close_playback(playback)

    assert library.al.sources == {}
    assert library.al.buffers == {}
    assert library.al.allocated_effects == set()
    assert library.al.allocated_effect_slots == set()
    assert library.alc.destroyed_contexts == [library.alc.context]
    with pytest.raises(PlaybackClosedError):
        upload(playback, PCM(b"\0\0", channels=1, sample_rate=1))


def test_playback_sessions_can_close_out_of_opening_order() -> None:
    library = FakeLibrary()
    original_context = library.alc.context
    previous_context = library.alc.previous_context
    first = open_playback(library=as_library(library))
    second_context = object()
    library.alc.context = second_context
    second = open_playback(library=as_library(library))

    close_playback(first)

    assert library.alc.current_context is second_context

    close_playback(second)

    assert library.alc.current_context is previous_context
    assert library.alc.destroyed_contexts == [original_context, second_context]


def test_handles_cannot_cross_playback_sessions() -> None:
    first_library = FakeLibrary()
    second_library = FakeLibrary()
    first = open_playback(library=as_library(first_library))
    second = open_playback(library=as_library(second_library))
    try:
        clip = upload(first, PCM(b"\0\0", channels=1, sample_rate=1))
        with pytest.raises(InvalidHandleError, match="does not belong"):
            play(second, clip)
    finally:
        close_playback(second)
        close_playback(first)


def test_playback_does_not_enforce_thread_ownership() -> None:
    library = FakeLibrary()
    playback = open_playback(library=as_library(library))
    pcm = PCM(b"\0\0", channels=1, sample_rate=1)

    with ThreadPoolExecutor(max_workers=1) as executor:
        clip = executor.submit(upload, playback, pcm).result()
        voice = executor.submit(play, playback, clip).result()
        executor.submit(stop, playback, voice).result()
        executor.submit(close_playback, playback).result()

    assert library.al.sources == {}
    assert library.al.buffers == {}


def test_playback_serializes_complete_operations_across_threads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library = FakeLibrary()
    playback = open_playback(library=as_library(library))
    pcm = PCM(b"\0\0", channels=1, sample_rate=1)
    first_upload_entered = Event()
    allow_first_upload = Event()
    second_call_started = Event()
    second_allocation_entered = Event()
    allocation_count = 0
    original_gen_buffers = library.al.gen_buffers
    original_buffer_data = library.al.buffer_data

    def observed_gen_buffers(count: int = 1) -> tuple[int, ...]:
        nonlocal allocation_count
        allocation_count += 1
        if allocation_count == 2:
            second_allocation_entered.set()
        return original_gen_buffers(count)

    def blocking_buffer_data(
        identifier: int,
        format_name: int,
        data: bytes,
        sample_rate: int,
    ) -> None:
        if identifier == 1:
            first_upload_entered.set()
            if not allow_first_upload.wait(2.0):
                raise AssertionError("timed out waiting to finish the first upload")
        original_buffer_data(identifier, format_name, data, sample_rate)

    monkeypatch.setattr(library.al, "gen_buffers", observed_gen_buffers)
    monkeypatch.setattr(library.al, "buffer_data", blocking_buffer_data)

    def upload_second_clip() -> Clip:
        second_call_started.set()
        return upload(playback, pcm)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(upload, playback, pcm)
            assert first_upload_entered.wait(1.0)
            second = executor.submit(upload_second_clip)

            assert second_call_started.wait(1.0)
            assert not second_allocation_entered.wait(0.1)
            allow_first_upload.set()
            first.result()
            second.result()

        assert second_allocation_entered.is_set()
        assert library.al.allocated_buffers == {1, 2}
    finally:
        allow_first_upload.set()
        close_playback(playback)


def test_playback_sessions_sharing_a_library_serialize_context_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library = FakeLibrary()
    first_context = library.alc.context
    first_playback = open_playback(library=as_library(library))
    second_context = object()
    library.alc.context = second_context
    second_playback = open_playback(library=as_library(library))
    pcm = PCM(b"\0\0", channels=1, sample_rate=1)
    first_upload_entered = Event()
    allow_first_upload = Event()
    second_call_started = Event()
    second_context_activated = Event()
    original_buffer_data = library.al.buffer_data
    original_make_context_current = library.alc.make_context_current

    def blocking_buffer_data(
        identifier: int,
        format_name: int,
        data: bytes,
        sample_rate: int,
    ) -> None:
        if identifier == 1:
            first_upload_entered.set()
            if not allow_first_upload.wait(2.0):
                raise AssertionError("timed out waiting to finish the first upload")
        original_buffer_data(identifier, format_name, data, sample_rate)

    def observe_context_activation(context: object | None) -> bool:
        if context is second_context:
            second_context_activated.set()
        return original_make_context_current(context)

    def upload_to_second_playback() -> Clip:
        second_call_started.set()
        return upload(second_playback, pcm)

    monkeypatch.setattr(library.al, "buffer_data", blocking_buffer_data)
    monkeypatch.setattr(
        library.alc,
        "make_context_current",
        observe_context_activation,
    )

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(upload, first_playback, pcm)
            assert first_upload_entered.wait(1.0)
            second = executor.submit(upload_to_second_playback)

            assert second_call_started.wait(1.0)
            assert library.alc.current_context is first_context
            assert not second_context_activated.wait(0.1)
            allow_first_upload.set()
            first.result()
            second.result()

        assert second_context_activated.is_set()
        assert library.alc.current_context is second_context
    finally:
        allow_first_upload.set()
        close_playback(second_playback)
        close_playback(first_playback)


def test_stream_uses_bounded_reusable_buffers_and_drains_finished_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library = FakeLibrary()
    with open_playback(library=as_library(library)) as playback:
        stream = open_stream(
            playback,
            channels=1,
            sample_rate=10,
            buffer_count=2,
            config=VoiceConfig(
                gain=0.5,
                filter=HighPassFilter(low_frequency_gain=0.5),
                effect_sends=(EffectSend(effect=Reverb(decay_time=0.5)),),
            ),
        )
        assert len(library.al.allocated_buffers) == 2
        assert library.al.sources[100][bindings.AL_GAIN] == 0.5

        first = bytearray(b"\0\0" * 10)
        assert try_write_stream(playback, stream, first)
        first[:] = b"\xff" * len(first)
        assert library.al.buffers[1][1] == b"\0\0" * 10
        assert try_write_stream(playback, stream, b"\0\0" * 5)

        def fail_copy(samples: object) -> bytes:
            del samples
            raise AssertionError("backpressure copied a rejected chunk")

        monkeypatch.setattr(playback_module, "_copy_stream_samples", fail_copy)
        assert not try_write_stream(playback, stream, b"\0\0")

        start_stream(playback, stream)
        status = update_stream(playback, stream)
        assert status.state is StreamState.PLAYING
        assert status.queued_chunks == 2
        assert status.queued_seconds == pytest.approx(1.5)

        finish_stream(playback, stream)
        library.al.processed[100] = 2
        library.al.states[100] = bindings.AL_STOPPED
        status = update_stream(playback, stream)
        assert status.state is StreamState.FINISHED
        assert status.input_finished
        assert status.queued_chunks == 0
        assert status.queued_seconds == 0.0
        assert status.underrun_count == 0

        assert release_finished(playback) == 1
        with pytest.raises(InvalidHandleError, match="released"):
            update_stream(playback, stream)

    assert library.al.allocated_buffers == set()
    assert library.al.allocated_effects == set()
    assert library.al.allocated_effect_slots == set()
    assert library.al.allocated_filters == set()


def test_stream_update_reclaims_offsets_and_counts_underrun_episodes() -> None:
    library = FakeLibrary()
    with open_playback(library=as_library(library)) as playback:
        stream = open_stream(
            playback,
            channels=1,
            sample_rate=10,
            buffer_count=1,
        )
        assert try_write_stream(playback, stream, b"\0\0" * 10)
        start_stream(playback, stream)
        library.al.offsets[100] = 0.25
        assert update_stream(playback, stream).queued_seconds == pytest.approx(0.75)

        library.al.processed[100] = 1
        library.al.states[100] = bindings.AL_STOPPED
        status = update_stream(playback, stream)
        assert status.state is StreamState.PLAYING
        assert status.queued_chunks == 0
        assert status.underrun_count == 1
        assert update_stream(playback, stream).underrun_count == 1

        assert try_write_stream(playback, stream, b"\0\0" * 2)
        assert library.al.play_calls == [100, 100]
        library.al.processed[100] = 1
        library.al.states[100] = bindings.AL_STOPPED
        assert update_stream(playback, stream).underrun_count == 2

        finish_stream(playback, stream)
        status = update_stream(playback, stream)
        assert status.state is StreamState.FINISHED
        assert status.underrun_count == 2
        release(playback, stream)


def test_stream_write_restarts_without_replaying_unreclaimed_chunks() -> None:
    library = FakeLibrary()
    with open_playback(library=as_library(library)) as playback:
        stream = open_stream(
            playback,
            channels=1,
            sample_rate=10,
            buffer_count=2,
        )
        assert try_write_stream(playback, stream, b"\0\0" * 10)
        start_stream(playback, stream)

        library.al.processed[100] = 1
        library.al.states[100] = bindings.AL_STOPPED
        assert try_write_stream(playback, stream, b"\0\0" * 2)

        assert library.al.play_calls == [100, 100]
        assert library.al.sample_offset_calls == [(100, 10)]
        release(playback, stream)


def test_stream_pause_resume_stop_and_looping_rules() -> None:
    library = FakeLibrary()
    with open_playback(library=as_library(library)) as playback:
        with pytest.raises(ValueError, match="cannot loop"):
            open_stream(
                playback,
                channels=2,
                sample_rate=44_100,
                config=VoiceConfig(looping=True),
            )

        stream = open_stream(
            playback,
            channels=2,
            sample_rate=44_100,
            sample_type=SampleType.UINT8,
            buffer_count=2,
        )
        pause(playback, stream)
        assert try_write_stream(playback, stream, b"\0\0")
        start_stream(playback, stream)
        pause(playback, stream)
        assert update_stream(playback, stream).state is StreamState.PAUSED
        finish_stream(playback, stream)
        assert update_stream(playback, stream).state is StreamState.PAUSED
        with pytest.raises(ValueError, match="cannot loop"):
            set_voice_config(playback, stream, VoiceConfig(looping=True))

        resume(playback, stream)
        assert update_stream(playback, stream).state is StreamState.PLAYING
        stop(playback, stream)
        status = update_stream(playback, stream)
        assert status.state is StreamState.STOPPED
        assert status.input_finished
        assert status.queued_chunks == 0
        stop(playback, stream)
        pause(playback, stream)
        with pytest.raises(InvalidVoiceStateError, match="stopped"):
            resume(playback, stream)
        with pytest.raises(InvalidVoiceStateError, match="stopped"):
            finish_stream(playback, stream)
        with pytest.raises(InvalidVoiceStateError, match="stopped"):
            try_write_stream(playback, stream, b"\0\0")
        release(playback, stream)


@pytest.mark.parametrize(
    ("arguments", "exception", "message"),
    [
        ({"channels": 3, "sample_rate": 1}, ValueError, "channels"),
        ({"channels": True, "sample_rate": 1}, TypeError, "integer"),
        ({"channels": 1.0, "sample_rate": 1}, TypeError, "integer"),
        ({"channels": 1, "sample_rate": 0}, ValueError, "positive"),
        ({"channels": 1, "sample_rate": True}, TypeError, "integer"),
        ({"channels": 1, "sample_rate": 1, "buffer_count": 0}, ValueError, "positive"),
        (
            {"channels": 1, "sample_rate": 1, "buffer_count": True},
            TypeError,
            "integer",
        ),
    ],
)
def test_open_stream_rejects_invalid_layouts(
    arguments: dict[str, object], exception: type[Exception], message: str
) -> None:
    library = FakeLibrary()
    with (
        open_playback(library=as_library(library)) as playback,
        pytest.raises(exception, match=message),
    ):
        open_stream(playback, **arguments)  # type: ignore[arg-type]


def test_stream_rejects_invalid_chunks_and_invalid_start_transitions() -> None:
    library = FakeLibrary()
    with open_playback(library=as_library(library)) as playback:
        stream = open_stream(
            playback,
            channels=2,
            sample_rate=1,
            buffer_count=1,
        )
        with pytest.raises(InvalidVoiceStateError, match="without a queued chunk"):
            start_stream(playback, stream)
        with pytest.raises(TypeError, match="bytes-like"):
            try_write_stream(playback, stream, "audio")  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="empty"):
            try_write_stream(playback, stream, b"")
        with pytest.raises(ValueError, match="whole number"):
            try_write_stream(playback, stream, b"\0\0")

        assert try_write_stream(playback, stream, b"\0" * 4)
        assert not try_write_stream(playback, stream, b"\0")
        start_stream(playback, stream)
        with pytest.raises(InvalidVoiceStateError, match="playing"):
            start_stream(playback, stream)
        finish_stream(playback, stream)
        finish_stream(playback, stream)
        with pytest.raises(InvalidVoiceStateError, match="end-of-input"):
            try_write_stream(playback, stream, b"\0" * 4)
        release(playback, stream)


def test_primed_finished_stream_can_start_and_close_releases_all_buffers() -> None:
    library = FakeLibrary()
    playback = open_playback(library=as_library(library))
    stream = open_stream(
        playback,
        channels=1,
        sample_rate=1,
        buffer_count=3,
    )
    assert try_write_stream(playback, stream, b"\0\0")
    finish_stream(playback, stream)
    start_stream(playback, stream)

    close_playback(playback)

    assert library.al.sources == {}
    assert library.al.allocated_buffers == set()
