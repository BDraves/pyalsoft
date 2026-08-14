"""Clip upload, static voice control, and resource release."""

from __future__ import annotations

from contextlib import suppress
from typing import overload

from pyalsoft import bindings
from pyalsoft._managed._backend import _FORMAT_BY_LAYOUT
from pyalsoft._managed.audio import PCM, SoundInfo
from pyalsoft._managed.errors import (
    AudioBackendError,
    InvalidHandleError,
    InvalidVoiceStateError,
    ResourceInUseError,
)
from pyalsoft._managed.playback.effects import (
    _EMPTY_EFX_RESOURCES,
    _apply_voice_config,
    _attach_efx_resources,
    _delete_efx_resources,
    _EfxResources,
    _install_efx_resources,
    _prepare_efx_replacement,
)
from pyalsoft._managed.playback.session import (
    Playback,
    _check_al_error,
    _clear_al_errors,
    _get_voice_state,
    _prepare_al,
    _require_playback,
    _serialized_playback,
)
from pyalsoft._managed.playback.streams import _stream_record
from pyalsoft._managed.resources import (
    Clip,
    Stream,
    StreamState,
    Voice,
    VoiceState,
    VoiceStatus,
)
from pyalsoft._managed.spatial import (
    _DEFAULT_VOICE_CONFIG,
    _OMITTED_FILTER,
    EffectSend,
    Filter,
    Vector3,
    VoiceConfig,
    _frame_offset,
    _sound_offset,
    _UnsetType,
    _validate_offsets,
)


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


@_serialized_playback
def upload(playback: Playback, pcm: PCM) -> Clip:
    """Upload immutable PCM data to a playback session.

    OpenAL copies the samples into a native buffer. The returned clip may be
    played more than once and remains owned by ``playback`` until it is released
    explicitly or the session closes.

    Args:
        playback: Open session that will own the clip.
        pcm: Complete PCM sample data to copy.

    Returns:
        An opaque clip identity for the uploaded audio.

    Raises:
        TypeError: ``pcm`` is not a [`PCM`][pyalsoft.PCM].
        PlaybackClosedError: ``playback`` is closed.
        AudioBackendError: OpenAL cannot allocate or populate the buffer.
    """

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
    spatialize: bool | None = None,
) -> Voice:
    """Create one configured static voice and optionally start it."""

    if not isinstance(config, VoiceConfig):
        raise TypeError("config must be a VoiceConfig")
    if not isinstance(start, bool):
        raise TypeError("start must be a boolean")
    if spatialize is not None and not isinstance(spatialize, bool):
        raise TypeError("spatialize must be a boolean or None")
    clip_identifier = _clip_identifier(playback, clip)
    offset_seconds, offset_frames = _validate_offsets(
        clip.info, offset_seconds, offset_frames
    )
    _prepare_al(playback)
    if spatialize is not None and not playback._library.is_al_extension_present(
        "AL_SOFT_source_spatialize"
    ):
        raise AudioBackendError(
            "explicit spatialization requires the AL_SOFT_source_spatialize extension"
        )
    identifiers = playback._library.al.gen_sources()
    if len(identifiers) != 1:
        raise AudioBackendError("OpenAL did not create exactly one source")
    identifier = identifiers[0]
    efx = _EMPTY_EFX_RESOURCES
    try:
        _check_al_error(playback, "create voice")
        _apply_voice_config(playback, identifier, config)
        if spatialize is not None:
            playback._library.al.sourcei(
                identifier,
                bindings.AL_SOURCE_SPATIALIZE_SOFT,
                bindings.AL_TRUE if spatialize else bindings.AL_FALSE,
            )
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
    spatialize: bool | None = None,
) -> Voice:
    """Create and immediately play one voice using a clip."""

    return _create_voice(
        playback,
        clip,
        config,
        offset_seconds=offset_seconds,
        offset_frames=offset_frames,
        start=True,
        spatialize=spatialize,
    )


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
        _apply_voice_config(
            playback,
            identifier,
            config,
            previous=previous if changed_only else None,
        )
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
    """Apply a complete immutable configuration to a live voice or stream.

    Existing filters, effects, and auxiliary sends are replaced by the values in
    ``config``. Stream configurations cannot enable looping.

    Args:
        playback: Session that owns ``voice``.
        voice: Live static voice or stream to configure.
        config: Complete replacement configuration.

    Raises:
        TypeError: ``config`` is not a [`VoiceConfig`][pyalsoft.VoiceConfig].
        ValueError: Looping is enabled for a stream.
        InvalidHandleError: The handle is released or belongs to another session.
        PlaybackClosedError: ``playback`` is closed.
        AudioBackendError: OpenAL cannot apply the configuration or requested EFX.
    """

    _set_voice_config(playback, voice, config, changed_only=False)


@_serialized_playback
def seek(playback: Playback, voice: Voice, offset_seconds: float) -> None:
    """Move a static voice's playhead to a source-audio time offset.

    Args:
        playback: Session that owns ``voice``.
        voice: Live static voice to seek.
        offset_seconds: Finite offset greater than or equal to zero and strictly
            less than the clip duration.

    Raises:
        TypeError: ``offset_seconds`` is not numeric or a handle has the wrong type.
        ValueError: ``offset_seconds`` is non-finite or outside the clip.
        InvalidHandleError: ``voice`` is released or belongs to another session.
        PlaybackClosedError: ``playback`` is closed.
        AudioBackendError: OpenAL cannot move the playhead.
    """

    info = _voice_clip_info(playback, voice)
    offset_seconds = _sound_offset(offset_seconds, info.duration_seconds)
    identifier = _voice_identifier(playback, voice)
    _prepare_al(playback)
    playback._library.al.sourcef(identifier, bindings.AL_SEC_OFFSET, offset_seconds)
    _check_al_error(playback, "seek voice")


@_serialized_playback
def seek_frames(playback: Playback, voice: Voice, offset_frames: int) -> None:
    """Move a static voice's playhead to an exact sample-frame offset.

    Args:
        playback: Session that owns ``voice``.
        voice: Live static voice to seek.
        offset_frames: Integer frame index greater than or equal to zero and
            strictly less than the clip's frame count.

    Raises:
        TypeError: ``offset_frames`` is not an integer or a handle has the wrong
            type.
        ValueError: ``offset_frames`` is outside the clip.
        InvalidHandleError: ``voice`` is released or belongs to another session.
        PlaybackClosedError: ``playback`` is closed.
        AudioBackendError: OpenAL cannot move the playhead.
    """

    info = _voice_clip_info(playback, voice)
    offset_frames = _frame_offset(offset_frames, info.frame_count)
    identifier = _voice_identifier(playback, voice)
    _prepare_al(playback)
    playback._library.al.sourcei(identifier, bindings.AL_SAMPLE_OFFSET, offset_frames)
    _check_al_error(playback, "seek voice by frames")


@_serialized_playback
def rewind(playback: Playback, voice: Voice) -> None:
    """Move a static voice to its beginning and set it to the initial state.

    Args:
        playback: Session that owns ``voice``.
        voice: Live static voice to rewind.

    Raises:
        InvalidHandleError: ``voice`` is released or belongs to another session.
        PlaybackClosedError: ``playback`` is closed.
        AudioBackendError: OpenAL cannot rewind the voice.
    """

    _control_voice(playback, voice, "rewind")


@_serialized_playback
def restart(playback: Playback, voice: Voice) -> None:
    """Rewind a static voice and immediately start it playing.

    Args:
        playback: Session that owns ``voice``.
        voice: Live static voice to restart.

    Raises:
        InvalidHandleError: ``voice`` is released or belongs to another session.
        PlaybackClosedError: ``playback`` is closed.
        AudioBackendError: OpenAL cannot rewind or play the voice.
    """

    identifier = _voice_identifier(playback, voice)
    _prepare_al(playback)
    playback._library.al.source_rewind(identifier)
    playback._library.al.source_play(identifier)
    _check_al_error(playback, "restart voice")


@_serialized_playback
def get_voice_status(playback: Playback, voice: Voice) -> VoiceStatus:
    """Return the current state and playback offset of a live static voice.

    Args:
        playback: Session that owns ``voice``.
        voice: Live static voice to query.

    Returns:
        The observed OpenAL state and source-timeline offsets.

    Raises:
        InvalidHandleError: ``voice`` is released or belongs to another session.
        PlaybackClosedError: ``playback`` is closed.
        AudioBackendError: OpenAL returns an unknown state or rejects the query.
    """

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


def _control_voice(playback: Playback, voice: Voice, operation: str) -> None:
    identifier = _voice_identifier(playback, voice)
    _prepare_al(playback)
    function = getattr(playback._library.al, f"source_{operation}")
    function(identifier)
    _check_al_error(playback, f"{operation} voice")


@_serialized_playback
def pause(playback: Playback, voice: Voice | Stream) -> None:
    """Pause a live voice or a logically playing stream.

    Pausing a stream that is not currently playing is harmless. Static voices
    follow the underlying OpenAL pause semantics.

    Args:
        playback: Session that owns ``voice``.
        voice: Live static voice or stream to pause.

    Raises:
        InvalidHandleError: The handle is released or belongs to another session.
        PlaybackClosedError: ``playback`` is closed.
        AudioBackendError: OpenAL cannot pause the source.
    """

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
    """Resume a paused voice or stream.

    Args:
        playback: Session that owns ``voice``.
        voice: Paused static voice or stream to resume.

    Raises:
        InvalidHandleError: The handle is released or belongs to another session.
        InvalidVoiceStateError: ``voice`` is not paused.
        PlaybackClosedError: ``playback`` is closed.
        AudioBackendError: OpenAL cannot resume the source.
    """

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
    """Stop a live voice or discard a stream's queued audio.

    Stopping a terminal stream is harmless. The handle remains allocated until
    [`release`][pyalsoft.release],
    [`release_finished`][pyalsoft.release_finished], or session closure.

    Args:
        playback: Session that owns ``voice``.
        voice: Live static voice or stream to stop.

    Raises:
        InvalidHandleError: The handle is released or belongs to another session.
        PlaybackClosedError: ``playback`` is closed.
        AudioBackendError: OpenAL cannot stop the source or discard stream buffers.
    """

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

    Args:
        playback: Open session whose terminal resources should be released.

    Returns:
        Total number of released static voices and streams.

    Raises:
        PlaybackClosedError: ``playback`` is closed.
        AudioBackendError: OpenAL cannot query or release the resources.
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
    """Release a clip, voice, or stream before its playback session closes.

    Releasing a voice stops it. Releasing a stream stops it and discards queued
    audio. A clip cannot be released while any live voice still refers to it.
    Every successful release permanently invalidates the handle.

    Args:
        playback: Session that owns ``resource``.
        resource: Live clip, static voice, or stream to release.

    Raises:
        TypeError: ``resource`` is not a supported handle.
        InvalidHandleError: The handle is released or belongs to another session.
        ResourceInUseError: ``resource`` is a clip attached to a live voice.
        PlaybackClosedError: ``playback`` is closed.
        AudioBackendError: OpenAL cannot release the native resources.
    """

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
