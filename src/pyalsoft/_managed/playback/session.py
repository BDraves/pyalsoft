"""Playback session ownership, context activation, and device state."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import wraps
from threading import RLock
from types import TracebackType
from typing import TYPE_CHECKING, Concatenate, Self

from pyalsoft import bindings
from pyalsoft._managed._backend import _check_alc_error, _clear_alc_errors
from pyalsoft._managed.errors import (
    AudioBackendError,
    PlaybackClosedError,
    PlaybackOpenError,
)
from pyalsoft._managed.resources import (
    HRTFStatus,
    PlaybackConfig,
    PlaybackDevice,
    PlaybackInfo,
    PlaybackOutputMode,
    VoiceState,
)
from pyalsoft._managed.spatial import Acoustics, DistanceModel, Listener
from pyalsoft.bindings._library import _pointer_address

if TYPE_CHECKING:
    from pyalsoft._managed.audio import SoundInfo
    from pyalsoft._managed.playback.effects import _EfxResources
    from pyalsoft._managed.playback.streams import _StreamRecord
    from pyalsoft._managed.spatial import VoiceConfig


class Playback:
    """Opaque owner for a playback device, context, clips, voices, and streams.

    Instances are returned by [`open_playback`][pyalsoft.open_playback]. Use them
    as context managers or pass them to
    [`close_playback`][pyalsoft.close_playback] for deterministic cleanup.
    Operations are serialized per session and across sessions sharing a native
    library, so a session may safely be used from multiple Python threads.

    When this session's context is still current, closing restores the context
    that was current when the session opened. Do not construct instances
    directly. Closing a session invalidates every [`Clip`][pyalsoft.Clip],
    [`Voice`][pyalsoft.Voice], and [`Stream`][pyalsoft.Stream] that belongs to it.
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


_VOICE_STATE_BY_AL = {
    int(bindings.enums.ALSourceState.INITIAL): VoiceState.INITIAL,
    int(bindings.enums.ALSourceState.PLAYING): VoiceState.PLAYING,
    int(bindings.enums.ALSourceState.PAUSED): VoiceState.PAUSED,
    int(bindings.enums.ALSourceState.STOPPED): VoiceState.STOPPED,
}

_DEFAULT_PLAYBACK_CONFIG = PlaybackConfig()

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

_PLAYBACK_OUTPUT_MODE_TO_ALC = {
    PlaybackOutputMode.ANY: bindings.ALC_ANY_SOFT,
    PlaybackOutputMode.MONO: bindings.ALC_MONO_SOFT,
    PlaybackOutputMode.STEREO: bindings.ALC_STEREO_SOFT,
    PlaybackOutputMode.STEREO_BASIC: bindings.ALC_STEREO_BASIC_SOFT,
    PlaybackOutputMode.STEREO_UHJ: bindings.ALC_STEREO_UHJ_SOFT,
    PlaybackOutputMode.STEREO_HRTF: bindings.ALC_STEREO_HRTF_SOFT,
    PlaybackOutputMode.QUAD: bindings.ALC_QUAD_SOFT,
    PlaybackOutputMode.SURROUND_5_1: bindings.ALC_SURROUND_5_1_SOFT,
    PlaybackOutputMode.SURROUND_6_1: bindings.ALC_SURROUND_6_1_SOFT,
    PlaybackOutputMode.SURROUND_7_1: bindings.ALC_SURROUND_7_1_SOFT,
}
_PLAYBACK_OUTPUT_MODE_BY_ALC = {
    value: key for key, value in _PLAYBACK_OUTPUT_MODE_TO_ALC.items()
}


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


def _load_playback_library(
    library: bindings.OpenALLibrary | None,
) -> bindings.OpenALLibrary:
    if library is not None:
        return library
    try:
        return bindings.load()
    except bindings.LibraryNotFoundError as error:
        raise PlaybackOpenError("could not load an OpenAL library") from error


def _playback_context_attributes(
    config: PlaybackConfig,
    library: bindings.OpenALLibrary,
    device: object,
) -> tuple[int, ...] | None:
    """Translate managed requests into an extension-safe ALC attribute list."""

    attributes: list[int] = []

    def append(parameter: int, value: int | bool | None) -> None:
        if value is not None:
            attributes.extend((parameter, int(value)))

    append(bindings.ALC_FREQUENCY, config.sample_rate)
    append(bindings.ALC_REFRESH, config.refresh_rate)
    append(bindings.ALC_SYNC, config.synchronous)
    append(bindings.ALC_MONO_SOURCES, config.mono_sources)
    append(bindings.ALC_STEREO_SOURCES, config.stereo_sources)

    if library.alc.is_extension_present(device, "ALC_EXT_EFX"):
        append(bindings.ALC_MAX_AUXILIARY_SENDS, config.max_auxiliary_sends)
    if library.alc.is_extension_present(device, "ALC_SOFT_HRTF"):
        append(bindings.ALC_HRTF_SOFT, config.hrtf)
        if config.hrtf_name is not None:
            profiles = _get_hrtf_profiles(library, device)
            try:
                profile_id = profiles.index(config.hrtf_name)
            except ValueError:
                raise PlaybackOpenError(
                    f"HRTF profile is unavailable: {config.hrtf_name!r}"
                ) from None
            append(bindings.ALC_HRTF_ID_SOFT, profile_id)
    if library.alc.is_extension_present(device, "ALC_SOFT_output_limiter"):
        append(bindings.ALC_OUTPUT_LIMITER_SOFT, config.output_limiter)
    if config.output_mode is not None and library.alc.is_extension_present(
        device, "ALC_SOFT_output_mode"
    ):
        append(
            bindings.ALC_OUTPUT_MODE_SOFT,
            _PLAYBACK_OUTPUT_MODE_TO_ALC[config.output_mode],
        )
    return tuple(attributes) or None


def _get_hrtf_profiles(
    library: bindings.OpenALLibrary,
    device: object,
) -> tuple[str, ...]:
    """Enumerate HRTF names on an open device after extension validation."""

    _clear_alc_errors(library, device)
    count = library.alc.get_integerv(device, bindings.ALC_NUM_HRTF_SPECIFIERS_SOFT, 1)[
        0
    ]
    profiles = tuple(
        library.alc.get_stringi_soft(
            device,
            bindings.ALC_HRTF_SPECIFIER_SOFT,
            index,
        )
        for index in range(count)
    )
    _check_alc_error(library, device, "enumerate HRTF profiles")
    if any(profile is None for profile in profiles):
        raise AudioBackendError("OpenAL returned an incomplete HRTF profile list")
    return tuple(profile for profile in profiles if profile is not None)


def _close_playback_device(
    library: bindings.OpenALLibrary,
    device: object,
) -> bool:
    """Close a raw playback device and discard its extension entry points."""

    closed = library.alc.close_device(device)
    if closed:
        library._invalidate_device_extensions(device)
    return closed


def list_hrtf_profiles(
    device_name: PlaybackDevice | str | bytes | None = None,
    *,
    library: bindings.OpenALLibrary | None = None,
) -> tuple[str, ...]:
    """Return HRTF profile names available to a playback device.

    The device is opened only for enumeration and is closed before this
    function returns. An empty tuple means the selected device does not expose
    ``ALC_SOFT_HRTF`` or currently reports no profiles.

    Args:
        device_name: Playback device object or device specifier. ``None`` selects
            the runtime's default playback device.
        library: Loaded low-level library to query. By default, discover and load
            the platform's OpenAL implementation.

    Raises:
        TypeError: ``device_name`` has the wrong type.
        PlaybackOpenError: OpenAL could not be loaded or the device could not open.
        AudioBackendError: Profile enumeration or device cleanup failed.
    """

    if isinstance(device_name, PlaybackDevice):
        device_name = device_name.name
    elif device_name is not None and not isinstance(device_name, (str, bytes)):
        raise TypeError("device_name must be a PlaybackDevice, str, bytes, or None")

    library = _load_playback_library(library)
    with library._context_lock:
        device = library.alc.open_device(device_name)
        if not device:
            raise PlaybackOpenError("could not open the requested playback device")
        try:
            if not library.alc.is_extension_present(device, "ALC_SOFT_HRTF"):
                profiles: tuple[str, ...] = ()
            else:
                profiles = _get_hrtf_profiles(library, device)
        except Exception:
            _close_playback_device(library, device)
            raise
        if not _close_playback_device(library, device):
            raise AudioBackendError("could not close the playback device")
        return profiles


def list_playback_devices(
    *, library: bindings.OpenALLibrary | None = None
) -> tuple[PlaybackDevice, ...]:
    """Return playback devices known to the selected OpenAL runtime.

    Args:
        library: Loaded low-level library to query. By default, discover and load
            the platform's OpenAL implementation.

    Returns:
        Devices in runtime order, with duplicate names removed. The tuple may be
        empty when the runtime reports no playback devices.

    Raises:
        PlaybackOpenError: No OpenAL implementation could be loaded.
        AudioBackendError: Device enumeration failed.
    """

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
    """Open a managed playback session and make its context current.

    The session restores the previously current context when it closes. Prefer a
    ``with`` statement so native resources are released deterministically.

    Args:
        device_name: Playback device object or device specifier. ``None`` selects
            the runtime's default playback device. A ``bytes`` value is
            passed to OpenAL unchanged.
        config: Context-creation preferences such as HRTF.
        library: Loaded low-level library to use. By default, discover and load
            the platform's OpenAL implementation.

    Returns:
        A new, open playback session.

    Raises:
        TypeError: A device or configuration argument has the wrong type.
        PlaybackOpenError: OpenAL could not be loaded or the device, context, or
            context activation could not be created.
    """

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
            attributes = _playback_context_attributes(config, library, device)
            context = library.alc.create_context(device, attributes)
            if not context:
                raise PlaybackOpenError("could not create an OpenAL context")
            if not library.alc.make_context_current(context):
                raise PlaybackOpenError("could not make the OpenAL context current")
        except Exception:
            if context is not None:
                library.alc.destroy_context(context)
            _close_playback_device(library, device)
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
    """Return observed device, context, renderer, and HRTF information.

    Args:
        playback: Open session to query.

    Returns:
        Properties reported by the active backend.

    Raises:
        PlaybackClosedError: ``playback`` is closed.
        AudioBackendError: OpenAL rejects the query or returns incomplete data.
    """

    _prepare_al(playback)
    library = playback._library
    _clear_alc_errors(library, playback._device)
    device_name = library.alc.get_string(
        playback._device, bindings.ALC_DEVICE_SPECIFIER
    )
    sample_rate = library.alc.get_integerv(playback._device, bindings.ALC_FREQUENCY, 1)[
        0
    ]
    refresh_rate = library.alc.get_integerv(playback._device, bindings.ALC_REFRESH, 1)[
        0
    ]
    synchronous = bool(
        library.alc.get_integerv(playback._device, bindings.ALC_SYNC, 1)[0]
    )
    mono_sources = library.alc.get_integerv(
        playback._device, bindings.ALC_MONO_SOURCES, 1
    )[0]
    stereo_sources = library.alc.get_integerv(
        playback._device, bindings.ALC_STEREO_SOURCES, 1
    )[0]

    has_efx = library.alc.is_extension_present(playback._device, "ALC_EXT_EFX")
    max_auxiliary_sends = (
        library.alc.get_integerv(playback._device, bindings.ALC_MAX_AUXILIARY_SENDS, 1)[
            0
        ]
        if has_efx
        else None
    )

    has_hrtf = library.alc.is_extension_present(playback._device, "ALC_SOFT_HRTF")
    if has_hrtf:
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

    has_output_limiter = library.alc.is_extension_present(
        playback._device, "ALC_SOFT_output_limiter"
    )
    output_limiter = (
        bool(
            library.alc.get_integerv(
                playback._device, bindings.ALC_OUTPUT_LIMITER_SOFT, 1
            )[0]
        )
        if has_output_limiter
        else None
    )

    has_output_mode = library.alc.is_extension_present(
        playback._device, "ALC_SOFT_output_mode"
    )
    if has_output_mode:
        native_output_mode = library.alc.get_integerv(
            playback._device, bindings.ALC_OUTPUT_MODE_SOFT, 1
        )[0]
        output_mode = _PLAYBACK_OUTPUT_MODE_BY_ALC.get(
            native_output_mode, PlaybackOutputMode.UNKNOWN
        )
    else:
        output_mode = None
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
        sample_rate=sample_rate,
        refresh_rate=refresh_rate,
        synchronous=synchronous,
        mono_sources=mono_sources,
        stereo_sources=stereo_sources,
        max_auxiliary_sends=max_auxiliary_sends,
        output_limiter=output_limiter,
        output_mode=output_mode,
    )


def close_playback(playback: Playback) -> None:
    """Release every resource and close a playback session.

    Closing an already closed session is harmless. All clips, voices, and
    streams owned by the session become invalid, even when cleanup reports an
    error.

    Args:
        playback: Session to close.

    Raises:
        TypeError: ``playback`` is not a [`Playback`][pyalsoft.Playback].
        AudioBackendError: OpenAL reports a resource or context cleanup failure.
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

    from pyalsoft._managed.playback.effects import (
        _delete_efx_resources,
        _EfxResources,
    )

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
            if not _close_playback_device(
                playback._library,
                playback._device,
            ):
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
