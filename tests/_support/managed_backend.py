"""Reusable fake OpenAL backend for managed API tests."""

from __future__ import annotations

from threading import RLock
from typing import cast

from pyalsoft import bindings


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
        self.scheduled_play_calls: list[tuple[int, int]] = []
        self.defer_update_calls = 0
        self.process_update_calls = 0
        self.updates_deferred = False
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
        self.enabled: set[int] = set()
        self.resampler_names = ("Nearest", "Cubic Spline", "23rd order Sinc")
        self.default_resampler = 1

    def get_error(self) -> int:
        error, self.error = self.error, bindings.AL_NO_ERROR
        return error

    def get_string(self, parameter: int) -> str | None:
        return self.strings.get(parameter)

    def get_stringi_soft(self, parameter: int, index: int) -> str | None:
        assert parameter == bindings.AL_RESAMPLER_NAME_SOFT
        return self.resampler_names[index]

    def enable(self, capability: int) -> None:
        self.enabled.add(capability)

    def disable(self, capability: int) -> None:
        self.enabled.discard(capability)

    def is_enabled(self, capability: int) -> bool:
        return capability in self.enabled

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

    def effectfv(
        self, identifier: int, parameter: int, values: tuple[float, ...]
    ) -> None:
        self.effects[identifier][parameter] = tuple(values)

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

    def sourcefv(
        self, identifier: int, parameter: int, values: tuple[float, ...]
    ) -> None:
        self.sources[identifier][parameter] = tuple(values)
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

    def source_play_at_time_soft(self, identifier: int, start_time: int) -> None:
        self.scheduled_play_calls.append((identifier, start_time))
        self.source_play(identifier)

    def defer_updates_soft(self) -> None:
        self.defer_update_calls += 1
        self.updates_deferred = True

    def process_updates_soft(self) -> None:
        self.process_update_calls += 1
        self.updates_deferred = False

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
        if parameter == bindings.AL_SUPER_STEREO_WIDTH_SOFT:
            return cast(float, self.sources[identifier].get(parameter, 0.46))
        assert parameter == bindings.AL_SEC_OFFSET
        if identifier in self.queues:
            return self.offsets.get(identifier, 0.0)
        return self.offsets.get(identifier, 0.25)

    def get_sourcei64v_soft(
        self, identifier: int, parameter: int, result_size: int = 1
    ) -> tuple[int, ...]:
        assert identifier in self.sources
        assert result_size == 2
        offset = self._source_sample_rate(identifier) * 5 * (1 << 32) // 4
        if parameter == bindings.AL_SAMPLE_OFFSET_LATENCY_SOFT:
            return (offset, 25_000_000)
        assert parameter == bindings.AL_SAMPLE_OFFSET_CLOCK_SOFT
        return (offset, 42_500_000_000)

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
        if parameter == bindings.AL_DISTANCE_MODEL:
            return self.distance_model_value
        if parameter == bindings.AL_NUM_RESAMPLERS_SOFT:
            return len(self.resampler_names)
        assert parameter == bindings.AL_DEFAULT_RESAMPLER_SOFT
        return self.default_resampler

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
        self.reset_attributes: list[tuple[int, ...] | None] = []
        self.reset_result = True
        self.reset_error = bindings.ALC_INVALID_VALUE
        self.extensions = {
            "ALC_ENUMERATE_ALL_EXT",
            "ALC_EXT_EFX",
            "ALC_SOFT_HRTF",
            "ALC_SOFT_output_limiter",
            "ALC_SOFT_output_mode",
            "ALC_SOFT_device_clock",
        }
        self.hrtf_profiles = ("Built-in HRTF", "Studio HRTF", "Gaming HRTF")
        self._restore_device_defaults()
        self.string_list_queries: list[int] = []
        self.error = bindings.ALC_NO_ERROR
        self.string_list_error = bindings.ALC_NO_ERROR
        self.hrtf_query_error = bindings.ALC_NO_ERROR

    def _restore_device_defaults(self) -> None:
        self.sample_rate = 48_000
        self.refresh_rate = 94
        self.synchronous = False
        self.mono_sources = 255
        self.stereo_sources = 1
        self.max_auxiliary_sends = 2
        self.hrtf_status = bindings.ALC_HRTF_ENABLED_SOFT
        self.hrtf_name: str | None = "Built-in HRTF"
        self.output_limiter = True
        self.output_mode = bindings.ALC_STEREO_SOFT
        self.connected = True

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
        if parameter == bindings.ALC_FREQUENCY:
            return (self.sample_rate,)
        if parameter == bindings.ALC_REFRESH:
            return (self.refresh_rate,)
        if parameter == bindings.ALC_SYNC:
            return (int(self.synchronous),)
        if parameter == bindings.ALC_MONO_SOURCES:
            return (self.mono_sources,)
        if parameter == bindings.ALC_STEREO_SOURCES:
            return (self.stereo_sources,)
        if parameter == bindings.ALC_CONNECTED:
            return (int(self.connected),)
        if parameter == bindings.ALC_MAX_AUXILIARY_SENDS:
            return (self.max_auxiliary_sends,)
        if parameter == bindings.ALC_OUTPUT_LIMITER_SOFT:
            return (int(self.output_limiter),)
        if parameter == bindings.ALC_OUTPUT_MODE_SOFT:
            return (self.output_mode,)
        if parameter == bindings.ALC_NUM_HRTF_SPECIFIERS_SOFT:
            return (len(self.hrtf_profiles),)
        assert parameter == bindings.ALC_HRTF_STATUS_SOFT
        self.error = self.hrtf_query_error
        return (self.hrtf_status,)

    def get_stringi_soft(
        self, device: object, parameter: int, index: int
    ) -> str | None:
        assert device is self.device
        assert parameter == bindings.ALC_HRTF_SPECIFIER_SOFT
        return self.hrtf_profiles[index]

    def get_integer64v_soft(
        self, device: object, parameter: int, size: int
    ) -> tuple[int, ...]:
        assert device is self.device
        assert parameter == bindings.ALC_DEVICE_CLOCK_LATENCY_SOFT
        assert size == 2
        return (42_500_000_000, 25_000_000)

    def create_context(
        self, device: object, attributes: tuple[int, ...] | None
    ) -> object:
        assert device is self.device
        self.context_attributes = attributes
        return self.context

    def make_context_current(self, context: object | None) -> bool:
        self.current_context = context
        return True

    def reset_device_soft(
        self, device: object, attributes: tuple[int, ...] | None
    ) -> bool:
        assert device is self.device
        self.reset_attributes.append(attributes)
        if not self.reset_result:
            self.error = self.reset_error
            return False
        self._restore_device_defaults()
        if attributes is None:
            return True
        values = dict(zip(attributes[::2], attributes[1::2], strict=True))
        if bindings.ALC_FREQUENCY in values:
            self.sample_rate = values[bindings.ALC_FREQUENCY]
        if bindings.ALC_REFRESH in values:
            self.refresh_rate = values[bindings.ALC_REFRESH]
        if bindings.ALC_SYNC in values:
            self.synchronous = bool(values[bindings.ALC_SYNC])
        if bindings.ALC_MONO_SOURCES in values:
            self.mono_sources = values[bindings.ALC_MONO_SOURCES]
        if bindings.ALC_STEREO_SOURCES in values:
            self.stereo_sources = values[bindings.ALC_STEREO_SOURCES]
        if bindings.ALC_MAX_AUXILIARY_SENDS in values:
            self.max_auxiliary_sends = values[bindings.ALC_MAX_AUXILIARY_SENDS]
        if bindings.ALC_HRTF_SOFT in values:
            self.hrtf_status = (
                bindings.ALC_HRTF_ENABLED_SOFT
                if values[bindings.ALC_HRTF_SOFT]
                else bindings.ALC_HRTF_DISABLED_SOFT
            )
        if bindings.ALC_HRTF_ID_SOFT in values:
            self.hrtf_name = self.hrtf_profiles[values[bindings.ALC_HRTF_ID_SOFT]]
        if bindings.ALC_OUTPUT_LIMITER_SOFT in values:
            self.output_limiter = bool(values[bindings.ALC_OUTPUT_LIMITER_SOFT])
        if bindings.ALC_OUTPUT_MODE_SOFT in values:
            self.output_mode = values[bindings.ALC_OUTPUT_MODE_SOFT]
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
        self.invalidated_devices: list[object] = []
        self.al_extensions = {
            "AL_EXT_source_distance_model",
            "AL_EXT_SOURCE_RADIUS",
            "AL_EXT_STEREO_ANGLES",
            "AL_SOFT_direct_channels",
            "AL_SOFT_direct_channels_remix",
            "AL_SOFT_source_latency",
            "AL_SOFT_source_start_delay",
            "AL_SOFT_deferred_updates",
            "AL_SOFT_source_resampler",
            "AL_SOFT_source_spatialize",
            "AL_SOFT_UHJ",
        }

    def _invalidate_device_extensions(self, device: object) -> None:
        self.invalidated_devices.append(device)

    def is_al_extension_present(self, extension: str) -> bool:
        return extension in self.al_extensions


def as_library(library: FakeLibrary) -> bindings.OpenALLibrary:
    return cast(bindings.OpenALLibrary, library)
