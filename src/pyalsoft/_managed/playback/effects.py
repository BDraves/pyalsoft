"""Native EFX resource allocation and voice configuration."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass

from pyalsoft import bindings
from pyalsoft._managed._backend import _check_alc_error, _clear_alc_errors
from pyalsoft._managed.errors import AudioBackendError
from pyalsoft._managed.playback.session import (
    Playback,
    _check_al_error,
    _clear_al_errors,
)
from pyalsoft._managed.spatial import (
    EffectSend,
    Filter,
    LowPassFilter,
    Reverb,
    VoiceConfig,
)


@dataclass(frozen=True, slots=True)
class _EfxResources:
    direct_filter: int | None = None
    effects: tuple[int, ...] = ()
    slots: tuple[int, ...] = ()
    send_filters: tuple[int | None, ...] = ()

    @property
    def filters(self) -> tuple[int, ...]:
        identifiers = () if self.direct_filter is None else (self.direct_filter,)
        return identifiers + tuple(
            identifier for identifier in self.send_filters if identifier is not None
        )


_EMPTY_EFX_RESOURCES = _EfxResources()


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
    if isinstance(config, LowPassFilter):
        al.filteri(identifier, bindings.AL_FILTER_TYPE, bindings.AL_FILTER_LOWPASS)
        al.filterf(identifier, bindings.AL_LOWPASS_GAIN, config.gain)
        al.filterf(
            identifier,
            bindings.AL_LOWPASS_GAINHF,
            config.high_frequency_gain,
        )
    else:
        al.filteri(identifier, bindings.AL_FILTER_TYPE, bindings.AL_FILTER_HIGHPASS)
        al.filterf(identifier, bindings.AL_HIGHPASS_GAIN, config.gain)
        al.filterf(
            identifier,
            bindings.AL_HIGHPASS_GAINLF,
            config.low_frequency_gain,
        )


def _configure_reverb(playback: Playback, identifier: int, config: Reverb) -> None:
    al = playback._library.al
    al.effecti(identifier, bindings.AL_EFFECT_TYPE, bindings.AL_EFFECT_REVERB)
    float_properties = (
        (bindings.AL_REVERB_DENSITY, config.density),
        (bindings.AL_REVERB_DIFFUSION, config.diffusion),
        (bindings.AL_REVERB_GAIN, config.gain),
        (bindings.AL_REVERB_GAINHF, config.high_frequency_gain),
        (bindings.AL_REVERB_DECAY_TIME, config.decay_time),
        (
            bindings.AL_REVERB_DECAY_HFRATIO,
            config.high_frequency_decay_ratio,
        ),
        (bindings.AL_REVERB_REFLECTIONS_GAIN, config.reflections_gain),
        (bindings.AL_REVERB_REFLECTIONS_DELAY, config.reflections_delay),
        (bindings.AL_REVERB_LATE_REVERB_GAIN, config.late_reverb_gain),
        (bindings.AL_REVERB_LATE_REVERB_DELAY, config.late_reverb_delay),
        (
            bindings.AL_REVERB_AIR_ABSORPTION_GAINHF,
            config.air_absorption_high_frequency_gain,
        ),
        (bindings.AL_REVERB_ROOM_ROLLOFF_FACTOR, config.room_rolloff_factor),
    )
    for parameter, value in float_properties:
        al.effectf(identifier, parameter, value)
    al.effecti(
        identifier,
        bindings.AL_REVERB_DECAY_HFLIMIT,
        int(config.high_frequency_decay_limit),
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
    if resources.slots:
        al.delete_auxiliary_effect_slots(resources.slots)
    if resources.effects:
        al.delete_effects(resources.effects)
    if resources.filters:
        al.delete_filters(resources.filters)
    _check_al_error(playback, operation)


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
            effect_ids = al.gen_effects()
            if len(effect_ids) != 1:
                raise AudioBackendError("OpenAL did not create exactly one effect")
            effect = effect_ids[0]
            effects.append(effect)
            _check_al_error(playback, "create effect")
            _configure_reverb(playback, effect, send.effect)
            _check_al_error(playback, "configure reverb")

            slot_ids = al.gen_auxiliary_effect_slots()
            if len(slot_ids) != 1:
                raise AudioBackendError(
                    "OpenAL did not create exactly one auxiliary effect slot"
                )
            slot = slot_ids[0]
            slots.append(slot)
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
        send_filters=(
            created.send_filters if effect_sends_changed else previous.send_filters
        ),
    )
    retired = _EfxResources(
        direct_filter=previous.direct_filter if direct_filter_changed else None,
        effects=previous.effects if effect_sends_changed else (),
        slots=previous.slots if effect_sends_changed else (),
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
) -> None:
    """Apply every voice property, or only values changed from ``previous``."""

    al = playback._library.al
    apply_all = previous is None
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
