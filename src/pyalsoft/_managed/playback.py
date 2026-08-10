"""Explicit managed playback sessions built on the low-level bindings."""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Buffer, Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from functools import wraps
from threading import RLock
from types import TracebackType
from typing import Concatenate, Self, overload

from pyalsoft import bindings
from pyalsoft._managed.models import (
    _OMITTED_FILTER,
    PCM,
    Acoustics,
    AudioBackendError,
    Clip,
    DistanceModel,
    EffectSend,
    Filter,
    HRTFStatus,
    InvalidHandleError,
    InvalidVoiceStateError,
    Listener,
    LowPassFilter,
    PlaybackClosedError,
    PlaybackConfig,
    PlaybackDevice,
    PlaybackInfo,
    PlaybackOpenError,
    ResourceInUseError,
    Reverb,
    SampleType,
    SoundInfo,
    Stream,
    StreamState,
    StreamStatus,
    Vector3,
    Voice,
    VoiceConfig,
    VoiceState,
    VoiceStatus,
    _frame_offset,
    _sound_offset,
    _UnsetType,
    _validate_offsets,
)
from pyalsoft.bindings._library import _pointer_address


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


@dataclass(frozen=True, slots=True)
class _StreamChunk:
    buffer: int
    frame_count: int
    duration: float


@dataclass(slots=True)
class _StreamRecord:
    identifier: int
    buffers: tuple[int, ...]
    free_buffers: deque[int]
    queued_chunks: deque[_StreamChunk]
    channels: int
    sample_rate: int
    sample_type: SampleType
    config: VoiceConfig
    efx: _EfxResources = _EMPTY_EFX_RESOURCES
    state: StreamState = StreamState.INITIAL
    input_finished: bool = False
    underrun_count: int = 0
    underrun_active: bool = False


class Playback:
    """Opaque owner for a playback device, context, clips, voices, and streams.

    Instances are returned by [`open_playback`][pyalsoft.open_playback]. Use them
    as context managers or pass them to
    [`close_playback`][pyalsoft.close_playback] for deterministic cleanup.
    Operations are serialized per session and across sessions sharing a native
    library, so a session may safely be used from multiple Python threads.
    """

    __slots__ = (
        "_clips",
        "_clip_infos",
        "_closed",
        "_context",
        "_device",
        "_library",
        "_lock",
        "_previous_context",
        "_previous_playback",
        "_streams",
        "_token",
        "_voice_clips",
        "_voice_configs",
        "_voice_efx",
        "_voices",
    )

    def __init__(
        self,
        library: bindings.OpenALLibrary,
        device: object,
        context: object,
        previous_context: object | None,
        previous_playback: Playback | None,
    ) -> None:
        self._library = library
        self._lock = RLock()
        self._device = device
        self._context = context
        self._previous_context = previous_context
        self._previous_playback = previous_playback
        self._token = object()
        self._clips: dict[object, int] = {}
        self._clip_infos: dict[object, SoundInfo] = {}
        self._voices: dict[object, int] = {}
        self._voice_clips: dict[object, object] = {}
        self._voice_configs: dict[object, VoiceConfig] = {}
        self._voice_efx: dict[object, _EfxResources] = {}
        self._streams: dict[object, _StreamRecord] = {}
        self._closed = False

    def __enter__(self) -> Self:
        with self._library._context_lock, self._lock:
            _activate(self)
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exception_type, traceback
        try:
            close_playback(self)
        except Exception as cleanup_error:
            if exception is None:
                raise
            exception.add_note(f"audio cleanup also failed: {cleanup_error}")


_active_playbacks: set[Playback] = set()
_active_playbacks_lock = RLock()


def _serialized_playback[**P, R](
    function: Callable[Concatenate[Playback, P], R],
) -> Callable[Concatenate[Playback, P], R]:
    """Run one complete playback operation under stable context ownership."""

    @wraps(function)
    def serialized(playback: Playback, /, *args: P.args, **kwargs: P.kwargs) -> R:
        with _playback_operation(playback):
            return function(playback, *args, **kwargs)

    return serialized


@contextmanager
def _playback_operation(playback: Playback) -> Iterator[None]:
    if not isinstance(playback, Playback):
        raise TypeError("playback must be a Playback")
    with playback._library._context_lock, playback._lock:
        _require_playback(playback)
        yield


def _same_context(left: object | None, right: object | None) -> bool:
    """Compare native context handles while supporting test doubles."""

    if left is right:
        return True
    try:
        return _pointer_address(left) == _pointer_address(right)
    except TypeError:
        return False


def _playback_for_context(
    library: bindings.OpenALLibrary,
    context: object | None,
) -> Playback | None:
    if context is None:
        return None
    return next(
        (
            playback
            for playback in _active_playbacks
            if playback._library is library
            and not playback._closed
            and _same_context(playback._context, context)
        ),
        None,
    )


def _live_previous_context(playback: Playback) -> object | None:
    """Follow closed managed predecessors to the nearest live context."""

    predecessor = playback._previous_playback
    while predecessor is not None:
        if not predecessor._closed:
            return predecessor._context
        if predecessor._previous_playback is None:
            return predecessor._previous_context
        predecessor = predecessor._previous_playback
    return playback._previous_context


_FORMAT_BY_LAYOUT = {
    (1, SampleType.UINT8): bindings.enums.ALFormat.FORMAT_MONO8,
    (1, SampleType.INT16): bindings.enums.ALFormat.FORMAT_MONO16,
    (2, SampleType.UINT8): bindings.enums.ALFormat.FORMAT_STEREO8,
    (2, SampleType.INT16): bindings.enums.ALFormat.FORMAT_STEREO16,
}

_VOICE_STATE_BY_AL = {
    int(bindings.enums.ALSourceState.INITIAL): VoiceState.INITIAL,
    int(bindings.enums.ALSourceState.PLAYING): VoiceState.PLAYING,
    int(bindings.enums.ALSourceState.PAUSED): VoiceState.PAUSED,
    int(bindings.enums.ALSourceState.STOPPED): VoiceState.STOPPED,
}

_DEFAULT_VOICE_CONFIG = VoiceConfig()
_DEFAULT_PLAYBACK_CONFIG = PlaybackConfig()
_DEFAULT_LISTENER = Listener()
_DEFAULT_ACOUSTICS = Acoustics()

_DISTANCE_MODEL_TO_AL = {
    DistanceModel.NONE: bindings.AL_NONE,
    DistanceModel.INVERSE: bindings.AL_INVERSE_DISTANCE,
    DistanceModel.INVERSE_CLAMPED: bindings.AL_INVERSE_DISTANCE_CLAMPED,
    DistanceModel.LINEAR: bindings.AL_LINEAR_DISTANCE,
    DistanceModel.LINEAR_CLAMPED: bindings.AL_LINEAR_DISTANCE_CLAMPED,
    DistanceModel.EXPONENT: bindings.AL_EXPONENT_DISTANCE,
    DistanceModel.EXPONENT_CLAMPED: bindings.AL_EXPONENT_DISTANCE_CLAMPED,
}
_DISTANCE_MODEL_BY_AL = {value: key for key, value in _DISTANCE_MODEL_TO_AL.items()}

_HRTF_STATUS_BY_ALC = {
    bindings.ALC_HRTF_DISABLED_SOFT: HRTFStatus.DISABLED,
    bindings.ALC_HRTF_ENABLED_SOFT: HRTFStatus.ENABLED,
    bindings.ALC_HRTF_DENIED_SOFT: HRTFStatus.DENIED,
    bindings.ALC_HRTF_REQUIRED_SOFT: HRTFStatus.REQUIRED,
    bindings.ALC_HRTF_HEADPHONES_DETECTED_SOFT: HRTFStatus.HEADPHONES_DETECTED,
    bindings.ALC_HRTF_UNSUPPORTED_FORMAT_SOFT: HRTFStatus.UNSUPPORTED_FORMAT,
}


def _voice_config_with_overrides(
    config: VoiceConfig | None,
    *,
    position: Vector3 | None = None,
    velocity: Vector3 | None = None,
    direction: Vector3 | None = None,
    gain: float | None = None,
    pitch: float | None = None,
    looping: bool | None = None,
    relative: bool | None = None,
    min_gain: float | None = None,
    max_gain: float | None = None,
    reference_distance: float | None = None,
    max_distance: float | None = None,
    rolloff_factor: float | None = None,
    cone_inner_angle: float | None = None,
    cone_outer_angle: float | None = None,
    cone_outer_gain: float | None = None,
    filter: Filter | None = _OMITTED_FILTER,
    effect_sends: tuple[EffectSend, ...] | list[EffectSend] | None = None,
) -> VoiceConfig:
    if config is None:
        config = _DEFAULT_VOICE_CONFIG
    elif not isinstance(config, VoiceConfig):
        raise TypeError("config must be a VoiceConfig or None")
    return VoiceConfig(
        position=config.position if position is None else position,
        velocity=config.velocity if velocity is None else velocity,
        direction=config.direction if direction is None else direction,
        gain=config.gain if gain is None else gain,
        pitch=config.pitch if pitch is None else pitch,
        looping=config.looping if looping is None else looping,
        relative=config.relative if relative is None else relative,
        min_gain=config.min_gain if min_gain is None else min_gain,
        max_gain=config.max_gain if max_gain is None else max_gain,
        reference_distance=(
            config.reference_distance
            if reference_distance is None
            else reference_distance
        ),
        max_distance=(config.max_distance if max_distance is None else max_distance),
        rolloff_factor=(
            config.rolloff_factor if rolloff_factor is None else rolloff_factor
        ),
        cone_inner_angle=(
            config.cone_inner_angle if cone_inner_angle is None else cone_inner_angle
        ),
        cone_outer_angle=(
            config.cone_outer_angle if cone_outer_angle is None else cone_outer_angle
        ),
        cone_outer_gain=(
            config.cone_outer_gain if cone_outer_gain is None else cone_outer_gain
        ),
        filter=config.filter if isinstance(filter, _UnsetType) else filter,
        effect_sends=(
            config.effect_sends if effect_sends is None else tuple(effect_sends)
        ),
    )


def _require_playback(playback: Playback) -> None:
    if not isinstance(playback, Playback):
        raise TypeError("playback must be a Playback")
    if playback._closed:
        raise PlaybackClosedError("playback session is closed")


def _activate(playback: Playback) -> None:
    _require_playback(playback)
    if not playback._library.alc.make_context_current(playback._context):
        raise AudioBackendError("could not make the playback context current")


def _clear_al_errors(playback: Playback) -> None:
    for _ in range(16):
        if int(playback._library.al.get_error()) == bindings.AL_NO_ERROR:
            return
    raise AudioBackendError("OpenAL error state could not be cleared")


def _clear_alc_errors(library: bindings.OpenALLibrary, device: object | None) -> None:
    for _ in range(16):
        if int(library.alc.get_error(device)) == bindings.ALC_NO_ERROR:
            return
    raise AudioBackendError("OpenAL ALC error state could not be cleared")


def _prepare_al(playback: Playback) -> None:
    _activate(playback)
    _clear_al_errors(playback)


def _check_al_error(playback: Playback, operation: str) -> None:
    code = int(playback._library.al.get_error())
    if code == bindings.AL_NO_ERROR:
        return
    try:
        name = bindings.enums.ALErrorCode(code).name
    except ValueError:
        name = f"unknown error 0x{code:04x}"
    raise AudioBackendError(f"{operation} failed: OpenAL {name}")


def _check_alc_error(
    library: bindings.OpenALLibrary,
    device: object | None,
    operation: str,
) -> None:
    code = int(library.alc.get_error(device))
    if code == bindings.ALC_NO_ERROR:
        return
    try:
        name = bindings.enums.ALCContextErrorCode(code).name
    except ValueError:
        name = f"unknown error 0x{code:04x}"
    raise AudioBackendError(f"{operation} failed: OpenAL ALC {name}")


def _clip_identifier(playback: Playback, clip: Clip) -> int:
    _require_playback(playback)
    if not isinstance(clip, Clip) or clip._owner is not playback._token:
        raise InvalidHandleError("clip does not belong to this playback session")
    identifier = playback._clips.get(clip._token)
    if identifier is None or identifier != clip._identifier:
        raise InvalidHandleError("clip has been released")
    return identifier


def _voice_identifier(playback: Playback, voice: Voice) -> int:
    _require_playback(playback)
    if not isinstance(voice, Voice) or voice._owner is not playback._token:
        raise InvalidHandleError("voice does not belong to this playback session")
    identifier = playback._voices.get(voice._token)
    if identifier is None or identifier != voice._identifier:
        raise InvalidHandleError("voice has been released")
    return identifier


def _voice_clip_info(playback: Playback, voice: Voice) -> SoundInfo:
    """Return metadata for the clip attached to a validated static voice."""

    _voice_identifier(playback, voice)
    clip_token = playback._voice_clips[voice._token]
    return playback._clip_infos[clip_token]


def _stream_record(playback: Playback, stream: Stream) -> _StreamRecord:
    _require_playback(playback)
    if not isinstance(stream, Stream) or stream._owner is not playback._token:
        raise InvalidHandleError("stream does not belong to this playback session")
    record = playback._streams.get(stream._token)
    if record is None or record.identifier != stream._identifier:
        raise InvalidHandleError("stream has been released")
    return record


def _validate_stream_layout(
    channels: int,
    sample_rate: int,
    sample_type: SampleType,
    buffer_count: int,
) -> None:
    if isinstance(channels, bool) or not isinstance(channels, int):
        raise TypeError("channels must be an integer")
    if channels not in (1, 2):
        raise ValueError("channels must be 1 or 2")
    if isinstance(sample_rate, bool) or not isinstance(sample_rate, int):
        raise TypeError("sample_rate must be an integer")
    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    if not isinstance(sample_type, SampleType):
        raise TypeError("sample_type must be a SampleType")
    if isinstance(buffer_count, bool) or not isinstance(buffer_count, int):
        raise TypeError("buffer_count must be an integer")
    if buffer_count <= 0:
        raise ValueError("buffer_count must be positive")


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
    playback: Playback, identifier: int, config: VoiceConfig
) -> None:
    al = playback._library.al
    al.source3f(identifier, bindings.AL_POSITION, *config.position)
    al.source3f(identifier, bindings.AL_VELOCITY, *config.velocity)
    al.source3f(identifier, bindings.AL_DIRECTION, *config.direction)
    al.sourcef(identifier, bindings.AL_GAIN, config.gain)
    al.sourcef(identifier, bindings.AL_PITCH, config.pitch)
    al.sourcef(identifier, bindings.AL_MIN_GAIN, config.min_gain)
    al.sourcef(identifier, bindings.AL_MAX_GAIN, config.max_gain)
    al.sourcef(identifier, bindings.AL_REFERENCE_DISTANCE, config.reference_distance)
    al.sourcef(identifier, bindings.AL_MAX_DISTANCE, config.max_distance)
    al.sourcef(identifier, bindings.AL_ROLLOFF_FACTOR, config.rolloff_factor)
    al.sourcef(identifier, bindings.AL_CONE_INNER_ANGLE, config.cone_inner_angle)
    al.sourcef(identifier, bindings.AL_CONE_OUTER_ANGLE, config.cone_outer_angle)
    al.sourcef(identifier, bindings.AL_CONE_OUTER_GAIN, config.cone_outer_gain)
    al.sourcei(identifier, bindings.AL_LOOPING, int(config.looping))
    al.sourcei(identifier, bindings.AL_SOURCE_RELATIVE, int(config.relative))


def _apply_voice_config_changes(
    playback: Playback,
    identifier: int,
    previous: VoiceConfig,
    current: VoiceConfig,
) -> None:
    """Apply only properties changed by a partial voice update."""

    al = playback._library.al
    if current.position != previous.position:
        al.source3f(identifier, bindings.AL_POSITION, *current.position)
    if current.velocity != previous.velocity:
        al.source3f(identifier, bindings.AL_VELOCITY, *current.velocity)
    if current.direction != previous.direction:
        al.source3f(identifier, bindings.AL_DIRECTION, *current.direction)
    float_properties = (
        (bindings.AL_GAIN, previous.gain, current.gain),
        (bindings.AL_PITCH, previous.pitch, current.pitch),
        (bindings.AL_MIN_GAIN, previous.min_gain, current.min_gain),
        (bindings.AL_MAX_GAIN, previous.max_gain, current.max_gain),
        (
            bindings.AL_REFERENCE_DISTANCE,
            previous.reference_distance,
            current.reference_distance,
        ),
        (bindings.AL_MAX_DISTANCE, previous.max_distance, current.max_distance),
        (bindings.AL_ROLLOFF_FACTOR, previous.rolloff_factor, current.rolloff_factor),
        (
            bindings.AL_CONE_INNER_ANGLE,
            previous.cone_inner_angle,
            current.cone_inner_angle,
        ),
        (
            bindings.AL_CONE_OUTER_ANGLE,
            previous.cone_outer_angle,
            current.cone_outer_angle,
        ),
        (
            bindings.AL_CONE_OUTER_GAIN,
            previous.cone_outer_gain,
            current.cone_outer_gain,
        ),
    )
    for parameter, old_value, new_value in float_properties:
        if new_value != old_value:
            al.sourcef(identifier, parameter, new_value)
    if current.looping != previous.looping:
        al.sourcei(identifier, bindings.AL_LOOPING, int(current.looping))
    if current.relative != previous.relative:
        al.sourcei(identifier, bindings.AL_SOURCE_RELATIVE, int(current.relative))


def _load_playback_library(
    library: bindings.OpenALLibrary | None,
) -> bindings.OpenALLibrary:
    if library is not None:
        return library
    try:
        return bindings.load()
    except bindings.LibraryNotFoundError as error:
        raise PlaybackOpenError("could not load an OpenAL library") from error


def list_playback_devices(
    *, library: bindings.OpenALLibrary | None = None
) -> tuple[PlaybackDevice, ...]:
    """Return playback devices known to the selected OpenAL runtime."""

    library = _load_playback_library(library)
    _clear_alc_errors(library, None)
    enumerate_all = library.alc.is_extension_present(None, "ALC_ENUMERATE_ALL_EXT")
    if enumerate_all:
        devices_selector = bindings.ALC_ALL_DEVICES_SPECIFIER
        default_selector = bindings.ALC_DEFAULT_ALL_DEVICES_SPECIFIER
    else:
        devices_selector = bindings.ALC_DEVICE_SPECIFIER
        default_selector = bindings.ALC_DEFAULT_DEVICE_SPECIFIER

    names = library.alc.get_strings(None, devices_selector)
    default_name = library.alc.get_string(None, default_selector)
    _check_alc_error(library, None, "enumerate playback devices")
    return tuple(
        PlaybackDevice(name, is_default=name == default_name)
        for name in dict.fromkeys(names)
    )


def open_playback(
    device_name: PlaybackDevice | str | bytes | None = None,
    *,
    config: PlaybackConfig = _DEFAULT_PLAYBACK_CONFIG,
    library: bindings.OpenALLibrary | None = None,
) -> Playback:
    """Open a managed playback session."""

    if not isinstance(config, PlaybackConfig):
        raise TypeError("config must be a PlaybackConfig")
    if isinstance(device_name, PlaybackDevice):
        device_name = device_name.name
    elif device_name is not None and not isinstance(device_name, (str, bytes)):
        raise TypeError("device_name must be a PlaybackDevice, str, bytes, or None")

    library = _load_playback_library(library)
    with _active_playbacks_lock, library._context_lock:
        previous_context = library.alc.get_current_context()
        previous_playback = _playback_for_context(library, previous_context)
        device = library.alc.open_device(device_name)
        if not device:
            raise PlaybackOpenError("could not open the requested playback device")
        context: object | None = None
        try:
            attributes: tuple[int, ...] | None = None
            if config.hrtf is not None and library.alc.is_extension_present(
                device, "ALC_SOFT_HRTF"
            ):
                attributes = (bindings.ALC_HRTF_SOFT, int(config.hrtf))
            context = library.alc.create_context(device, attributes)
            if not context:
                raise PlaybackOpenError("could not create an OpenAL context")
            if not library.alc.make_context_current(context):
                raise PlaybackOpenError("could not make the OpenAL context current")
        except Exception:
            if context is not None:
                library.alc.destroy_context(context)
            library.alc.close_device(device)
            raise
        playback = Playback(
            library,
            device,
            context,
            previous_context,
            previous_playback,
        )
        _active_playbacks.add(playback)
        return playback


@_serialized_playback
def get_playback_info(playback: Playback) -> PlaybackInfo:
    """Return observed device, renderer, version, and HRTF information."""

    _prepare_al(playback)
    library = playback._library
    _clear_alc_errors(library, playback._device)
    device_name = library.alc.get_string(
        playback._device, bindings.ALC_DEVICE_SPECIFIER
    )
    if library.alc.is_extension_present(playback._device, "ALC_SOFT_HRTF"):
        native_status = library.alc.get_integerv(
            playback._device, bindings.ALC_HRTF_STATUS_SOFT, 1
        )[0]
        hrtf_status = _HRTF_STATUS_BY_ALC.get(native_status, HRTFStatus.UNKNOWN)
        hrtf_name = library.alc.get_string(
            playback._device, bindings.ALC_HRTF_SPECIFIER_SOFT
        )
        if not hrtf_name:
            hrtf_name = None
    else:
        hrtf_status = HRTFStatus.UNAVAILABLE
        hrtf_name = None
    _check_alc_error(library, playback._device, "query playback information")

    renderer = library.al.get_string(bindings.AL_RENDERER)
    version = library.al.get_string(bindings.AL_VERSION)
    _check_al_error(playback, "query playback information")
    if device_name is None or renderer is None or version is None:
        raise AudioBackendError("OpenAL returned incomplete playback information")

    return PlaybackInfo(
        device_name=device_name,
        renderer=renderer,
        version=version,
        hrtf_status=hrtf_status,
        hrtf_name=hrtf_name,
    )


def close_playback(playback: Playback) -> None:
    """Release every resource and close a playback session.

    Closing an already closed session is harmless.
    """

    if not isinstance(playback, Playback):
        raise TypeError("playback must be a Playback")
    with (
        _active_playbacks_lock,
        playback._library._context_lock,
        playback._lock,
    ):
        if not playback._closed:
            _close_playback(playback)


def _close_playback(playback: Playback) -> None:
    """Close a validated, live playback while lifecycle state is serialized."""

    first_error: Exception | None = None

    def remember(error: Exception) -> None:
        nonlocal first_error
        if first_error is None:
            first_error = error

    current_context = playback._library.alc.get_current_context()
    restore_context = (
        _live_previous_context(playback)
        if _same_context(current_context, playback._context)
        else current_context
    )

    try:
        if not playback._library.alc.make_context_current(playback._context):
            remember(AudioBackendError("could not activate context for cleanup"))
        else:
            try:
                _clear_al_errors(playback)
                source_ids = tuple(playback._voices.values()) + tuple(
                    record.identifier for record in playback._streams.values()
                )
                if source_ids:
                    playback._library.al.source_stopv(source_ids)
                    playback._library.al.delete_sources(source_ids)
                efx_resources = tuple(playback._voice_efx.values()) + tuple(
                    record.efx for record in playback._streams.values()
                )
                combined_efx = _EfxResources(
                    effects=tuple(
                        identifier
                        for resources in efx_resources
                        for identifier in resources.effects
                    ),
                    slots=tuple(
                        identifier
                        for resources in efx_resources
                        for identifier in resources.slots
                    ),
                    send_filters=tuple(
                        identifier
                        for resources in efx_resources
                        for identifier in resources.filters
                    ),
                )
                _delete_efx_resources(
                    playback,
                    combined_efx,
                    operation="EFX cleanup",
                )
                buffer_ids = tuple(playback._clips.values()) + tuple(
                    identifier
                    for record in playback._streams.values()
                    for identifier in record.buffers
                )
                if buffer_ids:
                    playback._library.al.delete_buffers(buffer_ids)
                _check_al_error(playback, "audio cleanup")
            except Exception as error:
                remember(error)
    finally:
        try:
            if not playback._library.alc.make_context_current(restore_context):
                remember(AudioBackendError("could not restore the previous context"))
        except Exception as error:
            remember(error)
        try:
            playback._library.alc.destroy_context(playback._context)
        except Exception as error:
            remember(error)
        try:
            if not playback._library.alc.close_device(playback._device):
                remember(AudioBackendError("could not close the playback device"))
        except Exception as error:
            remember(error)
        playback._voices.clear()
        playback._voice_clips.clear()
        playback._voice_configs.clear()
        playback._voice_efx.clear()
        playback._streams.clear()
        playback._clips.clear()
        playback._clip_infos.clear()
        playback._closed = True
        _active_playbacks.discard(playback)

    if first_error is not None:
        raise first_error


@_serialized_playback
def upload(playback: Playback, pcm: PCM) -> Clip:
    """Upload immutable PCM data and return its opaque clip identity."""

    if not isinstance(pcm, PCM):
        raise TypeError("pcm must be a PCM value")
    _prepare_al(playback)
    identifiers = playback._library.al.gen_buffers()
    if len(identifiers) != 1:
        raise AudioBackendError("OpenAL did not create exactly one buffer")
    identifier = identifiers[0]
    try:
        _check_al_error(playback, "create clip")
        playback._library.al.buffer_data(
            identifier,
            _FORMAT_BY_LAYOUT[(pcm.channels, pcm.sample_type)],
            pcm.samples,
            pcm.sample_rate,
        )
        _check_al_error(playback, "upload clip")
    except Exception:
        _clear_al_errors(playback)
        playback._library.al.delete_buffers((identifier,))
        playback._library.al.get_error()
        raise
    token = object()
    playback._clips[token] = identifier
    playback._clip_infos[token] = pcm.info
    return Clip(playback._token, token, identifier, pcm.info)


@_serialized_playback
def _create_voice(
    playback: Playback,
    clip: Clip,
    config: VoiceConfig = _DEFAULT_VOICE_CONFIG,
    *,
    offset_seconds: float = 0.0,
    offset_frames: int | None = None,
    start: bool = True,
) -> Voice:
    """Create one configured static voice and optionally start it."""

    if not isinstance(config, VoiceConfig):
        raise TypeError("config must be a VoiceConfig")
    if not isinstance(start, bool):
        raise TypeError("start must be a boolean")
    clip_identifier = _clip_identifier(playback, clip)
    offset_seconds, offset_frames = _validate_offsets(
        clip.info, offset_seconds, offset_frames
    )
    _prepare_al(playback)
    identifiers = playback._library.al.gen_sources()
    if len(identifiers) != 1:
        raise AudioBackendError("OpenAL did not create exactly one source")
    identifier = identifiers[0]
    efx = _EMPTY_EFX_RESOURCES
    try:
        _check_al_error(playback, "create voice")
        _apply_voice_config(playback, identifier, config)
        playback._library.al.sourcei(identifier, bindings.AL_BUFFER, clip_identifier)
        if offset_frames is not None:
            playback._library.al.sourcei(
                identifier, bindings.AL_SAMPLE_OFFSET, offset_frames
            )
        elif offset_seconds:
            playback._library.al.sourcef(
                identifier, bindings.AL_SEC_OFFSET, offset_seconds
            )
        efx = _install_efx_resources(
            playback,
            identifier,
            config,
        )
        if start:
            playback._library.al.source_play(identifier)
        _check_al_error(playback, "play voice" if start else "create voice")
    except Exception:
        _clear_al_errors(playback)
        playback._library.al.source_stop(identifier)
        playback._library.al.delete_sources((identifier,))
        with suppress(Exception):
            _delete_efx_resources(
                playback,
                efx,
                operation="clean up incomplete voice EFX resources",
            )
        playback._library.al.get_error()
        raise
    token = object()
    playback._voices[token] = identifier
    playback._voice_clips[token] = clip._token
    playback._voice_configs[token] = config
    playback._voice_efx[token] = efx
    return Voice(playback._token, token, identifier)


def _play_voice(
    playback: Playback,
    clip: Clip,
    config: VoiceConfig = _DEFAULT_VOICE_CONFIG,
    *,
    offset_seconds: float = 0.0,
    offset_frames: int | None = None,
) -> Voice:
    """Create and immediately play one voice using a clip."""

    return _create_voice(
        playback,
        clip,
        config,
        offset_seconds=offset_seconds,
        offset_frames=offset_frames,
        start=True,
    )


@_serialized_playback
def open_stream(
    playback: Playback,
    *,
    channels: int,
    sample_rate: int,
    sample_type: SampleType = SampleType.INT16,
    buffer_count: int = 4,
    config: VoiceConfig = _DEFAULT_VOICE_CONFIG,
) -> Stream:
    """Create an unstarted source with a bounded pool of streaming buffers."""

    _validate_stream_layout(channels, sample_rate, sample_type, buffer_count)
    if not isinstance(config, VoiceConfig):
        raise TypeError("config must be a VoiceConfig")
    if config.looping:
        raise ValueError("streaming voices cannot loop")

    _prepare_al(playback)
    source_ids: tuple[int, ...] = ()
    buffer_ids: tuple[int, ...] = ()
    efx = _EMPTY_EFX_RESOURCES
    try:
        source_ids = playback._library.al.gen_sources()
        if len(source_ids) != 1:
            raise AudioBackendError("OpenAL did not create exactly one source")
        _check_al_error(playback, "create stream source")
        buffer_ids = playback._library.al.gen_buffers(buffer_count)
        if len(buffer_ids) != buffer_count:
            raise AudioBackendError(
                f"OpenAL did not create exactly {buffer_count} stream buffers"
            )
        _check_al_error(playback, "create stream buffers")
        _apply_voice_config(playback, source_ids[0], config)
        efx = _install_efx_resources(
            playback,
            source_ids[0],
            config,
        )
        _check_al_error(playback, "configure stream")
    except Exception:
        _clear_al_errors(playback)
        if source_ids:
            playback._library.al.source_stopv(source_ids)
            playback._library.al.delete_sources(source_ids)
        with suppress(Exception):
            _delete_efx_resources(
                playback,
                efx,
                operation="clean up incomplete stream EFX resources",
            )
        if buffer_ids:
            playback._library.al.delete_buffers(buffer_ids)
        playback._library.al.get_error()
        raise

    token = object()
    identifier = source_ids[0]
    playback._streams[token] = _StreamRecord(
        identifier=identifier,
        buffers=buffer_ids,
        free_buffers=deque(buffer_ids),
        queued_chunks=deque(),
        channels=channels,
        sample_rate=sample_rate,
        sample_type=sample_type,
        config=config,
        efx=efx,
    )
    return Stream(playback._token, token, identifier)


def _copy_stream_samples(samples: Buffer) -> bytes:
    try:
        view = memoryview(samples)
    except TypeError as error:
        raise TypeError("samples must be bytes-like") from error
    try:
        return view.tobytes()
    finally:
        view.release()


@_serialized_playback
def try_write_stream(
    playback: Playback,
    stream: Stream,
    samples: Buffer,
) -> bool:
    """Queue one complete PCM chunk, or report bounded-buffer backpressure."""

    record = _stream_record(playback, stream)
    if record.state in (StreamState.FINISHED, StreamState.STOPPED):
        raise InvalidVoiceStateError(
            f"cannot write to a stream in the {record.state.value} state"
        )
    if record.input_finished:
        raise InvalidVoiceStateError("cannot write to a stream after end-of-input")
    if not record.free_buffers:
        return False

    data = _copy_stream_samples(samples)
    if not data:
        raise ValueError("samples cannot be empty")
    frame_width = record.channels * record.sample_type.byte_width
    if len(data) % frame_width:
        raise ValueError("samples must contain a whole number of frames")

    _prepare_al(playback)
    buffer = record.free_buffers[0]
    playback._library.al.buffer_data(
        buffer,
        _FORMAT_BY_LAYOUT[(record.channels, record.sample_type)],
        data,
        record.sample_rate,
    )
    _check_al_error(playback, "upload stream chunk")
    playback._library.al.source_queue_buffers(record.identifier, (buffer,))
    _check_al_error(playback, "queue stream chunk")

    record.free_buffers.popleft()
    frame_count = len(data) // frame_width
    record.queued_chunks.append(
        _StreamChunk(
            buffer=buffer,
            frame_count=frame_count,
            duration=frame_count / record.sample_rate,
        )
    )
    record.underrun_active = False

    if record.state is StreamState.PLAYING:
        native_state = _get_voice_state(
            playback, record.identifier, "query stream after write"
        )
        if native_state is VoiceState.STOPPED:
            playback._library.al.source_play(record.identifier)
            _check_al_error(playback, "restart stream after write")
            processed_frames = sum(
                chunk.frame_count for chunk in tuple(record.queued_chunks)[:-1]
            )
            if processed_frames:
                playback._library.al.sourcei(
                    record.identifier, bindings.AL_SAMPLE_OFFSET, processed_frames
                )
                _check_al_error(playback, "skip processed stream chunks")
    return True


@_serialized_playback
def start_stream(playback: Playback, stream: Stream) -> None:
    """Start a primed stream for the first and only time."""

    record = _stream_record(playback, stream)
    if record.state is not StreamState.INITIAL:
        raise InvalidVoiceStateError(
            f"cannot start a stream in the {record.state.value} state"
        )
    if not record.queued_chunks:
        raise InvalidVoiceStateError("cannot start a stream without a queued chunk")
    _prepare_al(playback)
    playback._library.al.source_play(record.identifier)
    _check_al_error(playback, "start stream")
    record.state = StreamState.PLAYING


def _stream_status(record: _StreamRecord, offset_seconds: float = 0.0) -> StreamStatus:
    queued_seconds = sum(chunk.duration for chunk in record.queued_chunks)
    if record.queued_chunks:
        current_duration = record.queued_chunks[0].duration
        queued_seconds -= min(max(offset_seconds, 0.0), current_duration)
    return StreamStatus(
        state=record.state,
        input_finished=record.input_finished,
        queued_chunks=len(record.queued_chunks),
        queued_seconds=max(queued_seconds, 0.0),
        underrun_count=record.underrun_count,
    )


@_serialized_playback
def update_stream(playback: Playback, stream: Stream) -> StreamStatus:
    """Reclaim processed chunks, recover underruns, and return stream status."""

    record = _stream_record(playback, stream)
    if record.state in (StreamState.FINISHED, StreamState.STOPPED):
        return _stream_status(record)

    _prepare_al(playback)
    processed = int(
        playback._library.al.get_sourcei(
            record.identifier, bindings.AL_BUFFERS_PROCESSED
        )
    )
    _check_al_error(playback, "query processed stream buffers")
    if not 0 <= processed <= len(record.queued_chunks):
        raise AudioBackendError("OpenAL returned an invalid processed buffer count")
    if processed:
        returned = playback._library.al.source_unqueue_buffers(
            record.identifier, processed
        )
        _check_al_error(playback, "reclaim stream buffers")
        expected = tuple(
            chunk.buffer for chunk in tuple(record.queued_chunks)[:processed]
        )
        if returned != expected:
            raise AudioBackendError("OpenAL returned unexpected stream buffers")
        for _ in range(processed):
            chunk = record.queued_chunks.popleft()
            record.free_buffers.append(chunk.buffer)

    if not record.queued_chunks and record.input_finished:
        record.state = StreamState.FINISHED
        record.underrun_active = False
    elif record.state is StreamState.PLAYING:
        if not record.queued_chunks:
            if not record.underrun_active:
                record.underrun_count += 1
                record.underrun_active = True
        else:
            native_state = _get_voice_state(
                playback, record.identifier, "query stream playback state"
            )
            if native_state is VoiceState.STOPPED:
                playback._library.al.source_play(record.identifier)
                _check_al_error(playback, "restart stream after underrun")

    offset = 0.0
    if record.queued_chunks:
        offset = float(
            playback._library.al.get_sourcef(record.identifier, bindings.AL_SEC_OFFSET)
        )
        _check_al_error(playback, "query stream offset")
        if not math.isfinite(offset):
            raise AudioBackendError("OpenAL returned a non-finite stream offset")
    return _stream_status(record, offset)


@_serialized_playback
def finish_stream(playback: Playback, stream: Stream) -> None:
    """Declare end-of-input and allow already queued chunks to drain."""

    record = _stream_record(playback, stream)
    if record.state is StreamState.STOPPED:
        raise InvalidVoiceStateError("cannot finish a stream in the stopped state")
    if record.state is StreamState.FINISHED or record.input_finished:
        return
    record.input_finished = True
    record.underrun_active = False
    if not record.queued_chunks:
        record.state = StreamState.FINISHED


@_serialized_playback
def _set_voice_config(
    playback: Playback,
    voice: Voice | Stream,
    config: VoiceConfig,
    *,
    changed_only: bool,
) -> None:
    if not isinstance(config, VoiceConfig):
        raise TypeError("config must be a VoiceConfig")
    if isinstance(voice, Stream):
        record = _stream_record(playback, voice)
        identifier = record.identifier
        if config.looping:
            raise ValueError("streaming voices cannot loop")
        previous = record.config
        previous_efx = record.efx
    else:
        identifier = _voice_identifier(playback, voice)
        previous = playback._voice_configs[voice._token]
        previous_efx = playback._voice_efx[voice._token]
    _prepare_al(playback)
    replacement = _prepare_efx_replacement(
        playback,
        previous,
        previous_efx,
        config,
    )
    try:
        if changed_only:
            _apply_voice_config_changes(playback, identifier, previous, config)
        else:
            _apply_voice_config(playback, identifier, config)
        _check_al_error(playback, "configure voice")
        if replacement.direct_filter_changed or replacement.effect_sends_changed:
            _attach_efx_resources(
                playback,
                identifier,
                replacement.current,
                clear_send_count=len(previous_efx.slots),
                attach_direct_filter=replacement.direct_filter_changed,
                attach_effect_sends=replacement.effect_sends_changed,
            )
    except BaseException:
        with suppress(Exception):
            _clear_al_errors(playback)
        with suppress(Exception):
            _apply_voice_config(playback, identifier, previous)
            _check_al_error(playback, "restore voice configuration")
        with suppress(Exception):
            _clear_al_errors(playback)
        if replacement.direct_filter_changed or replacement.effect_sends_changed:
            with suppress(Exception):
                _attach_efx_resources(
                    playback,
                    identifier,
                    previous_efx,
                    clear_send_count=len(replacement.current.slots),
                    attach_direct_filter=replacement.direct_filter_changed,
                    attach_effect_sends=replacement.effect_sends_changed,
                )
        with suppress(Exception):
            _clear_al_errors(playback)
        with suppress(Exception):
            _delete_efx_resources(
                playback,
                replacement.created,
                operation="clean up failed voice configuration",
            )
        playback._library.al.get_error()
        raise

    if isinstance(voice, Stream):
        record.config = config
        record.efx = replacement.current
    else:
        playback._voice_configs[voice._token] = config
        playback._voice_efx[voice._token] = replacement.current

    _delete_efx_resources(
        playback,
        replacement.retired,
        operation="release replaced EFX resources",
    )


def set_voice_config(
    playback: Playback, voice: Voice | Stream, config: VoiceConfig
) -> None:
    """Apply a complete immutable configuration to a live voice or stream."""

    _set_voice_config(playback, voice, config, changed_only=False)


@_serialized_playback
def seek(playback: Playback, voice: Voice, offset_seconds: float) -> None:
    """Move a static voice's playhead to an offset in source-audio seconds."""

    info = _voice_clip_info(playback, voice)
    offset_seconds = _sound_offset(offset_seconds, info.duration_seconds)
    identifier = _voice_identifier(playback, voice)
    _prepare_al(playback)
    playback._library.al.sourcef(identifier, bindings.AL_SEC_OFFSET, offset_seconds)
    _check_al_error(playback, "seek voice")


@_serialized_playback
def seek_frames(playback: Playback, voice: Voice, offset_frames: int) -> None:
    """Move a static voice's playhead to an exact sample-frame offset."""

    info = _voice_clip_info(playback, voice)
    offset_frames = _frame_offset(offset_frames, info.frame_count)
    identifier = _voice_identifier(playback, voice)
    _prepare_al(playback)
    playback._library.al.sourcei(identifier, bindings.AL_SAMPLE_OFFSET, offset_frames)
    _check_al_error(playback, "seek voice by frames")


@_serialized_playback
def rewind(playback: Playback, voice: Voice) -> None:
    """Move a static voice to its beginning and set it to the initial state."""

    _control_voice(playback, voice, "rewind")


@_serialized_playback
def restart(playback: Playback, voice: Voice) -> None:
    """Rewind a static voice and immediately start it playing."""

    identifier = _voice_identifier(playback, voice)
    _prepare_al(playback)
    playback._library.al.source_rewind(identifier)
    playback._library.al.source_play(identifier)
    _check_al_error(playback, "restart voice")


@_serialized_playback
def _set_listener(playback: Playback, listener: Listener) -> None:
    """Apply an immutable listener description to the playback context."""

    if not isinstance(listener, Listener):
        raise TypeError("listener must be a Listener")
    _prepare_al(playback)
    al = playback._library.al
    al.listener3f(bindings.AL_POSITION, *listener.position)
    al.listener3f(bindings.AL_VELOCITY, *listener.velocity)
    al.listenerfv(bindings.AL_ORIENTATION, listener.forward + listener.up)
    al.listenerf(bindings.AL_GAIN, listener.gain)
    _check_al_error(playback, "configure listener")


@_serialized_playback
def _get_listener(playback: Playback) -> Listener:
    """Query the current listener description from a playback context."""

    _prepare_al(playback)
    al = playback._library.al
    position = al.get_listener3f(bindings.AL_POSITION)
    velocity = al.get_listener3f(bindings.AL_VELOCITY)
    orientation = al.get_listenerfv(bindings.AL_ORIENTATION, 6)
    gain = al.get_listenerf(bindings.AL_GAIN)
    _check_al_error(playback, "query listener")
    if len(orientation) != 6:
        raise AudioBackendError("OpenAL returned an invalid listener orientation")
    return Listener(
        position=position,
        velocity=velocity,
        forward=orientation[:3],
        up=orientation[3:],
        gain=float(gain),
    )


@_serialized_playback
def _set_acoustics(playback: Playback, acoustics: Acoustics) -> None:
    """Apply global distance and Doppler controls to a playback context."""

    if not isinstance(acoustics, Acoustics):
        raise TypeError("acoustics must be an Acoustics value")
    _prepare_al(playback)
    al = playback._library.al
    al.distance_model(_DISTANCE_MODEL_TO_AL[acoustics.distance_model])
    al.doppler_factor(acoustics.doppler_factor)
    al.speed_of_sound(acoustics.speed_of_sound)
    _check_al_error(playback, "configure acoustics")


@_serialized_playback
def _get_acoustics(playback: Playback) -> Acoustics:
    """Query global distance and Doppler controls from a playback context."""

    _prepare_al(playback)
    al = playback._library.al
    native_model = int(al.get_integer(bindings.AL_DISTANCE_MODEL))
    doppler_factor = float(al.get_float(bindings.AL_DOPPLER_FACTOR))
    speed_of_sound = float(al.get_float(bindings.AL_SPEED_OF_SOUND))
    _check_al_error(playback, "query acoustics")
    try:
        distance_model = _DISTANCE_MODEL_BY_AL[native_model]
    except KeyError as error:
        raise AudioBackendError(
            f"OpenAL returned unknown distance model 0x{native_model:04x}"
        ) from error
    return Acoustics(
        distance_model=distance_model,
        doppler_factor=doppler_factor,
        speed_of_sound=speed_of_sound,
    )


@_serialized_playback
def get_voice_status(playback: Playback, voice: Voice) -> VoiceStatus:
    """Return the current state and playback offset of a live voice."""

    identifier = _voice_identifier(playback, voice)
    _prepare_al(playback)
    state = _get_voice_state(playback, identifier, "query voice state")
    offset = playback._library.al.get_sourcef(identifier, bindings.AL_SEC_OFFSET)
    offset_frames = playback._library.al.get_sourcei(
        identifier, bindings.AL_SAMPLE_OFFSET
    )
    _check_al_error(playback, "query voice")
    return VoiceStatus(
        state=state,
        offset_seconds=float(offset),
        offset_frames=int(offset_frames),
    )


def _get_voice_state(playback: Playback, identifier: int, operation: str) -> VoiceState:
    raw_state = int(
        playback._library.al.get_sourcei(identifier, bindings.AL_SOURCE_STATE)
    )
    _check_al_error(playback, operation)
    try:
        return _VOICE_STATE_BY_AL[raw_state]
    except KeyError as error:
        raise AudioBackendError(
            f"OpenAL returned unknown voice state 0x{raw_state:04x}"
        ) from error


def _control_voice(playback: Playback, voice: Voice, operation: str) -> None:
    identifier = _voice_identifier(playback, voice)
    _prepare_al(playback)
    function = getattr(playback._library.al, f"source_{operation}")
    function(identifier)
    _check_al_error(playback, f"{operation} voice")


@_serialized_playback
def pause(playback: Playback, voice: Voice | Stream) -> None:
    """Pause a live voice or a logically playing stream."""

    if isinstance(voice, Stream):
        record = _stream_record(playback, voice)
        if record.state is not StreamState.PLAYING:
            return
        _prepare_al(playback)
        playback._library.al.source_pause(record.identifier)
        _check_al_error(playback, "pause stream")
        record.state = StreamState.PAUSED
        record.underrun_active = False
        return
    _control_voice(playback, voice, "pause")


@_serialized_playback
def resume(playback: Playback, voice: Voice | Stream) -> None:
    """Resume a paused voice or stream."""

    if isinstance(voice, Stream):
        record = _stream_record(playback, voice)
        if record.state is not StreamState.PAUSED:
            raise InvalidVoiceStateError(
                f"cannot resume a stream in the {record.state.value} state"
            )
        _prepare_al(playback)
        playback._library.al.source_play(record.identifier)
        _check_al_error(playback, "resume stream")
        record.state = StreamState.PLAYING
        return
    identifier = _voice_identifier(playback, voice)
    _prepare_al(playback)
    state = _get_voice_state(playback, identifier, "query voice before resume")
    if state is not VoiceState.PAUSED:
        raise InvalidVoiceStateError(
            f"cannot resume a voice in the {state.value} state"
        )
    playback._library.al.source_play(identifier)
    _check_al_error(playback, "resume voice")


@_serialized_playback
def stop(playback: Playback, voice: Voice | Stream) -> None:
    """Stop a live voice or discard a stream's queued audio."""

    if isinstance(voice, Stream):
        record = _stream_record(playback, voice)
        if record.state in (StreamState.FINISHED, StreamState.STOPPED):
            return
        _prepare_al(playback)
        playback._library.al.source_stop(record.identifier)
        _check_al_error(playback, "stop stream")
        queued_count = len(record.queued_chunks)
        if queued_count:
            returned = playback._library.al.source_unqueue_buffers(
                record.identifier, queued_count
            )
            _check_al_error(playback, "discard stopped stream buffers")
            expected = tuple(chunk.buffer for chunk in record.queued_chunks)
            if returned != expected:
                raise AudioBackendError("OpenAL returned unexpected stream buffers")
            for chunk in record.queued_chunks:
                record.free_buffers.append(chunk.buffer)
            record.queued_chunks.clear()
        record.state = StreamState.STOPPED
        record.underrun_active = False
        return
    _control_voice(playback, voice, "stop")


@_serialized_playback
def release_finished(playback: Playback) -> int:
    """Release all terminal voices and streams and return the count.

    OpenAL reports both naturally completed and explicitly stopped voices as
    stopped. Streams are collected only after their managed state becomes
    ``FINISHED`` or ``STOPPED``; this function never updates active streams.
    """

    _prepare_al(playback)
    stopped_tokens: list[object] = []
    stopped_identifiers: list[int] = []
    for token, identifier in tuple(playback._voices.items()):
        state = _get_voice_state(playback, identifier, "query finished voice")
        if state is VoiceState.STOPPED:
            stopped_tokens.append(token)
            stopped_identifiers.append(identifier)

    stream_tokens = [
        token
        for token, record in playback._streams.items()
        if record.state in (StreamState.FINISHED, StreamState.STOPPED)
    ]
    stream_identifiers = [
        playback._streams[token].identifier for token in stream_tokens
    ]
    stream_buffers = [
        identifier
        for token in stream_tokens
        for identifier in playback._streams[token].buffers
    ]

    if not stopped_identifiers and not stream_identifiers:
        return 0

    playback._library.al.delete_sources(stopped_identifiers + stream_identifiers)
    released_efx = tuple(
        playback._voice_efx[token] for token in stopped_tokens
    ) + tuple(playback._streams[token].efx for token in stream_tokens)
    combined_efx = _EfxResources(
        effects=tuple(
            identifier for resources in released_efx for identifier in resources.effects
        ),
        slots=tuple(
            identifier for resources in released_efx for identifier in resources.slots
        ),
        send_filters=tuple(
            identifier for resources in released_efx for identifier in resources.filters
        ),
    )
    _delete_efx_resources(
        playback,
        combined_efx,
        operation="release finished voice EFX resources",
    )
    if stream_buffers:
        playback._library.al.delete_buffers(stream_buffers)
    _check_al_error(playback, "release finished voices and streams")
    for token in stopped_tokens:
        del playback._voices[token]
        del playback._voice_clips[token]
        del playback._voice_configs[token]
        del playback._voice_efx[token]
    for token in stream_tokens:
        del playback._streams[token]
    return len(stopped_tokens) + len(stream_tokens)


@overload
def release(playback: Playback, resource: Clip) -> None: ...


@overload
def release(playback: Playback, resource: Voice) -> None: ...


@overload
def release(playback: Playback, resource: Stream) -> None: ...


def release(playback: Playback, resource: Clip | Voice | Stream) -> None:
    """Release a clip, voice, or stream before its playback session closes."""

    _release(playback, resource)


@_serialized_playback
def _release(playback: Playback, resource: Clip | Voice | Stream) -> None:
    if isinstance(resource, Stream):
        record = _stream_record(playback, resource)
        _prepare_al(playback)
        playback._library.al.source_stop(record.identifier)
        playback._library.al.delete_sources((record.identifier,))
        _delete_efx_resources(
            playback,
            record.efx,
            operation="release stream EFX resources",
        )
        playback._library.al.delete_buffers(record.buffers)
        _check_al_error(playback, "release stream")
        del playback._streams[resource._token]
        return
    if isinstance(resource, Voice):
        identifier = _voice_identifier(playback, resource)
        _prepare_al(playback)
        playback._library.al.source_stop(identifier)
        playback._library.al.delete_sources((identifier,))
        _delete_efx_resources(
            playback,
            playback._voice_efx[resource._token],
            operation="release voice EFX resources",
        )
        _check_al_error(playback, "release voice")
        del playback._voices[resource._token]
        del playback._voice_clips[resource._token]
        del playback._voice_configs[resource._token]
        del playback._voice_efx[resource._token]
        return
    if isinstance(resource, Clip):
        identifier = _clip_identifier(playback, resource)
        if resource._token in playback._voice_clips.values():
            raise ResourceInUseError("clip is still attached to a live voice")
        _prepare_al(playback)
        playback._library.al.delete_buffers((identifier,))
        _check_al_error(playback, "release clip")
        del playback._clips[resource._token]
        del playback._clip_infos[resource._token]
        return
    raise TypeError("resource must be a Clip, Voice, or Stream")
