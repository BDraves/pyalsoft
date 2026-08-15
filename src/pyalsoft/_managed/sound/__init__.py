"""Convenient sound playback facade and implicit-runtime controls."""

from __future__ import annotations

import atexit
from contextlib import nullcontext, suppress
from os import PathLike
from pathlib import Path
from threading import RLock
from typing import overload

from pyalsoft._managed.audio import PCM, AudioPath
from pyalsoft._managed.effects import _OMITTED_FILTER, EffectSend, Filter
from pyalsoft._managed.playback.session import (
    Playback,
    _get_acoustics,
    _get_listener,
    _playback_operation,
    _set_acoustics,
    _set_listener,
)
from pyalsoft._managed.playback.voices import (
    _play_voice,
    _voice_config_with_overrides,
)
from pyalsoft._managed.resources import Clip, SoundCacheInfo, Voice
from pyalsoft._managed.sound.handle import PlayingSound
from pyalsoft._managed.sound.runtime import _DefaultRuntime
from pyalsoft._managed.sound.wave import get_sound_info
from pyalsoft._managed.spatial import (
    Acoustics,
    DistanceModel,
    Listener,
    Vector3,
    VoiceConfig,
)

__all__ = [
    "PlayingSound",
    "clear_sound_cache",
    "get_acoustics",
    "get_listener",
    "get_sound_cache_info",
    "get_sound_info",
    "play",
    "set_acoustics",
    "set_listener",
    "set_sound_cache_limit",
    "shutdown",
    "update_acoustics",
    "update_listener",
]

_default_runtime: _DefaultRuntime | None = None
_default_lock = RLock()


def _get_default_runtime() -> _DefaultRuntime:
    global _default_runtime
    with _default_lock:
        if _default_runtime is None:
            _default_runtime = _DefaultRuntime()
        return _default_runtime


def set_sound_cache_limit(max_bytes: int | None) -> None:
    """Set the convenience runtime's file-cache byte budget.

    The default budget is 64 MiB. Reducing it immediately evicts least-recently
    used clips that are not attached to active sounds. Active clips remain pinned
    and may temporarily keep the cache over budget.

    Args:
        max_bytes: Non-negative byte budget, or ``None`` for no limit. Zero
            disables retention of inactive file clips.

    Raises:
        TypeError: ``max_bytes`` is not an integer or ``None``.
        ValueError: ``max_bytes`` is negative.
    """

    if max_bytes is not None:
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int):
            raise TypeError("max_bytes must be an integer or None")
        if max_bytes < 0:
            raise ValueError("max_bytes cannot be negative")
    _get_default_runtime().set_cache_limit(max_bytes)


def clear_sound_cache(path: AudioPath | None = None) -> int:
    """Evict file clips from the convenience runtime's cache.

    Clips attached to active sounds are marked for later eviction and are not
    included in the returned count. In-memory PCM passed to
    [`play`][pyalsoft.play] is never part of this cache.

    Args:
        path: Specific WAV path to evict. ``None`` targets every cached file.

    Returns:
        Number of clips evicted immediately.

    Raises:
        TypeError: ``path`` is neither path-like nor ``None``.
    """

    normalized: Path | None = None
    if path is not None:
        if not isinstance(path, (str, PathLike)):
            raise TypeError("path must be a path to a WAV file or None")
        normalized = Path(path).expanduser().resolve()
    return _get_default_runtime().clear_cache(normalized)


def get_sound_cache_info() -> SoundCacheInfo:
    """Return byte usage and activity for the convenience file cache.

    Querying cache state also reaps sounds that have completed and performs any
    deferred or budget-driven evictions.

    Returns:
        Current budget, byte use, clip counts, and pending-eviction count.
    """

    return _get_default_runtime().cache_info()


@overload
def set_listener(playback: Playback, listener: Listener) -> None: ...


@overload
def set_listener(listener: Listener, /) -> None: ...


def set_listener(
    playback: Playback | Listener, listener: Listener | None = None
) -> None:
    """Set the listener for an explicit session or the convenience runtime.

    Call ``set_listener(listener)`` for the convenience runtime, or
    ``set_listener(playback, listener)`` for an explicit session. Setting the
    convenience listener opens its playback session if necessary.

    Args:
        playback: Explicit session, or the listener when using the one-argument
            form.
        listener: Complete listener state for an explicit session.

    Raises:
        TypeError: The call form or listener value is invalid.
        PlaybackClosedError: The explicit session is closed.
        AudioBackendError: OpenAL cannot apply the listener state.
    """

    if isinstance(playback, Playback):
        if listener is None:
            raise TypeError("listener must be provided with an explicit Playback")
        _set_listener(playback, listener)
        return
    if listener is not None:
        raise TypeError("listener is only valid with an explicit Playback")
    if not isinstance(playback, Listener):
        raise TypeError("listener must be a Listener")
    _get_default_runtime().set_listener(playback)


def get_listener(playback: Playback | None = None) -> Listener:
    """Return the listener for an explicit session or the convenience runtime.

    Args:
        playback: Explicit session to query. ``None`` returns the convenience
            runtime's current state without opening an audio device.

    Returns:
        Complete current listener state.

    Raises:
        PlaybackClosedError: The explicit session is closed.
        AudioBackendError: OpenAL cannot return a valid listener state.
    """

    if playback is None:
        return _get_default_runtime().listener()
    return _get_listener(playback)


def update_listener(
    playback: Playback | None = None,
    *,
    position: Vector3 | None = None,
    velocity: Vector3 | None = None,
    forward: Vector3 | None = None,
    up: Vector3 | None = None,
    gain: float | None = None,
) -> Listener:
    """Apply a batch of listener changes and return the complete new state.

    Omitted fields retain their current values.

    Args:
        playback: Explicit session to update. ``None`` selects the convenience
            runtime.
        position: New listener position.
        velocity: New listener velocity used for Doppler shift.
        forward: New non-zero viewing-direction vector.
        up: New non-zero upward vector.
        gain: New non-negative final-mix linear gain.

    Returns:
        Validated listener state after applying the changes.

    Raises:
        TypeError: A value has the wrong type.
        ValueError: A vector is invalid or ``gain`` is negative or non-finite.
        PlaybackClosedError: The explicit session is closed.
        AudioBackendError: OpenAL cannot query or apply the listener state.
    """

    operation = nullcontext() if playback is None else _playback_operation(playback)
    with operation:
        current = get_listener(playback)
        updated = Listener(
            position=current.position if position is None else position,
            velocity=current.velocity if velocity is None else velocity,
            forward=current.forward if forward is None else forward,
            up=current.up if up is None else up,
            gain=current.gain if gain is None else gain,
        )
        if playback is None:
            _get_default_runtime().set_listener(updated)
        else:
            _set_listener(playback, updated)
        return updated


@overload
def set_acoustics(playback: Playback, acoustics: Acoustics) -> None: ...


@overload
def set_acoustics(acoustics: Acoustics, /) -> None: ...


def set_acoustics(
    playback: Playback | Acoustics, acoustics: Acoustics | None = None
) -> None:
    """Set acoustics for an explicit session or the convenience runtime.

    Call ``set_acoustics(acoustics)`` for the convenience runtime, or
    ``set_acoustics(playback, acoustics)`` for an explicit session. Setting the
    convenience state opens its playback session if necessary.

    Args:
        playback: Explicit session, or the acoustic settings when using the
            one-argument form.
        acoustics: Complete acoustic settings for an explicit session.

    Raises:
        TypeError: The call form or acoustic settings are invalid.
        PlaybackClosedError: The explicit session is closed.
        AudioBackendError: OpenAL cannot apply the acoustic settings.
    """

    if isinstance(playback, Playback):
        if acoustics is None:
            raise TypeError("acoustics must be provided with an explicit Playback")
        _set_acoustics(playback, acoustics)
        return
    if acoustics is not None:
        raise TypeError("acoustics is only valid with an explicit Playback")
    if not isinstance(playback, Acoustics):
        raise TypeError("acoustics must be an Acoustics value")
    _get_default_runtime().set_acoustics(playback)


def get_acoustics(playback: Playback | None = None) -> Acoustics:
    """Return acoustics for an explicit session or the convenience runtime.

    Args:
        playback: Explicit session to query. ``None`` returns the convenience
            runtime's current state without opening an audio device.

    Returns:
        Complete current acoustic settings.

    Raises:
        PlaybackClosedError: The explicit session is closed.
        AudioBackendError: OpenAL cannot return valid acoustic settings.
    """

    if playback is None:
        return _get_default_runtime().acoustics()
    return _get_acoustics(playback)


def update_acoustics(
    playback: Playback | None = None,
    *,
    distance_model: DistanceModel | None = None,
    doppler_factor: float | None = None,
    speed_of_sound: float | None = None,
) -> Acoustics:
    """Apply acoustic changes and return the complete new state.

    Omitted fields retain their current values.

    Args:
        playback: Explicit session to update. ``None`` selects the convenience
            runtime.
        distance_model: New distance-attenuation formula.
        doppler_factor: New non-negative Doppler scale.
        speed_of_sound: New propagation speed in world-units per second.

    Returns:
        Validated acoustic settings after applying the changes.

    Raises:
        TypeError: A value has the wrong type.
        ValueError: A numeric value is non-finite or outside its supported range.
        PlaybackClosedError: The explicit session is closed.
        AudioBackendError: OpenAL cannot query or apply the acoustic settings.
    """

    operation = nullcontext() if playback is None else _playback_operation(playback)
    with operation:
        current = get_acoustics(playback)
        updated = Acoustics(
            distance_model=(
                current.distance_model if distance_model is None else distance_model
            ),
            doppler_factor=(
                current.doppler_factor if doppler_factor is None else doppler_factor
            ),
            speed_of_sound=(
                current.speed_of_sound if speed_of_sound is None else speed_of_sound
            ),
        )
        if playback is None:
            _get_default_runtime().set_acoustics(updated)
        else:
            _set_acoustics(playback, updated)
        return updated


@overload
def play(
    playback: Playback,
    clip: Clip,
    config: VoiceConfig | None = None,
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
    filter: Filter | None = None,
    effect_sends: tuple[EffectSend, ...] | list[EffectSend] | None = None,
    offset_seconds: float = 0.0,
    offset_frames: int | None = None,
    spatialize: bool | None = None,
    direct_channels: bool = False,
) -> Voice: ...


@overload
def play(
    playback: AudioPath | PCM,
    /,
    *,
    config: VoiceConfig | None = None,
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
    filter: Filter | None = None,
    effect_sends: tuple[EffectSend, ...] | list[EffectSend] | None = None,
    offset_seconds: float = 0.0,
    offset_frames: int | None = None,
    spatialize: bool | None = None,
    direct_channels: bool = False,
) -> PlayingSound: ...


def play(
    playback: Playback | AudioPath | PCM,
    clip: Clip | None = None,
    config: VoiceConfig | None = None,
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
    offset_seconds: float = 0.0,
    offset_frames: int | None = None,
    spatialize: bool | None = None,
    direct_channels: bool = False,
) -> Voice | PlayingSound:
    """Play an explicit clip, WAV file, or PCM value.

    ``play(playback, clip, config)`` starts a clip owned by an explicit session.
    ``play(sound, config=config)`` starts asynchronous playback through the
    convenience runtime, where *sound* is a WAV path or in-memory
    [`PCM`][pyalsoft.PCM] value. The runtime keeps playing when the returned
    handle is discarded, and it caches file-backed clips by resolved path.

    Individual control keywords override the corresponding field in ``config``.
    ``filter=None`` explicitly removes a configured direct filter; omit
    ``filter`` to preserve the value from ``config``. Use an empty
    ``effect_sends`` sequence to remove configured auxiliary routes.
    Pass ``spatialize=False`` for UI, player-attached, and other sounds that
    should ignore position, distance, Doppler, and directional cones. Pass
    ``direct_channels=True`` when the source must additionally bypass HRTF
    virtualization. Convenience playback duplicates mono frames into stereo;
    explicit-session clips must already be stereo.

    Args:
        playback: Explicit playback session in the two-argument form; otherwise,
            a WAV path or PCM value to play through the convenience runtime.
        clip: Clip owned by ``playback``. Valid only in the explicit-session form.
        config: Base voice configuration. ``None`` uses all defaults.
        position: Sound position in world or listener-relative coordinates.
        velocity: Sound velocity used for Doppler shift.
        direction: Attenuation-cone direction; the zero vector is omnidirectional.
        gain: Non-negative pre-attenuation linear gain.
        pitch: Playback-rate multiplier from 0.5 through 2.0.
        looping: Whether the complete source repeats.
        relative: Whether coordinates are relative to the listener.
        min_gain: Lower post-attenuation gain clamp.
        max_gain: Upper post-attenuation gain clamp.
        reference_distance: Non-negative distance with unity attenuation.
        max_distance: Non-negative outer distance for clamped distance models.
        rolloff_factor: Non-negative distance-attenuation multiplier.
        cone_inner_angle: Full inner cone angle in degrees, from 0 through 360.
        cone_outer_angle: Full outer cone angle in degrees, from 0 through 360.
        cone_outer_gain: Linear gain outside the outer cone.
        filter: Direct EFX filter, or ``None`` to remove the base filter.
        effect_sends: Ordered auxiliary EFX routes. An empty sequence removes all.
        offset_seconds: Initial position in source-audio seconds. Must be
            non-negative and less than the source duration.
        offset_frames: Exact initial sample-frame index. When provided,
            ``offset_seconds`` must remain 0.0.
        spatialize: ``True`` forces spatial rendering, ``False`` disables it,
            and ``None`` leaves the decision to OpenAL based on the source
            format.
        direct_channels: Whether to route stereo channels directly to matching
            outputs, bypassing HRTF virtualization. Mono WAV and PCM values are
            duplicated to stereo by convenience playback. Explicit clips must
            already be stereo.

    Returns:
        A [`Voice`][pyalsoft.Voice] owned by the explicit session, or a
        [`PlayingSound`][pyalsoft.PlayingSound] owned by the convenience runtime.

    Raises:
        TypeError: The call form or an argument has the wrong type.
        ValueError: A configuration or initial offset is invalid.
        AudioFileError: A WAV file cannot be read or has an unsupported format.
        PlaybackOpenError: The convenience runtime cannot open an audio session.
        PlaybackClosedError: The explicit session is closed.
        InvalidHandleError: ``clip`` is released or belongs to another session.
        AudioBackendError: OpenAL cannot create, configure, or start the voice,
            or an explicit spatialization or direct-channel mode is requested
            without backend support.
    """

    resolved_config = _voice_config_with_overrides(
        config,
        position=position,
        velocity=velocity,
        direction=direction,
        gain=gain,
        pitch=pitch,
        looping=looping,
        relative=relative,
        min_gain=min_gain,
        max_gain=max_gain,
        reference_distance=reference_distance,
        max_distance=max_distance,
        rolloff_factor=rolloff_factor,
        cone_inner_angle=cone_inner_angle,
        cone_outer_angle=cone_outer_angle,
        cone_outer_gain=cone_outer_gain,
        filter=filter,
        effect_sends=effect_sends,
    )
    if isinstance(playback, Playback):
        if clip is None:
            raise TypeError("clip must be provided with an explicit Playback")
        return _play_voice(
            playback,
            clip,
            resolved_config,
            offset_seconds=offset_seconds,
            offset_frames=offset_frames,
            spatialize=spatialize,
            direct_channels=direct_channels,
        )
    if clip is not None:
        raise TypeError("clip is only valid with an explicit Playback")
    if not isinstance(playback, (str, PathLike, PCM)):
        raise TypeError("sound must be a path to a WAV file or a PCM value")
    return _get_default_runtime().play(
        playback,
        resolved_config,
        offset_seconds=offset_seconds,
        offset_frames=offset_frames,
        spatialize=spatialize,
        direct_channels=direct_channels,
    )


def shutdown() -> None:
    """Close and forget the convenience playback runtime, if it was opened.

    Active [`PlayingSound`][pyalsoft.PlayingSound] handles become stopped with an
    end reason of ``SoundEndReason.SHUTDOWN``. Calling this when no runtime exists
    is harmless. A later convenience call creates a fresh runtime.
    """

    global _default_runtime
    with _default_lock:
        runtime, _default_runtime = _default_runtime, None
        if runtime is not None:
            runtime.close()


def _shutdown_at_exit() -> None:
    with suppress(Exception):
        shutdown()


atexit.register(_shutdown_at_exit)
