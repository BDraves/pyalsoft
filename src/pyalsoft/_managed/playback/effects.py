"""Native EFX resource allocation and voice configuration."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from typing import cast

from pyalsoft import bindings
from pyalsoft._managed._backend import _check_alc_error, _clear_alc_errors
from pyalsoft._managed.effects import (
    EffectBusConfig,
    EffectSend,
    Filter,
    _EffectConfig,
    _iter_native_parameters,
    _ParameterKind,
    _ParameterSpec,
)
from pyalsoft._managed.errors import (
    AudioBackendError,
    InvalidHandleError,
    ResourceInUseError,
)
from pyalsoft._managed.playback.session import (
    Playback,
    _check_al_error,
    _clear_al_errors,
    _prepare_al,
    _serialized_playback,
)
from pyalsoft._managed.playback.source_controls import (
    _apply_advanced_source_config,
)
from pyalsoft._managed.resources import EffectBus
from pyalsoft._managed.spatial import VoiceConfig


@dataclass(frozen=True, slots=True)
class _EfxResources:
    direct_filter: int | None = None
    effects: tuple[int, ...] = ()
    slots: tuple[int, ...] = ()
    owned_slots: tuple[int, ...] = ()
    send_filters: tuple[int | None, ...] = ()

    @property
    def filters(self) -> tuple[int, ...]:
        identifiers = () if self.direct_filter is None else (self.direct_filter,)
        return identifiers + tuple(
            identifier for identifier in self.send_filters if identifier is not None
        )


_EMPTY_EFX_RESOURCES = _EfxResources()


@dataclass(frozen=True, slots=True)
class _EffectBusRecord:
    effect: int
    slot: int
    config: EffectBusConfig


@dataclass(frozen=True, slots=True)
class _EfxReplacement:
    current: _EfxResources
    created: _EfxResources
    retired: _EfxResources
    direct_filter_changed: bool
    effect_sends_changed: bool


def _require_efx_support(playback: Playback, send_count: int) -> None:
    """Ensure the active device supports the requested EFX routing."""

    library = playback._library
    _clear_alc_errors(library, playback._device)
    supported = library.alc.is_extension_present(playback._device, "ALC_EXT_EFX")
    _check_alc_error(library, playback._device, "query EFX support")
    if not supported:
        raise AudioBackendError("the playback device does not support EFX")
    if not send_count:
        return
    maximum = library.alc.get_integerv(
        playback._device,
        bindings.ALC_MAX_AUXILIARY_SENDS,
        1,
    )[0]
    _check_alc_error(library, playback._device, "query auxiliary send limit")
    if send_count > maximum:
        raise AudioBackendError(
            f"the playback device supports at most {maximum} auxiliary effect sends"
        )


def _configure_filter(playback: Playback, identifier: int, config: Filter) -> None:
    al = playback._library.al
    al.filteri(identifier, bindings.AL_FILTER_TYPE, config._native_type)
    for spec, value in _iter_native_parameters(config):
        if spec.kind is not _ParameterKind.FLOAT:
            raise AssertionError("managed filters only support float parameters")
        al.filterf(identifier, spec.parameter, cast(float, value))


def _native_integer(spec: _ParameterSpec, value: object) -> int:
    if spec.kind is _ParameterKind.ENUM:
        assert spec.enum_values is not None
        return spec.enum_values[value]
    assert isinstance(value, (bool, int))
    return int(value)


def _configure_effect(
    playback: Playback, identifier: int, config: _EffectConfig
) -> None:
    if (
        config._required_extension is not None
        and not playback._library.is_al_extension_present(config._required_extension)
    ):
        raise AudioBackendError(
            f"{type(config).__name__} requires the "
            f"{config._required_extension} extension"
        )
    al = playback._library.al
    al.effecti(identifier, bindings.AL_EFFECT_TYPE, config._native_type)
    for spec, value in _iter_native_parameters(config):
        if spec.kind is _ParameterKind.FLOAT:
            al.effectf(identifier, spec.parameter, cast(float, value))
        elif spec.kind is _ParameterKind.VECTOR3:
            al.effectfv(identifier, spec.parameter, cast(tuple[float, ...], value))
        else:
            al.effecti(
                identifier,
                spec.parameter,
                _native_integer(spec, value),
            )


def _delete_efx_resources(
    playback: Playback,
    resources: _EfxResources,
    *,
    operation: str,
) -> None:
    if resources == _EMPTY_EFX_RESOURCES:
        return
    al = playback._library.al
    if resources.owned_slots:
        al.delete_auxiliary_effect_slots(resources.owned_slots)
    if resources.effects:
        al.delete_effects(resources.effects)
    if resources.filters:
        al.delete_filters(resources.filters)
    _check_al_error(playback, operation)


def _effect_bus_record(playback: Playback, bus: EffectBus) -> _EffectBusRecord:
    if not isinstance(bus, EffectBus) or bus._owner is not playback._token:
        raise InvalidHandleError("effect bus does not belong to this playback session")
    record = playback._effect_buses.get(bus._token)
    if record is None or record.slot != bus._identifier:
        raise InvalidHandleError("effect bus has been released")
    return record


def _effect_bus_target_slot(
    playback: Playback,
    config: EffectBusConfig,
    *,
    source: EffectBus | None = None,
) -> int:
    target = config.target
    if target is None:
        return bindings.AL_EFFECTSLOT_NULL
    target_record = _effect_bus_record(playback, target)
    if source is not None and target._token is source._token:
        raise ValueError("an effect bus cannot target itself")

    visited = {source._token} if source is not None else set()
    current: EffectBus | None = target
    while current is not None:
        if current._token in visited:
            raise ValueError("effect bus targets cannot contain a cycle")
        visited.add(current._token)
        current = _effect_bus_record(playback, current).config.target
    return target_record.slot


def _configure_effect_bus_slot(
    playback: Playback,
    slot: int,
    effect: int,
    config: EffectBusConfig,
    *,
    source: EffectBus | None = None,
) -> None:
    target_slot = _effect_bus_target_slot(playback, config, source=source)
    has_effect_target = playback._library.is_al_extension_present(
        "AL_SOFT_effect_target"
    )
    if (
        target_slot != bindings.AL_EFFECTSLOT_NULL
        and not has_effect_target
    ):
        raise AudioBackendError(
            "effect bus chaining requires the AL_SOFT_effect_target extension"
        )
    al = playback._library.al
    al.auxiliary_effect_sloti(slot, bindings.AL_EFFECTSLOT_EFFECT, effect)
    al.auxiliary_effect_slotf(slot, bindings.AL_EFFECTSLOT_GAIN, config.gain)
    al.auxiliary_effect_sloti(
        slot,
        bindings.AL_EFFECTSLOT_AUXILIARY_SEND_AUTO,
        int(config.auxiliary_send_auto),
    )
    if target_slot != bindings.AL_EFFECTSLOT_NULL or (
        source is not None and has_effect_target
    ):
        al.auxiliary_effect_sloti(
            slot,
            bindings.AL_EFFECTSLOT_TARGET_SOFT,
            target_slot,
        )


@_serialized_playback
def create_effect_bus(playback: Playback, config: EffectBusConfig) -> EffectBus:
    """Create a reusable auxiliary effect bus owned by ``playback``."""

    if not isinstance(config, EffectBusConfig):
        raise TypeError("config must be an EffectBusConfig")
    _prepare_al(playback)
    _require_efx_support(playback, 0)
    _effect_bus_target_slot(playback, config)
    al = playback._library.al
    effects: tuple[int, ...] = ()
    slots: tuple[int, ...] = ()
    try:
        effects = al.gen_effects()
        if len(effects) != 1:
            raise AudioBackendError("OpenAL did not create exactly one effect")
        _check_al_error(playback, "create effect bus effect")
        _configure_effect(playback, effects[0], config.effect)
        _check_al_error(playback, f"configure {type(config.effect).__name__}")
        slots = al.gen_auxiliary_effect_slots()
        if len(slots) != 1:
            raise AudioBackendError(
                "OpenAL did not create exactly one auxiliary effect slot"
            )
        _check_al_error(playback, "create effect bus slot")
        _configure_effect_bus_slot(playback, slots[0], effects[0], config)
        _check_al_error(playback, "configure effect bus")
    except BaseException:
        _clear_al_errors(playback)
        if slots:
            al.delete_auxiliary_effect_slots(slots)
        if effects:
            al.delete_effects(effects)
        al.get_error()
        raise

    token = object()
    playback._effect_buses[token] = _EffectBusRecord(effects[0], slots[0], config)
    return EffectBus(playback._token, token, slots[0])


@_serialized_playback
def get_effect_bus_config(
    playback: Playback, bus: EffectBus
) -> EffectBusConfig:
    """Return the current immutable configuration of a live effect bus."""

    return _effect_bus_record(playback, bus).config


@_serialized_playback
def set_effect_bus_config(
    playback: Playback,
    bus: EffectBus,
    config: EffectBusConfig,
) -> None:
    """Atomically replace the effect and routing configuration of a bus."""

    if not isinstance(config, EffectBusConfig):
        raise TypeError("config must be an EffectBusConfig")
    record = _effect_bus_record(playback, bus)
    _effect_bus_target_slot(playback, config, source=bus)
    if config == record.config:
        return
    _prepare_al(playback)
    al = playback._library.al
    effects = al.gen_effects()
    if len(effects) != 1:
        raise AudioBackendError("OpenAL did not create exactly one effect")
    replacement = effects[0]
    try:
        _check_al_error(playback, "create replacement effect bus effect")
        _configure_effect(playback, replacement, config.effect)
        _check_al_error(playback, f"configure {type(config.effect).__name__}")
        _configure_effect_bus_slot(
            playback,
            record.slot,
            replacement,
            config,
            source=bus,
        )
        _check_al_error(playback, "replace effect bus configuration")
    except BaseException:
        _clear_al_errors(playback)
        with suppress(Exception):
            _configure_effect_bus_slot(
                playback,
                record.slot,
                record.effect,
                record.config,
                source=bus,
            )
        al.delete_effects((replacement,))
        al.get_error()
        raise
    al.delete_effects((record.effect,))
    _check_al_error(playback, "release replaced effect bus effect")
    playback._effect_buses[bus._token] = _EffectBusRecord(
        replacement,
        record.slot,
        config,
    )


def _config_uses_effect_bus(config: VoiceConfig, bus: EffectBus) -> bool:
    return any(send.bus == bus for send in config.effect_sends)


def _release_effect_bus(playback: Playback, bus: EffectBus) -> None:
    record = _effect_bus_record(playback, bus)
    voice_configs = tuple(playback._voice_configs.values()) + tuple(
        stream.config for stream in playback._streams.values()
    )
    if any(_config_uses_effect_bus(config, bus) for config in voice_configs):
        raise ResourceInUseError("effect bus is attached to a live voice or stream")
    if any(
        other.config.target == bus
        for token, other in playback._effect_buses.items()
        if token is not bus._token
    ):
        raise ResourceInUseError("effect bus is targeted by another effect bus")
    _prepare_al(playback)
    al = playback._library.al
    al.delete_auxiliary_effect_slots((record.slot,))
    al.delete_effects((record.effect,))
    _check_al_error(playback, "release effect bus")
    del playback._effect_buses[bus._token]


def _delete_all_effect_buses(playback: Playback) -> None:
    if not playback._effect_buses:
        return
    al = playback._library.al
    records = tuple(playback._effect_buses.values())
    if playback._library.is_al_extension_present("AL_SOFT_effect_target"):
        for record in records:
            al.auxiliary_effect_sloti(
                record.slot,
                bindings.AL_EFFECTSLOT_TARGET_SOFT,
                bindings.AL_EFFECTSLOT_NULL,
            )
    al.delete_auxiliary_effect_slots(tuple(record.slot for record in records))
    al.delete_effects(tuple(record.effect for record in records))
    _check_al_error(playback, "release effect buses")
    playback._effect_buses.clear()


def _create_efx_resources(
    playback: Playback,
    direct_filter_config: Filter | None,
    effect_sends: tuple[EffectSend, ...],
) -> _EfxResources:
    if direct_filter_config is None and not effect_sends:
        return _EMPTY_EFX_RESOURCES
    _require_efx_support(playback, len(effect_sends))
    al = playback._library.al
    direct_filter: int | None = None
    effects: list[int] = []
    slots: list[int] = []
    owned_slots: list[int] = []
    send_filters: list[int | None] = []
    try:
        if direct_filter_config is not None:
            identifiers = al.gen_filters()
            if len(identifiers) != 1:
                raise AudioBackendError("OpenAL did not create exactly one filter")
            direct_filter = identifiers[0]
            _check_al_error(playback, "create direct filter")
            _configure_filter(playback, direct_filter, direct_filter_config)
            _check_al_error(playback, "configure direct filter")

        for send in effect_sends:
            if send.bus is not None:
                slot = _effect_bus_record(playback, send.bus).slot
                slots.append(slot)
            else:
                assert send.effect is not None
                effect_ids = al.gen_effects()
                if len(effect_ids) != 1:
                    raise AudioBackendError("OpenAL did not create exactly one effect")
                effect = effect_ids[0]
                effects.append(effect)
                _check_al_error(playback, "create effect")
                _configure_effect(playback, effect, send.effect)
                _check_al_error(playback, f"configure {type(send.effect).__name__}")

                slot_ids = al.gen_auxiliary_effect_slots()
                if len(slot_ids) != 1:
                    raise AudioBackendError(
                        "OpenAL did not create exactly one auxiliary effect slot"
                    )
                slot = slot_ids[0]
                slots.append(slot)
                owned_slots.append(slot)
                _check_al_error(playback, "create auxiliary effect slot")
                al.auxiliary_effect_sloti(slot, bindings.AL_EFFECTSLOT_EFFECT, effect)
                _check_al_error(playback, "attach effect to auxiliary slot")

            if send.filter is None:
                send_filters.append(None)
            else:
                filter_ids = al.gen_filters()
                if len(filter_ids) != 1:
                    raise AudioBackendError(
                        "OpenAL did not create exactly one send filter"
                    )
                send_filter = filter_ids[0]
                send_filters.append(send_filter)
                _check_al_error(playback, "create send filter")
                _configure_filter(playback, send_filter, send.filter)
                _check_al_error(playback, "configure send filter")
    except BaseException:
        _clear_al_errors(playback)
        resources = _EfxResources(
            direct_filter=direct_filter,
            effects=tuple(effects),
            slots=tuple(slots),
            owned_slots=tuple(owned_slots),
            send_filters=tuple(send_filters),
        )
        with suppress(Exception):
            _delete_efx_resources(
                playback,
                resources,
                operation="clean up incomplete EFX resources",
            )
        playback._library.al.get_error()
        raise
    return _EfxResources(
        direct_filter=direct_filter,
        effects=tuple(effects),
        slots=tuple(slots),
        owned_slots=tuple(owned_slots),
        send_filters=tuple(send_filters),
    )


def _attach_efx_resources(
    playback: Playback,
    source: int,
    resources: _EfxResources,
    *,
    clear_send_count: int,
    attach_direct_filter: bool = True,
    attach_effect_sends: bool = True,
) -> None:
    al = playback._library.al
    if attach_direct_filter:
        al.sourcei(
            source,
            bindings.AL_DIRECT_FILTER,
            resources.direct_filter or bindings.AL_FILTER_NULL,
        )
    if attach_effect_sends:
        for index, slot in enumerate(resources.slots):
            send_filter = resources.send_filters[index]
            al.source3i(
                source,
                bindings.AL_AUXILIARY_SEND_FILTER,
                slot,
                index,
                send_filter or bindings.AL_FILTER_NULL,
            )
        for index in range(len(resources.slots), clear_send_count):
            al.source3i(
                source,
                bindings.AL_AUXILIARY_SEND_FILTER,
                bindings.AL_EFFECTSLOT_NULL,
                index,
                bindings.AL_FILTER_NULL,
            )
    _check_al_error(playback, "configure voice EFX routing")


def _install_efx_resources(
    playback: Playback,
    source: int,
    config: VoiceConfig,
) -> _EfxResources:
    if config.filter is None and not config.effect_sends:
        return _EMPTY_EFX_RESOURCES
    current = _create_efx_resources(
        playback,
        config.filter,
        config.effect_sends,
    )
    try:
        _attach_efx_resources(
            playback,
            source,
            current,
            clear_send_count=0,
        )
    except BaseException:
        with suppress(Exception):
            _clear_al_errors(playback)
        with suppress(Exception):
            _attach_efx_resources(
                playback,
                source,
                _EMPTY_EFX_RESOURCES,
                clear_send_count=len(current.slots),
            )
        with suppress(Exception):
            _clear_al_errors(playback)
        with suppress(Exception):
            _delete_efx_resources(
                playback,
                current,
                operation="clean up replacement EFX resources",
            )
        playback._library.al.get_error()
        raise
    return current


def _prepare_efx_replacement(
    playback: Playback,
    previous_config: VoiceConfig,
    previous: _EfxResources,
    current_config: VoiceConfig,
) -> _EfxReplacement:
    direct_filter_changed = current_config.filter != previous_config.filter
    effect_sends_changed = current_config.effect_sends != previous_config.effect_sends
    if not direct_filter_changed and not effect_sends_changed:
        return _EfxReplacement(
            current=previous,
            created=_EMPTY_EFX_RESOURCES,
            retired=_EMPTY_EFX_RESOURCES,
            direct_filter_changed=False,
            effect_sends_changed=False,
        )

    created = _create_efx_resources(
        playback,
        current_config.filter if direct_filter_changed else None,
        current_config.effect_sends if effect_sends_changed else (),
    )
    current = _EfxResources(
        direct_filter=(
            created.direct_filter if direct_filter_changed else previous.direct_filter
        ),
        effects=created.effects if effect_sends_changed else previous.effects,
        slots=created.slots if effect_sends_changed else previous.slots,
        owned_slots=(
            created.owned_slots if effect_sends_changed else previous.owned_slots
        ),
        send_filters=(
            created.send_filters if effect_sends_changed else previous.send_filters
        ),
    )
    retired = _EfxResources(
        direct_filter=previous.direct_filter if direct_filter_changed else None,
        effects=previous.effects if effect_sends_changed else (),
        slots=previous.slots if effect_sends_changed else (),
        owned_slots=previous.owned_slots if effect_sends_changed else (),
        send_filters=previous.send_filters if effect_sends_changed else (),
    )
    return _EfxReplacement(
        current=current,
        created=created,
        retired=retired,
        direct_filter_changed=direct_filter_changed,
        effect_sends_changed=effect_sends_changed,
    )


def _apply_voice_config(
    playback: Playback,
    identifier: int,
    config: VoiceConfig,
    *,
    previous: VoiceConfig | None = None,
    changed_only: bool = False,
) -> None:
    """Apply every voice property, or only values changed from ``previous``."""

    al = playback._library.al
    advanced_previous = previous
    apply_all = not changed_only
    if previous is None:
        previous = config
    vector_properties = (
        (bindings.AL_POSITION, previous.position, config.position),
        (bindings.AL_VELOCITY, previous.velocity, config.velocity),
        (bindings.AL_DIRECTION, previous.direction, config.direction),
    )
    for parameter, old_vector, new_vector in vector_properties:
        if apply_all or new_vector != old_vector:
            al.source3f(identifier, parameter, *new_vector)

    float_properties = (
        (bindings.AL_GAIN, previous.gain, config.gain),
        (bindings.AL_PITCH, previous.pitch, config.pitch),
        (bindings.AL_MIN_GAIN, previous.min_gain, config.min_gain),
        (bindings.AL_MAX_GAIN, previous.max_gain, config.max_gain),
        (
            bindings.AL_REFERENCE_DISTANCE,
            previous.reference_distance,
            config.reference_distance,
        ),
        (bindings.AL_MAX_DISTANCE, previous.max_distance, config.max_distance),
        (
            bindings.AL_ROLLOFF_FACTOR,
            previous.rolloff_factor,
            config.rolloff_factor,
        ),
        (
            bindings.AL_CONE_INNER_ANGLE,
            previous.cone_inner_angle,
            config.cone_inner_angle,
        ),
        (
            bindings.AL_CONE_OUTER_ANGLE,
            previous.cone_outer_angle,
            config.cone_outer_angle,
        ),
        (
            bindings.AL_CONE_OUTER_GAIN,
            previous.cone_outer_gain,
            config.cone_outer_gain,
        ),
    )
    for parameter, old_float, new_float in float_properties:
        if apply_all or new_float != old_float:
            al.sourcef(identifier, parameter, new_float)

    integer_properties = (
        (bindings.AL_LOOPING, previous.looping, config.looping),
        (bindings.AL_SOURCE_RELATIVE, previous.relative, config.relative),
    )
    for parameter, old_boolean, new_boolean in integer_properties:
        if apply_all or new_boolean != old_boolean:
            al.sourcei(identifier, parameter, int(new_boolean))

    _apply_advanced_source_config(
        playback,
        identifier,
        config,
        previous=advanced_previous,
    )
