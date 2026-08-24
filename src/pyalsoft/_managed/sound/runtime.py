"""Implicit playback runtime, active-sound ownership, and WAV clip cache."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from threading import RLock

from pyalsoft import bindings
from pyalsoft._managed._backend import _check_alc_error, _clear_alc_errors
from pyalsoft._managed.audio import PCM, AudioPath, _as_stereo
from pyalsoft._managed.effects import _OMITTED_FILTER, EffectSend, Filter
from pyalsoft._managed.errors import InvalidVoiceStateError
from pyalsoft._managed.playback.session import (
    Playback,
    _set_acoustics,
    _set_listener,
    close_playback,
    defer_updates,
    open_playback,
)
from pyalsoft._managed.playback.source_controls import _validate_playback_timing
from pyalsoft._managed.playback.voices import (
    _create_voice,
    _play_voice,
    _set_voice_config,
    _voice_config_with_overrides,
    get_voice_status,
    pause,
    release,
    restart,
    resume,
    rewind,
    seek,
    seek_frames,
    set_voice_config,
    stop,
    upload,
)
from pyalsoft._managed.resources import (
    Clip,
    SoundCacheInfo,
    SoundEndReason,
    Voice,
    VoiceState,
    VoiceStatus,
)
from pyalsoft._managed.sound.handle import (
    PlayingSound,
    _CachedSoundClip,
    _SoundRecord,
)
from pyalsoft._managed.sound.wave import _read_wave
from pyalsoft._managed.spatial import (
    _DEFAULT_ACOUSTICS,
    _DEFAULT_LISTENER,
    _OMITTED_DISTANCE_MODEL,
    _OMITTED_RESAMPLER,
    _OMITTED_STEREO_ANGLES,
    _OMITTED_SUPER_STEREO_WIDTH,
    Acoustics,
    DirectChannelsMode,
    DistanceModel,
    Listener,
    Resampler,
    SpatializationMode,
    StereoMode,
    Vector3,
    VoiceConfig,
    _frame_offset,
    _sound_offset,
    _validate_offsets,
)

_DEFAULT_SOUND_CACHE_LIMIT = 64 * 1024 * 1024


def _uses_direct_channels(config: VoiceConfig) -> bool:
    return config.direct_channels is not DirectChannelsMode.OFF


class _DefaultRuntime:
    """Own the implicit session, cached clips, and active playback voices."""

    __slots__ = (
        "_acoustics",
        "_active",
        "_cache_bytes",
        "_cache_limit",
        "_clips",
        "_closed",
        "_listener",
        "_lock",
        "_pending_evictions",
        "_playback",
    )

    def __init__(self) -> None:
        self._playback: Playback | None = None
        self._clips: OrderedDict[tuple[Path, bool], _CachedSoundClip] = OrderedDict()
        self._active: dict[object, _SoundRecord] = {}
        self._cache_limit: int | None = _DEFAULT_SOUND_CACHE_LIMIT
        self._cache_bytes = 0
        self._pending_evictions: set[tuple[Path, bool]] = set()
        self._listener = _DEFAULT_LISTENER
        self._acoustics = _DEFAULT_ACOUSTICS
        self._closed = False
        self._lock = RLock()

    def _require_open(self) -> None:
        if self._closed:
            raise InvalidVoiceStateError("the sound's playback runtime is closed")

    def _ensure_playback(self) -> Playback:
        self._require_open()
        if self._playback is None:
            self._playback = open_playback()
        return self._playback

    def _opened_playback(self) -> Playback:
        self._require_open()
        if self._playback is None:
            raise InvalidVoiceStateError("the sound was not started")
        return self._playback

    def _active_cache_keys(self) -> set[tuple[Path, bool]]:
        return {
            record.cache_key
            for record in self._active.values()
            if record.cache_key is not None and record.cache_key in self._clips
        }

    def _evict_cached_clip(
        self,
        key: tuple[Path, bool],
        active_keys: set[tuple[Path, bool]],
    ) -> bool:
        cached = self._clips.get(key)
        if cached is None:
            self._pending_evictions.discard(key)
            return False
        if key in active_keys:
            self._pending_evictions.add(key)
            return False
        release(self._opened_playback(), cached.clip)
        del self._clips[key]
        self._cache_bytes -= cached.clip.info.byte_count
        self._pending_evictions.discard(key)
        return True

    def _trim_cache(self, *, protected: tuple[Path, bool] | None = None) -> None:
        active_keys = self._active_cache_keys()
        if protected is not None:
            active_keys.add(protected)
        for key in tuple(self._pending_evictions):
            self._evict_cached_clip(key, active_keys)
        while self._cache_limit is not None and self._cache_bytes > self._cache_limit:
            candidate = next(
                (key for key in self._clips if key not in active_keys),
                None,
            )
            if candidate is None:
                return
            self._evict_cached_clip(candidate, active_keys)

    def _finalize(
        self,
        record: _SoundRecord,
        status: VoiceStatus,
        *,
        end_reason: SoundEndReason,
    ) -> None:
        playback = self._opened_playback()
        release(playback, record.voice)
        try:
            if record.pcm is not None:
                assert record.clip is not None
                release(playback, record.clip)
        finally:
            if record.pcm is not None:
                record.clip = None
            record.final_status = status
            record.end_reason = end_reason
            del self._active[record.token]
            self._trim_cache()

    def _replacement_clip(
        self,
        record: _SoundRecord,
        config: VoiceConfig,
    ) -> tuple[Clip, tuple[Path, bool] | None, bool]:
        playback = self._opened_playback()
        direct_channels = _uses_direct_channels(config)
        if record.pcm is not None:
            pcm = _as_stereo(record.pcm) if direct_channels else record.pcm
            return upload(playback, pcm), None, True

        assert record.path is not None
        cache_key = (record.path, direct_channels)
        cached = self._clips.get(cache_key)
        if cached is None:
            source_pcm = _read_wave(record.path)
            pcm = _as_stereo(source_pcm) if direct_channels else source_pcm
            cached = _CachedSoundClip(
                clip=upload(playback, pcm),
                info=source_pcm.info,
            )
            self._clips[cache_key] = cached
            self._cache_bytes += cached.clip.info.byte_count
        self._clips.move_to_end(cache_key)
        self._trim_cache(protected=cache_key)
        return cached.clip, cache_key, False

    def _create_replacement_voice(
        self,
        record: _SoundRecord,
        *,
        offset_seconds: float = 0.0,
        offset_frames: int | None = None,
        delay_seconds: float = 0.0,
        delay_frames: int | None = None,
        start_time_ns: int | None = None,
        start: bool,
    ) -> Voice:
        playback = self._opened_playback()
        clip, cache_key, owned = self._replacement_clip(record, record.config)
        try:
            voice = _create_voice(
                playback,
                clip,
                record.config,
                offset_seconds=offset_seconds,
                offset_frames=offset_frames,
                delay_seconds=delay_seconds,
                delay_frames=delay_frames,
                start_time_ns=start_time_ns,
                start=start,
            )
        except BaseException:
            if owned:
                with suppress(Exception):
                    release(playback, clip)
            else:
                with suppress(Exception):
                    self._trim_cache()
            raise
        record.clip = clip
        record.cache_key = cache_key
        return voice

    def _replace_active_voice_config(
        self,
        record: _SoundRecord,
        status: VoiceStatus,
        config: VoiceConfig,
    ) -> None:
        playback = self._opened_playback()
        old_voice = record.voice
        old_clip = record.clip
        clip, cache_key, owned = self._replacement_clip(record, config)
        try:
            voice = _create_voice(
                playback,
                clip,
                config,
                offset_frames=status.offset_frames,
                start=status.state in (VoiceState.PLAYING, VoiceState.PAUSED),
            )
            if status.state is VoiceState.PAUSED:
                pause(playback, voice)
        except BaseException:
            if owned:
                with suppress(Exception):
                    release(playback, clip)
            else:
                with suppress(Exception):
                    self._trim_cache()
            raise

        try:
            release(playback, old_voice)
            if record.pcm is not None and old_clip is not None:
                release(playback, old_clip)
        except BaseException:
            with suppress(Exception):
                release(playback, voice)
            if owned:
                with suppress(Exception):
                    release(playback, clip)
            else:
                with suppress(Exception):
                    self._trim_cache()
            raise

        record.voice = voice
        record.clip = clip
        record.config = config
        record.cache_key = cache_key
        self._trim_cache()

    def _replace_terminal_voice(
        self,
        record: _SoundRecord,
        *,
        offset_seconds: float = 0.0,
        offset_frames: int | None = None,
        delay_seconds: float = 0.0,
        delay_frames: int | None = None,
        start_time_ns: int | None = None,
        start: bool,
    ) -> None:
        record.voice = self._create_replacement_voice(
            record,
            offset_seconds=offset_seconds,
            offset_frames=offset_frames,
            delay_seconds=delay_seconds,
            delay_frames=delay_frames,
            start_time_ns=start_time_ns,
            start=start,
        )
        record.final_status = None
        record.end_reason = None
        self._active[record.token] = record

    def _device_disconnected(self) -> bool:
        playback = self._opened_playback()
        library = playback._library
        if not library.alc.is_extension_present(playback._device, "ALC_EXT_disconnect"):
            return False
        _clear_alc_errors(library, playback._device)
        connected = library.alc.get_integerv(
            playback._device, bindings.ALC_CONNECTED, 1
        )[0]
        _check_alc_error(library, playback._device, "query playback device connection")
        return not bool(connected)

    def _status(self, record: _SoundRecord) -> VoiceStatus:
        if record.final_status is not None:
            return record.final_status
        self._require_open()
        status = get_voice_status(self._opened_playback(), record.voice)
        if status.state is VoiceState.STOPPED:
            if self._device_disconnected():
                end_reason = SoundEndReason.DEVICE_LOST
            else:
                end_reason = SoundEndReason.FINISHED
                status = VoiceStatus(
                    state=VoiceState.STOPPED,
                    offset_seconds=record.info.duration_seconds,
                    offset_frames=record.info.frame_count,
                )
            self._finalize(record, status, end_reason=end_reason)
        return status

    def _reap_finished(self) -> None:
        for record in tuple(self._active.values()):
            self._status(record)

    def play(
        self,
        sound: AudioPath | PCM,
        config: VoiceConfig,
        *,
        offset_seconds: float = 0.0,
        offset_frames: int | None = None,
        delay_seconds: float = 0.0,
        delay_frames: int | None = None,
        start_time_ns: int | None = None,
    ) -> PlayingSound:
        if not isinstance(config, VoiceConfig):
            raise TypeError("config must be a VoiceConfig")
        delay_seconds, delay_frames, start_time_ns = _validate_playback_timing(
            delay_seconds, delay_frames, start_time_ns
        )
        direct_channels = _uses_direct_channels(config)
        normalized = (
            None if isinstance(sound, PCM) else Path(sound).expanduser().resolve()
        )
        with self._lock:
            self._require_open()
            self._reap_finished()
            if isinstance(sound, PCM):
                source_info = sound.info
                offset_seconds, offset_frames = _validate_offsets(
                    source_info, offset_seconds, offset_frames
                )
                pcm = _as_stereo(sound) if direct_channels else sound
                clip = upload(self._ensure_playback(), pcm)
                cache_key = None
            else:
                assert normalized is not None
                cache_key = (normalized, direct_channels)
                cached = self._clips.get(cache_key)
                if cached is None:
                    source_pcm = _read_wave(normalized)
                    source_info = source_pcm.info
                    offset_seconds, offset_frames = _validate_offsets(
                        source_info, offset_seconds, offset_frames
                    )
                    pcm = _as_stereo(source_pcm) if direct_channels else source_pcm
                    cached = _CachedSoundClip(
                        clip=upload(self._ensure_playback(), pcm),
                        info=source_info,
                    )
                    self._clips[cache_key] = cached
                    self._cache_bytes += cached.clip.info.byte_count
                else:
                    source_info = cached.info
                    offset_seconds, offset_frames = _validate_offsets(
                        source_info, offset_seconds, offset_frames
                    )
                self._clips.move_to_end(cache_key)
                self._trim_cache(protected=cache_key)
                clip = cached.clip
            try:
                voice = _play_voice(
                    self._opened_playback(),
                    clip,
                    config,
                    offset_seconds=offset_seconds,
                    offset_frames=offset_frames,
                    delay_seconds=delay_seconds,
                    delay_frames=delay_frames,
                    start_time_ns=start_time_ns,
                )
            except BaseException:
                if isinstance(sound, PCM):
                    with suppress(Exception):
                        release(self._opened_playback(), clip)
                else:
                    with suppress(Exception):
                        self._trim_cache()
                raise
            token = object()
            record = _SoundRecord(
                token=token,
                voice=voice,
                clip=clip,
                info=source_info,
                path=normalized,
                pcm=sound if isinstance(sound, PCM) else None,
                config=config,
                cache_key=cache_key,
            )
            self._active[token] = record
            return PlayingSound(self, record)

    def set_cache_limit(self, max_bytes: int | None) -> None:
        with self._lock:
            self._require_open()
            self._cache_limit = max_bytes
            self._reap_finished()
            self._trim_cache()

    def clear_cache(self, path: Path | None) -> int:
        with self._lock:
            self._require_open()
            self._reap_finished()
            active_keys = self._active_cache_keys()
            if path is not None:
                return sum(
                    self._evict_cached_clip(key, active_keys)
                    for key in tuple(self._clips)
                    if key[0] == path
                )
            evicted = 0
            for key in tuple(self._clips):
                evicted += self._evict_cached_clip(key, active_keys)
            return evicted

    def cache_info(self) -> SoundCacheInfo:
        with self._lock:
            self._require_open()
            self._reap_finished()
            active_keys = self._active_cache_keys()
            return SoundCacheInfo(
                max_bytes=self._cache_limit,
                current_bytes=self._cache_bytes,
                clip_count=len(self._clips),
                active_clip_count=len(active_keys),
                pending_eviction_count=len(self._pending_evictions),
            )

    def status(self, record: _SoundRecord) -> VoiceStatus:
        with self._lock:
            return self._status(record)

    def end_reason(self, record: _SoundRecord) -> SoundEndReason | None:
        with self._lock:
            self._status(record)
            return record.end_reason

    def config(self, record: _SoundRecord) -> VoiceConfig:
        with self._lock:
            return record.config

    def pause(self, record: _SoundRecord) -> None:
        with self._lock:
            status = self._status(record)
            if status.state is VoiceState.PLAYING:
                pause(self._opened_playback(), record.voice)

    def resume(self, record: _SoundRecord) -> None:
        with self._lock:
            status = self._status(record)
            if status.state is not VoiceState.PAUSED:
                raise InvalidVoiceStateError(
                    f"cannot resume a sound in the {status.state.value} state"
                )
            resume(self._opened_playback(), record.voice)

    def stop(self, record: _SoundRecord) -> None:
        with self._lock:
            if record.final_status is not None:
                return
            status = self._status(record)
            if status.state is VoiceState.STOPPED:
                return
            stop(self._opened_playback(), record.voice)
            self._finalize(
                record,
                VoiceStatus(
                    state=VoiceState.STOPPED,
                    offset_seconds=status.offset_seconds,
                    offset_frames=status.offset_frames,
                ),
                end_reason=SoundEndReason.STOPPED,
            )

    def seek(self, record: _SoundRecord, offset_seconds: float) -> None:
        offset_seconds = _sound_offset(offset_seconds, record.info.duration_seconds)
        with self._lock:
            self._require_open()
            status = self._status(record)
            if status.state is VoiceState.STOPPED:
                self._replace_terminal_voice(
                    record,
                    offset_seconds=offset_seconds,
                    start=False,
                )
                return
            seek(self._opened_playback(), record.voice, offset_seconds)

    def seek_frames(self, record: _SoundRecord, offset_frames: int) -> None:
        offset_frames = _frame_offset(offset_frames, record.info.frame_count)
        with self._lock:
            self._require_open()
            status = self._status(record)
            if status.state is VoiceState.STOPPED:
                self._replace_terminal_voice(
                    record,
                    offset_frames=offset_frames,
                    start=False,
                )
                return
            seek_frames(self._opened_playback(), record.voice, offset_frames)

    def rewind(self, record: _SoundRecord) -> None:
        with self._lock:
            self._require_open()
            status = self._status(record)
            if status.state is VoiceState.STOPPED:
                self._replace_terminal_voice(record, start=False)
                return
            rewind(self._opened_playback(), record.voice)
            record.end_reason = None

    def restart(
        self,
        record: _SoundRecord,
        *,
        delay_seconds: float = 0.0,
        delay_frames: int | None = None,
        start_time_ns: int | None = None,
    ) -> None:
        with self._lock:
            self._require_open()
            delay_seconds, delay_frames, start_time_ns = _validate_playback_timing(
                delay_seconds, delay_frames, start_time_ns
            )
            status = self._status(record)
            if status.state is VoiceState.STOPPED:
                self._replace_terminal_voice(
                    record,
                    delay_seconds=delay_seconds,
                    delay_frames=delay_frames,
                    start_time_ns=start_time_ns,
                    start=True,
                )
                return
            restart(
                self._opened_playback(),
                record.voice,
                delay_seconds=delay_seconds,
                delay_frames=delay_frames,
                start_time_ns=start_time_ns,
            )
            record.end_reason = None

    def set_config(self, record: _SoundRecord, config: VoiceConfig) -> None:
        if not isinstance(config, VoiceConfig):
            raise TypeError("config must be a VoiceConfig")
        with self._lock:
            status = self._status(record)
            if status.state is VoiceState.STOPPED:
                if record.end_reason is SoundEndReason.SHUTDOWN:
                    self._require_open()
                record.config = config
                return
            layout_changes = record.info.channels == 1 and _uses_direct_channels(
                record.config
            ) != _uses_direct_channels(config)
            if layout_changes:
                self._replace_active_voice_config(record, status, config)
                return
            set_voice_config(self._opened_playback(), record.voice, config)
            record.config = config

    def update(
        self,
        record: _SoundRecord,
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
    ) -> None:
        with self._lock:
            current = record.config
            updated = _voice_config_with_overrides(
                current,
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
                cone_outer_gain_high_frequency=cone_outer_gain_high_frequency,
                distance_model=distance_model,
                radius=radius,
                spatialization=spatialization,
                direct_channels=direct_channels,
                stereo_angles=stereo_angles,
                resampler=resampler,
                air_absorption_factor=air_absorption_factor,
                room_rolloff_factor=room_rolloff_factor,
                direct_filter_gain_high_frequency_auto=(
                    direct_filter_gain_high_frequency_auto
                ),
                auxiliary_send_filter_gain_auto=auxiliary_send_filter_gain_auto,
                auxiliary_send_filter_gain_high_frequency_auto=(
                    auxiliary_send_filter_gain_high_frequency_auto
                ),
                stereo_mode=stereo_mode,
                super_stereo_width=super_stereo_width,
                filter=filter,
                effect_sends=effect_sends,
            )
            status = self._status(record)
            if status.state is VoiceState.STOPPED:
                if record.end_reason is SoundEndReason.SHUTDOWN:
                    self._require_open()
                record.config = updated
                return
            layout_changes = record.info.channels == 1 and _uses_direct_channels(
                record.config
            ) != _uses_direct_channels(updated)
            if layout_changes:
                self._replace_active_voice_config(record, status, updated)
                return
            _set_voice_config(
                self._opened_playback(),
                record.voice,
                updated,
                changed_only=True,
            )
            record.config = updated

    def listener(self) -> Listener:
        with self._lock:
            return self._listener

    def set_listener(self, listener: Listener) -> None:
        with self._lock:
            _set_listener(self._ensure_playback(), listener)
            self._listener = listener

    def acoustics(self) -> Acoustics:
        with self._lock:
            return self._acoustics

    def set_acoustics(self, acoustics: Acoustics) -> None:
        with self._lock:
            _set_acoustics(self._ensure_playback(), acoustics)
            self._acoustics = acoustics

    @contextmanager
    def defer_updates(self) -> Iterator[None]:
        """Defer OpenAL updates for the runtime's current playback."""

        with self._lock:
            playback = self._ensure_playback()
        with defer_updates(playback):
            yield

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            statuses: dict[object, VoiceStatus] = {}
            for token, record in tuple(self._active.items()):
                try:
                    status = self._status(record)
                except Exception:
                    statuses[token] = VoiceStatus(
                        state=VoiceState.STOPPED,
                        offset_seconds=0.0,
                        offset_frames=0,
                    )
                else:
                    if token in self._active:
                        statuses[token] = VoiceStatus(
                            state=VoiceState.STOPPED,
                            offset_seconds=status.offset_seconds,
                            offset_frames=status.offset_frames,
                        )
            try:
                if self._playback is not None:
                    close_playback(self._playback)
            finally:
                for token, record in self._active.items():
                    record.final_status = statuses[token]
                    record.end_reason = SoundEndReason.SHUTDOWN
                self._active.clear()
                self._clips.clear()
                self._cache_bytes = 0
                self._pending_evictions.clear()
                self._playback = None
                self._closed = True
