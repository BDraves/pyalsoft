"""Clip upload, static voice control, and resource release."""

from __future__ import annotations

from contextlib import suppress
from typing import overload

from pyalsoft import bindings
from pyalsoft._managed._backend import (
    _FORMAT_BY_LAYOUT,
    _check_alc_error,
    _clear_alc_errors,
    _prepare_buffer_data,
    _require_al_extension,
    _require_pcm_layout,
)
from pyalsoft._managed.audio import (
    PCM,
    AudioPath,
    BufferData,
    BufferFormat,
    BufferInfo,
    SoundInfo,
)
from pyalsoft._managed.effects import (
    _OMITTED_FILTER,
    EffectSend,
    Filter,
    _UnsetType,
)
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
    _release_effect_bus,
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
from pyalsoft._managed.playback.source_controls import (
    _apply_start_delay,
    _require_source_start_delay,
    _start_source,
    _validate_playback_timing,
    _validate_source_layout,
)
from pyalsoft._managed.playback.streams import _stream_record
from pyalsoft._managed.resources import (
    Clip,
    EffectBus,
    PlaybackClock,
    Stream,
    StreamState,
    Voice,
    VoiceClock,
    VoiceLatency,
    VoiceState,
    VoiceStatus,
)
from pyalsoft._managed.spatial import (
    _DEFAULT_VOICE_CONFIG,
    _OMITTED_DISTANCE_MODEL,
    _OMITTED_RESAMPLER,
    _OMITTED_STEREO_ANGLES,
    _OMITTED_SUPER_STEREO_WIDTH,
    DirectChannelsMode,
    DistanceModel,
    Resampler,
    SpatializationMode,
    StereoMode,
    Vector3,
    VoiceConfig,
    _frame_offset,
    _sound_offset,
    _validate_offsets,
)

_ALINT_MAX = (1 << 31) - 1


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
    cone_outer_gain_high_frequency: float | None = None,
    distance_model: DistanceModel | None = _OMITTED_DISTANCE_MODEL,
    radius: float | None = None,
    spatialization: SpatializationMode | None = None,
    direct_channels: DirectChannelsMode | bool | None = None,
    stereo_angles: tuple[float, float] | None = _OMITTED_STEREO_ANGLES,
    resampler: Resampler | None = _OMITTED_RESAMPLER,
    air_absorption_factor: float | None = None,
    room_rolloff_factor: float | None = None,
    direct_filter_gain_high_frequency_auto: bool | None = None,
    auxiliary_send_filter_gain_auto: bool | None = None,
    auxiliary_send_filter_gain_high_frequency_auto: bool | None = None,
    stereo_mode: StereoMode | None = None,
    super_stereo_width: float | None = _OMITTED_SUPER_STEREO_WIDTH,
    filter: Filter | None = _OMITTED_FILTER,
    effect_sends: tuple[EffectSend, ...] | list[EffectSend] | None = None,
) -> VoiceConfig:
    if config is None:
        config = _DEFAULT_VOICE_CONFIG
    elif not isinstance(config, VoiceConfig):
        raise TypeError("config must be a VoiceConfig or None")
    if direct_channels is None:
        direct_channels_mode = config.direct_channels
    elif isinstance(direct_channels, bool):
        direct_channels_mode = (
            DirectChannelsMode.DROP_UNMATCHED
            if direct_channels
            else DirectChannelsMode.OFF
        )
    elif isinstance(direct_channels, DirectChannelsMode):
        direct_channels_mode = direct_channels
    else:
        raise TypeError(
            "direct_channels must be a boolean, DirectChannelsMode, or None"
        )
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
        cone_outer_gain_high_frequency=(
            config.cone_outer_gain_high_frequency
            if cone_outer_gain_high_frequency is None
            else cone_outer_gain_high_frequency
        ),
        distance_model=(
            config.distance_model
            if isinstance(distance_model, _UnsetType)
            else distance_model
        ),
        radius=config.radius if radius is None else radius,
        spatialization=(
            config.spatialization if spatialization is None else spatialization
        ),
        direct_channels=direct_channels_mode,
        stereo_angles=(
            config.stereo_angles
            if isinstance(stereo_angles, _UnsetType)
            else stereo_angles
        ),
        resampler=(
            config.resampler if isinstance(resampler, _UnsetType) else resampler
        ),
        air_absorption_factor=(
            config.air_absorption_factor
            if air_absorption_factor is None
            else air_absorption_factor
        ),
        room_rolloff_factor=(
            config.room_rolloff_factor
            if room_rolloff_factor is None
            else room_rolloff_factor
        ),
        direct_filter_gain_high_frequency_auto=(
            config.direct_filter_gain_high_frequency_auto
            if direct_filter_gain_high_frequency_auto is None
            else direct_filter_gain_high_frequency_auto
        ),
        auxiliary_send_filter_gain_auto=(
            config.auxiliary_send_filter_gain_auto
            if auxiliary_send_filter_gain_auto is None
            else auxiliary_send_filter_gain_auto
        ),
        auxiliary_send_filter_gain_high_frequency_auto=(
            config.auxiliary_send_filter_gain_high_frequency_auto
            if auxiliary_send_filter_gain_high_frequency_auto is None
            else auxiliary_send_filter_gain_high_frequency_auto
        ),
        stereo_mode=config.stereo_mode if stereo_mode is None else stereo_mode,
        super_stereo_width=(
            config.super_stereo_width
            if isinstance(super_stereo_width, _UnsetType)
            else super_stereo_width
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


def _voice_clip_info(playback: Playback, voice: Voice) -> SoundInfo | BufferInfo:
    """Return metadata for the clip attached to a validated static voice."""

    _voice_identifier(playback, voice)
    clip_token = playback._voice_clips[voice._token]
    return playback._clip_infos[clip_token]


def _validate_loop_points(
    loop_points: tuple[int, int] | None, frame_count: int
) -> tuple[int, int] | None:
    if loop_points is None:
        return None
    if not isinstance(loop_points, (tuple, list)):
        raise TypeError("loop_points must be a two-item tuple or list, or None")
    if len(loop_points) != 2:
        raise ValueError("loop_points must contain exactly two frame indices")
    start, end = loop_points
    if any(
        isinstance(value, bool) or not isinstance(value, int) for value in (start, end)
    ):
        raise TypeError("loop point frame indices must be integers")
    if not 0 <= start < end <= frame_count:
        raise ValueError(f"loop points must satisfy 0 <= start < end <= {frame_count}")
    if end > _ALINT_MAX:
        raise ValueError(f"loop point frame indices must not exceed {_ALINT_MAX}")
    return start, end


def upload(
    playback: Playback,
    pcm: PCM | BufferData | AudioPath,
    *,
    loop_points: tuple[int, int] | None = None,
) -> Clip:
    """Upload immutable audio data to a playback session.

    OpenAL copies the samples into a native buffer. The returned clip may be
    played more than once and remains owned by ``playback`` until it is released
    explicitly or the session closes. Optional loop points select the sample-frame
    range repeated by voices that enable looping; the start frame is inclusive
    and the end frame is exclusive.

    Args:
        playback: Open session that will own the clip.
        pcm: Complete PCM, exact-format buffer data, or supported WAV path to
            decode and copy.
        loop_points: Optional ``(start, end)`` loop-frame range. ``None`` makes
            looping voices repeat the complete clip.

    Returns:
        An opaque clip identity for the uploaded audio.

    Raises:
        TypeError: ``pcm`` is not [`PCM`][pyalsoft.PCM],
            [`BufferData`][pyalsoft.BufferData], or a path, or loop points are
            not integers.
        ValueError: The loop-point range is empty or outside the clip.
        PlaybackClosedError: ``playback`` is closed.
        AudioBackendError: OpenAL cannot allocate or populate the buffer, or
            loop points were requested without ``AL_SOFT_loop_points`` support.
    """

    if not isinstance(pcm, (PCM, BufferData)):
        from pyalsoft._managed.sound.wave import load_audio

        pcm = load_audio(pcm)
    return _upload(playback, pcm, loop_points=loop_points)


@_serialized_playback
def _upload(
    playback: Playback,
    pcm: PCM | BufferData,
    *,
    loop_points: tuple[int, int] | None,
) -> Clip:
    resolved_loop_points = _validate_loop_points(loop_points, pcm.frame_count)
    _prepare_al(playback)
    if isinstance(pcm, PCM):
        _require_pcm_layout(playback._library, pcm.channels, pcm.sample_type)
        native_format = _FORMAT_BY_LAYOUT[(pcm.channels, pcm.sample_type)]
    else:
        native_format = pcm.format.native_format
    if resolved_loop_points is not None:
        _require_al_extension(
            playback._library,
            "AL_SOFT_loop_points",
            "clip loop points",
        )
    identifiers = playback._library.al.gen_buffers()
    if len(identifiers) != 1:
        raise AudioBackendError("OpenAL did not create exactly one buffer")
    identifier = identifiers[0]
    try:
        _check_al_error(playback, "create clip")
        if isinstance(pcm, BufferData):
            _prepare_buffer_data(playback._library, identifier, pcm)
        playback._library.al.buffer_data(
            identifier,
            native_format,
            pcm.samples,
            pcm.sample_rate,
        )
        if resolved_loop_points is not None:
            playback._library.al.bufferiv(
                identifier,
                bindings.AL_LOOP_POINTS_SOFT,
                resolved_loop_points,
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
    return Clip(playback._token, token, identifier, pcm.info, resolved_loop_points)


@_serialized_playback
def _create_voice(
    playback: Playback,
    clip: Clip,
    config: VoiceConfig = _DEFAULT_VOICE_CONFIG,
    *,
    offset_seconds: float = 0.0,
    offset_frames: int | None = None,
    delay_seconds: float = 0.0,
    delay_frames: int | None = None,
    start_time_ns: int | None = None,
    start: bool = True,
) -> Voice:
    """Create one configured static voice and optionally start it."""

    if not isinstance(config, VoiceConfig):
        raise TypeError("config must be a VoiceConfig")
    if not isinstance(start, bool):
        raise TypeError("start must be a boolean")
    delay_seconds, delay_frames, start_time_ns = _validate_playback_timing(
        delay_seconds, delay_frames, start_time_ns
    )
    if not start and start_time_ns is not None:
        raise ValueError("start_time_ns requires start=True")
    clip_info = clip.info
    clip_format = clip_info.format if isinstance(clip_info, BufferInfo) else None
    _validate_source_layout(config, clip_info.channels, clip_format)
    clip_identifier = _clip_identifier(playback, clip)
    offset_seconds, offset_frames = _validate_offsets(
        clip.info, offset_seconds, offset_frames
    )
    if (delay_seconds != 0.0 or delay_frames is not None) and (
        offset_seconds != 0.0 or offset_frames is not None
    ):
        raise ValueError("initial offset and playback delay cannot both be set")
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
        _apply_start_delay(playback, identifier, delay_seconds, delay_frames)
        efx = _install_efx_resources(
            playback,
            identifier,
            config,
        )
        if start:
            _start_source(playback, identifier, start_time_ns)
        _check_al_error(playback, "play voice" if start else "create voice")
    except Exception:
        _clear_al_errors(playback)
        playback._library.al.source_stop(identifier)
        playback._library.al.delete_sources((identifier,))
        playback._super_stereo_width_defaults.pop(identifier, None)
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
    delay_seconds: float = 0.0,
    delay_frames: int | None = None,
    start_time_ns: int | None = None,
) -> Voice:
    """Create and immediately play one voice using a clip."""

    return _create_voice(
        playback,
        clip,
        config,
        offset_seconds=offset_seconds,
        offset_frames=offset_frames,
        delay_seconds=delay_seconds,
        delay_frames=delay_frames,
        start_time_ns=start_time_ns,
        start=True,
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
    source_format: BufferFormat | None
    if isinstance(voice, Stream):
        record = _stream_record(playback, voice)
        identifier = record.identifier
        if config.looping:
            raise ValueError("streaming voices cannot loop")
        previous = record.config
        previous_efx = record.efx
        channels = record.channels
        source_format = record.format
    else:
        identifier = _voice_identifier(playback, voice)
        previous = playback._voice_configs[voice._token]
        previous_efx = playback._voice_efx[voice._token]
        info = _voice_clip_info(playback, voice)
        channels = info.channels
        source_format = info.format if isinstance(info, BufferInfo) else None
    _validate_source_layout(config, channels, source_format)
    _prepare_al(playback)
    if config.stereo_mode is not previous.stereo_mode:
        state = _get_voice_state(playback, identifier, "query voice stereo mode")
        if state in (VoiceState.PLAYING, VoiceState.PAUSED):
            raise InvalidVoiceStateError(
                "stereo_mode cannot change while a voice is playing or paused"
            )
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
            previous=previous,
            changed_only=changed_only,
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
            _apply_voice_config(
                playback,
                identifier,
                previous,
                previous=config,
            )
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
def get_voice_config(playback: Playback, voice: Voice | Stream) -> VoiceConfig:
    """Return the complete managed configuration for a live voice or stream.

    The returned [`VoiceConfig`][pyalsoft.VoiceConfig] is immutable and reflects
    the most recent managed configuration applied to the source.

    Args:
        playback: Session that owns ``voice``.
        voice: Live static voice or stream to inspect.

    Raises:
        InvalidHandleError: The handle is released or belongs to another session.
        PlaybackClosedError: ``playback`` is closed.
    """

    if isinstance(voice, Stream):
        return _stream_record(playback, voice).config
    _voice_identifier(playback, voice)
    return playback._voice_configs[voice._token]


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
def restart(
    playback: Playback,
    voice: Voice,
    *,
    delay_seconds: float = 0.0,
    delay_frames: int | None = None,
    start_time_ns: int | None = None,
) -> None:
    """Rewind a static voice and start it immediately or at a future time.

    Args:
        playback: Session that owns ``voice``.
        voice: Live static voice to restart.
        delay_seconds: Initial silence in source-audio seconds. Pitch and Doppler
            affect its real-time duration.
        delay_frames: Exact number of silent sample frames. When provided,
            ``delay_seconds`` must remain 0.0.
        start_time_ns: Absolute audio-device clock time in nanoseconds. ``None``
            starts as soon as possible.

    Raises:
        TypeError: A timing argument has the wrong type.
        ValueError: A delay or device-clock time is invalid.
        InvalidHandleError: ``voice`` is released or belongs to another session.
        PlaybackClosedError: ``playback`` is closed.
        AudioBackendError: OpenAL cannot rewind or play the voice, or the requested
            timing feature is unavailable.
    """

    identifier = _voice_identifier(playback, voice)
    delay_seconds, delay_frames, start_time_ns = _validate_playback_timing(
        delay_seconds, delay_frames, start_time_ns
    )
    _prepare_al(playback)
    if delay_seconds != 0.0 or delay_frames or start_time_ns is not None:
        _require_source_start_delay(playback, "timed playback")
    playback._library.al.source_rewind(identifier)
    _apply_start_delay(playback, identifier, delay_seconds, delay_frames)
    _start_source(playback, identifier, start_time_ns)
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


def _source_identifier_and_sample_rate(
    playback: Playback, voice: Voice | Stream
) -> tuple[int, int]:
    if isinstance(voice, Stream):
        record = _stream_record(playback, voice)
        return record.identifier, record.sample_rate
    identifier = _voice_identifier(playback, voice)
    clip_token = playback._voice_clips[voice._token]
    return identifier, playback._clip_infos[clip_token].sample_rate


@_serialized_playback
def get_voice_latency(playback: Playback, voice: Voice | Stream) -> VoiceLatency:
    """Atomically query a source offset and its physical-output latency."""

    identifier, sample_rate = _source_identifier_and_sample_rate(playback, voice)
    _prepare_al(playback)
    if not playback._library.is_al_extension_present("AL_SOFT_source_latency"):
        raise AudioBackendError(
            "precise source latency requires the AL_SOFT_source_latency extension"
        )
    values = playback._library.al.get_sourcei64v_soft(
        identifier,
        bindings.AL_SAMPLE_OFFSET_LATENCY_SOFT,
        result_size=2,
    )
    _check_al_error(playback, "query voice latency")
    if len(values) != 2:
        raise AudioBackendError("OpenAL returned an invalid source latency result")
    return VoiceLatency(int(values[0]), int(values[1]), sample_rate)


@_serialized_playback
def get_voice_clock(playback: Playback, voice: Voice | Stream) -> VoiceClock:
    """Atomically query a source offset and the audio-device clock."""

    identifier, sample_rate = _source_identifier_and_sample_rate(playback, voice)
    _prepare_al(playback)
    if not playback._library.is_al_extension_present("AL_SOFT_source_latency"):
        raise AudioBackendError(
            "source clock queries require the AL_SOFT_source_latency extension"
        )
    if not playback._library.alc.is_extension_present(
        playback._device, "ALC_SOFT_device_clock"
    ):
        raise AudioBackendError(
            "source clock queries require the ALC_SOFT_device_clock extension"
        )
    values = playback._library.al.get_sourcei64v_soft(
        identifier,
        bindings.AL_SAMPLE_OFFSET_CLOCK_SOFT,
        result_size=2,
    )
    _check_al_error(playback, "query voice clock")
    if len(values) != 2:
        raise AudioBackendError("OpenAL returned an invalid source clock result")
    return VoiceClock(int(values[0]), int(values[1]), sample_rate)


@_serialized_playback
def get_playback_clock(playback: Playback) -> PlaybackClock:
    """Atomically query the audio-device clock and physical-output latency."""

    _prepare_al(playback)
    if not playback._library.alc.is_extension_present(
        playback._device, "ALC_SOFT_device_clock"
    ):
        raise AudioBackendError(
            "playback clock queries require the ALC_SOFT_device_clock extension"
        )
    _clear_alc_errors(playback._library, playback._device)
    values = playback._library.alc.get_integer64v_soft(
        playback._device,
        bindings.ALC_DEVICE_CLOCK_LATENCY_SOFT,
        2,
    )
    _check_alc_error(
        playback._library,
        playback._device,
        "query playback clock",
    )
    if len(values) != 2:
        raise AudioBackendError("OpenAL returned an invalid playback clock result")
    return PlaybackClock(int(values[0]), int(values[1]))


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
    for identifier in stopped_identifiers + stream_identifiers:
        playback._super_stereo_width_defaults.pop(identifier, None)
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
        owned_slots=tuple(
            identifier
            for resources in released_efx
            for identifier in resources.owned_slots
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


@overload
def release(playback: Playback, resource: EffectBus) -> None: ...


def release(playback: Playback, resource: Clip | Voice | Stream | EffectBus) -> None:
    """Release a clip, voice, stream, or effect bus before its session closes.

    Releasing a voice stops it. Releasing a stream stops it and discards queued
    audio. A clip cannot be released while any live voice still refers to it.
    Every successful release permanently invalidates the handle.

    Args:
        playback: Session that owns ``resource``.
        resource: Live clip, static voice, stream, or effect bus to release.

    Raises:
        TypeError: ``resource`` is not a supported handle.
        InvalidHandleError: The handle is released or belongs to another session.
        ResourceInUseError: ``resource`` is still referenced by another live
            managed resource.
        PlaybackClosedError: ``playback`` is closed.
        AudioBackendError: OpenAL cannot release the native resources.
    """

    _release(playback, resource)


@_serialized_playback
def _release(playback: Playback, resource: Clip | Voice | Stream | EffectBus) -> None:
    if isinstance(resource, EffectBus):
        _release_effect_bus(playback, resource)
        return
    if isinstance(resource, Stream):
        record = _stream_record(playback, resource)
        _prepare_al(playback)
        playback._library.al.source_stop(record.identifier)
        playback._library.al.delete_sources((record.identifier,))
        playback._super_stereo_width_defaults.pop(record.identifier, None)
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
        playback._super_stereo_width_defaults.pop(identifier, None)
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
    raise TypeError("resource must be a Clip, Voice, Stream, or EffectBus")
