"""Convenient sound playback, WAV loading, and the implicit runtime."""

from __future__ import annotations

import atexit
import wave
from collections import OrderedDict
from contextlib import nullcontext, suppress
from dataclasses import dataclass, replace
from os import PathLike
from pathlib import Path
from threading import RLock
from typing import overload

from pyalsoft import bindings
from pyalsoft._managed.models import (
    _DEFAULT_SOUND_CACHE_LIMIT,
    _OMITTED_FILTER,
    PCM,
    Acoustics,
    AudioError,
    AudioFileError,
    AudioPath,
    Clip,
    DistanceModel,
    EffectSend,
    Filter,
    InvalidVoiceStateError,
    Listener,
    SampleType,
    SoundCacheInfo,
    SoundEndReason,
    SoundInfo,
    Vector3,
    Voice,
    VoiceConfig,
    VoiceState,
    VoiceStatus,
    _frame_offset,
    _sound_offset,
    _validate_offsets,
)
from pyalsoft._managed.playback import (
    _DEFAULT_ACOUSTICS,
    _DEFAULT_LISTENER,
    Playback,
    _check_alc_error,
    _clear_alc_errors,
    _create_voice,
    _get_acoustics,
    _get_listener,
    _play_voice,
    _playback_operation,
    _set_acoustics,
    _set_listener,
    _set_voice_config,
    _voice_config_with_overrides,
    close_playback,
    get_voice_status,
    open_playback,
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


@dataclass(slots=True)
class _SoundRecord:
    token: object
    voice: Voice
    clip: Clip | None
    info: SoundInfo
    path: Path | None
    pcm: PCM | None
    config: VoiceConfig
    final_status: VoiceStatus | None = None
    end_reason: SoundEndReason | None = None


@dataclass(frozen=True, slots=True)
class _CachedSoundClip:
    clip: Clip


@dataclass(slots=True, eq=False)
class PlayingSound:
    """One playback instance returned by :func:`play`.

    The default playback runtime owns the native resources, so discarding this
    object does not stop the sound. Its methods are convenient delegates to the
    function-oriented managed API.
    """

    _runtime: _DefaultRuntime
    _record: _SoundRecord

    @property
    def status(self) -> VoiceStatus:
        """Current playback state and offset."""

        return self._runtime.status(self._record)

    @property
    def state(self) -> VoiceState:
        """Current playback state."""

        return self.status.state

    @property
    def playing(self) -> bool:
        """Whether the sound is currently playing."""

        return self.state is VoiceState.PLAYING

    @property
    def paused(self) -> bool:
        """Whether the sound is currently paused."""

        return self.state is VoiceState.PAUSED

    @property
    def stopped(self) -> bool:
        """Whether the sound is no longer playing or resumable."""

        return self.state is VoiceState.STOPPED

    @property
    def done(self) -> bool:
        """Whether the sound has completed naturally or was stopped."""

        return self.stopped

    @property
    def finished(self) -> bool:
        """Whether playback reached the end naturally."""

        return self.end_reason is SoundEndReason.FINISHED

    @property
    def end_reason(self) -> SoundEndReason | None:
        """Why the sound ended, or ``None`` while it remains active."""

        return self._runtime.end_reason(self._record)

    @property
    def offset_seconds(self) -> float:
        """Current playhead position in seconds of source audio."""

        return self.status.offset_seconds

    @property
    def offset_frames(self) -> int:
        """Current playhead position as an exact sample-frame offset."""

        return self.status.offset_frames

    @property
    def info(self) -> SoundInfo:
        """Format and length information for the source audio."""

        return self._record.info

    @property
    def path(self) -> Path:
        """Resolved source path for file-backed audio.

        In-memory PCM audio has no source path, so querying this property for
        such a sound raises :class:`AudioError`.
        """

        if self._record.path is None:
            raise AudioError("in-memory PCM audio has no source path")
        return self._record.path

    @property
    def duration_seconds(self) -> float:
        """Duration of the source audio, unaffected by pitch."""

        return self.info.duration_seconds

    @property
    def frame_count(self) -> int:
        """Number of sample frames in the source audio."""

        return self.info.frame_count

    @property
    def channels(self) -> int:
        """Number of interleaved audio channels."""

        return self.info.channels

    @property
    def sample_rate(self) -> int:
        """Number of sample frames per second."""

        return self.info.sample_rate

    @property
    def sample_type(self) -> SampleType:
        """PCM representation used by each channel sample."""

        return self.info.sample_type

    @property
    def remaining_seconds(self) -> float:
        """Source-audio seconds remaining in the current pass."""

        return max(0.0, self.duration_seconds - self.offset_seconds)

    @property
    def remaining_frames(self) -> int:
        """Sample frames remaining in the current pass."""

        return max(0, self.frame_count - self.offset_frames)

    @property
    def progress(self) -> float:
        """Current playhead position as a value from 0.0 through 1.0."""

        return min(1.0, max(0.0, self.offset_frames / self.frame_count))

    @property
    def config(self) -> VoiceConfig:
        """Current complete voice configuration."""

        return self._runtime.config(self._record)

    @property
    def position(self) -> Vector3:
        """Sound location in 3D space."""

        return self.config.position

    @position.setter
    def position(self, value: Vector3) -> None:
        self.update(position=value)

    @property
    def velocity(self) -> Vector3:
        """Sound velocity used for Doppler shift."""

        return self.config.velocity

    @velocity.setter
    def velocity(self, value: Vector3) -> None:
        self.update(velocity=value)

    @property
    def direction(self) -> Vector3:
        """Direction the sound's attenuation cone points."""

        return self.config.direction

    @direction.setter
    def direction(self, value: Vector3) -> None:
        self.update(direction=value)

    @property
    def gain(self) -> float:
        """Linear pre-attenuation amplitude multiplier."""

        return self.config.gain

    @gain.setter
    def gain(self, value: float) -> None:
        self.update(gain=value)

    @property
    def pitch(self) -> float:
        """Playback-rate and pitch multiplier."""

        return self.config.pitch

    @pitch.setter
    def pitch(self, value: float) -> None:
        self.update(pitch=value)

    @property
    def looping(self) -> bool:
        """Whether the complete sound repeats after reaching its end."""

        return self.config.looping

    @looping.setter
    def looping(self, value: bool) -> None:
        self.update(looping=value)

    @property
    def relative(self) -> bool:
        """Whether coordinates are relative to the listener."""

        return self.config.relative

    @relative.setter
    def relative(self, value: bool) -> None:
        self.update(relative=value)

    @property
    def min_gain(self) -> float:
        """Lower clamp applied after distance and cone attenuation."""

        return self.config.min_gain

    @min_gain.setter
    def min_gain(self, value: float) -> None:
        self.update(min_gain=value)

    @property
    def max_gain(self) -> float:
        """Upper clamp applied after distance and cone attenuation."""

        return self.config.max_gain

    @max_gain.setter
    def max_gain(self, value: float) -> None:
        self.update(max_gain=value)

    @property
    def reference_distance(self) -> float:
        """Reference point where distance attenuation has unity gain."""

        return self.config.reference_distance

    @reference_distance.setter
    def reference_distance(self, value: float) -> None:
        self.update(reference_distance=value)

    @property
    def max_distance(self) -> float:
        """Distance used as the outer bound by clamped distance models."""

        return self.config.max_distance

    @max_distance.setter
    def max_distance(self, value: float) -> None:
        self.update(max_distance=value)

    @property
    def rolloff_factor(self) -> float:
        """Multiplier controlling how rapidly distance attenuation changes."""

        return self.config.rolloff_factor

    @rolloff_factor.setter
    def rolloff_factor(self, value: float) -> None:
        self.update(rolloff_factor=value)

    @property
    def cone_inner_angle(self) -> float:
        """Full angle in which a directional sound is unattenuated."""

        return self.config.cone_inner_angle

    @cone_inner_angle.setter
    def cone_inner_angle(self, value: float) -> None:
        self.update(cone_inner_angle=value)

    @property
    def cone_outer_angle(self) -> float:
        """Full angle beyond which cone_outer_gain is applied."""

        return self.config.cone_outer_angle

    @cone_outer_angle.setter
    def cone_outer_angle(self, value: float) -> None:
        self.update(cone_outer_angle=value)

    @property
    def cone_outer_gain(self) -> float:
        """Gain applied outside a directional sound's outer cone."""

        return self.config.cone_outer_gain

    @cone_outer_gain.setter
    def cone_outer_gain(self, value: float) -> None:
        self.update(cone_outer_gain=value)

    @property
    def filter(self) -> Filter | None:
        """Direct EFX filter applied to the sound's dry signal."""

        return self.config.filter

    @filter.setter
    def filter(self, value: Filter | None) -> None:
        self.set_config(replace(self.config, filter=value))

    @property
    def effect_sends(self) -> tuple[EffectSend, ...]:
        """Ordered auxiliary EFX routes applied to this sound."""

        return self.config.effect_sends

    @effect_sends.setter
    def effect_sends(self, value: tuple[EffectSend, ...] | list[EffectSend]) -> None:
        self.set_config(replace(self.config, effect_sends=tuple(value)))

    def pause(self) -> None:
        """Pause the sound if it is currently playing."""

        self._runtime.pause(self._record)

    def resume(self) -> None:
        """Resume the sound if it is paused."""

        self._runtime.resume(self._record)

    def stop(self) -> None:
        """Stop the sound and release its playback voice."""

        self._runtime.stop(self._record)

    def seek(self, offset_seconds: float) -> None:
        """Move the playhead to an offset in source-audio seconds."""

        self._runtime.seek(self._record, offset_seconds)

    def seek_frames(self, offset_frames: int) -> None:
        """Move the playhead to an exact sample-frame offset."""

        self._runtime.seek_frames(self._record, offset_frames)

    def rewind(self) -> None:
        """Move the playhead to the beginning and enter the initial state."""

        self._runtime.rewind(self._record)

    def restart(self) -> None:
        """Start the sound again from its beginning."""

        self._runtime.restart(self._record)

    def set_config(self, config: VoiceConfig) -> None:
        """Apply a complete immutable voice configuration."""

        self._runtime.set_config(self._record, config)

    def update(
        self,
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
    ) -> None:
        """Validate and apply a batch of partial source-control changes."""

        self._runtime.update(
            self._record,
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

    def __repr__(self) -> str:
        return "PlayingSound(<opaque>)"


def _wave_info(source: wave.Wave_read, path: Path) -> SoundInfo:
    """Validate a WAV header and return its supported PCM layout."""

    if source.getcomptype() != "NONE":
        raise AudioFileError(f"unsupported compressed WAV file: {path}")
    sample_width = source.getsampwidth()
    try:
        sample_type = {
            1: SampleType.UINT8,
            2: SampleType.INT16,
        }[sample_width]
    except KeyError as error:
        raise AudioFileError(
            f"unsupported {sample_width * 8}-bit WAV file: {path}"
        ) from error

    try:
        return SoundInfo(
            channels=source.getnchannels(),
            sample_rate=source.getframerate(),
            sample_type=sample_type,
            frame_count=source.getnframes(),
        )
    except (TypeError, ValueError) as error:
        raise AudioFileError(f"unsupported WAV file {path}: {error}") from error


def _read_wave(path: Path) -> PCM:
    try:
        with wave.open(str(path), "rb") as source:
            info = _wave_info(source, path)
            samples = source.readframes(info.frame_count)
    except (EOFError, OSError, wave.Error) as error:
        raise AudioFileError(f"could not read WAV file {path}: {error}") from error

    if len(samples) != info.byte_count:
        raise AudioFileError(
            f"truncated WAV file {path}: expected {info.byte_count} sample bytes, "
            f"read {len(samples)}"
        )

    try:
        return PCM(
            samples=samples,
            channels=info.channels,
            sample_rate=info.sample_rate,
            sample_type=info.sample_type,
        )
    except (TypeError, ValueError) as error:
        raise AudioFileError(f"unsupported WAV file {path}: {error}") from error


def get_sound_info(path: AudioPath) -> SoundInfo:
    """Read WAV format and length information without opening an audio device."""

    if not isinstance(path, (str, PathLike)):
        raise TypeError("sound must be a path to a WAV file")
    normalized = Path(path).expanduser().resolve()
    try:
        with wave.open(str(normalized), "rb") as source:
            return _wave_info(source, normalized)
    except (EOFError, OSError, wave.Error) as error:
        raise AudioFileError(
            f"could not read WAV file {normalized}: {error}"
        ) from error


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
        self._clips: OrderedDict[Path, _CachedSoundClip] = OrderedDict()
        self._active: dict[object, _SoundRecord] = {}
        self._cache_limit: int | None = _DEFAULT_SOUND_CACHE_LIMIT
        self._cache_bytes = 0
        self._pending_evictions: set[Path] = set()
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

    def _active_cache_paths(self) -> set[Path]:
        return {
            record.path
            for record in self._active.values()
            if record.path is not None and record.path in self._clips
        }

    def _evict_cached_path(self, path: Path, active_paths: set[Path]) -> bool:
        cached = self._clips.get(path)
        if cached is None:
            self._pending_evictions.discard(path)
            return False
        if path in active_paths:
            self._pending_evictions.add(path)
            return False
        release(self._opened_playback(), cached.clip)
        del self._clips[path]
        self._cache_bytes -= cached.clip.info.byte_count
        self._pending_evictions.discard(path)
        return True

    def _trim_cache(self, *, protected: Path | None = None) -> None:
        active_paths = self._active_cache_paths()
        if protected is not None:
            active_paths.add(protected)
        for path in tuple(self._pending_evictions):
            self._evict_cached_path(path, active_paths)
        while self._cache_limit is not None and self._cache_bytes > self._cache_limit:
            candidate = next(
                (path for path in self._clips if path not in active_paths),
                None,
            )
            if candidate is None:
                return
            self._evict_cached_path(candidate, active_paths)

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

    def _create_replacement_voice(
        self,
        record: _SoundRecord,
        *,
        offset_seconds: float = 0.0,
        offset_frames: int | None = None,
        start: bool,
    ) -> Voice:
        playback = self._opened_playback()
        clip = record.clip
        uploaded = False
        if clip is None:
            assert record.pcm is not None
            clip = upload(playback, record.pcm)
            uploaded = True
        try:
            voice = _create_voice(
                playback,
                clip,
                record.config,
                offset_seconds=offset_seconds,
                offset_frames=offset_frames,
                start=start,
            )
        except BaseException:
            if uploaded:
                with suppress(Exception):
                    release(playback, clip)
            raise
        record.clip = clip
        return voice

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
    ) -> PlayingSound:
        if not isinstance(config, VoiceConfig):
            raise TypeError("config must be a VoiceConfig")
        normalized = (
            None if isinstance(sound, PCM) else Path(sound).expanduser().resolve()
        )
        with self._lock:
            self._require_open()
            self._reap_finished()
            if isinstance(sound, PCM):
                pcm = sound
                offset_seconds, offset_frames = _validate_offsets(
                    pcm.info, offset_seconds, offset_frames
                )
                clip = upload(self._ensure_playback(), pcm)
            else:
                assert normalized is not None
                cached = self._clips.get(normalized)
                if cached is None:
                    pcm = _read_wave(normalized)
                    offset_seconds, offset_frames = _validate_offsets(
                        pcm.info, offset_seconds, offset_frames
                    )
                    cached = _CachedSoundClip(
                        clip=upload(self._ensure_playback(), pcm),
                    )
                    self._clips[normalized] = cached
                    self._cache_bytes += cached.clip.info.byte_count
                else:
                    offset_seconds, offset_frames = _validate_offsets(
                        cached.clip.info, offset_seconds, offset_frames
                    )
                self._clips.move_to_end(normalized)
                self._trim_cache(protected=normalized)
                clip = cached.clip
            try:
                voice = _play_voice(
                    self._opened_playback(),
                    clip,
                    config,
                    offset_seconds=offset_seconds,
                    offset_frames=offset_frames,
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
                info=clip.info,
                path=normalized,
                pcm=sound if isinstance(sound, PCM) else None,
                config=config,
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
            active_paths = self._active_cache_paths()
            if path is not None:
                return int(self._evict_cached_path(path, active_paths))
            evicted = 0
            for cached_path in tuple(self._clips):
                evicted += self._evict_cached_path(cached_path, active_paths)
            return evicted

    def cache_info(self) -> SoundCacheInfo:
        with self._lock:
            self._require_open()
            self._reap_finished()
            active_paths = self._active_cache_paths()
            return SoundCacheInfo(
                max_bytes=self._cache_limit,
                current_bytes=self._cache_bytes,
                clip_count=len(self._clips),
                active_clip_count=len(active_paths),
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
                record.voice = self._create_replacement_voice(
                    record,
                    offset_seconds=offset_seconds,
                    start=False,
                )
                record.final_status = None
                record.end_reason = None
                self._active[record.token] = record
                return
            seek(self._opened_playback(), record.voice, offset_seconds)

    def seek_frames(self, record: _SoundRecord, offset_frames: int) -> None:
        offset_frames = _frame_offset(offset_frames, record.info.frame_count)
        with self._lock:
            self._require_open()
            status = self._status(record)
            if status.state is VoiceState.STOPPED:
                record.voice = self._create_replacement_voice(
                    record,
                    offset_frames=offset_frames,
                    start=False,
                )
                record.final_status = None
                record.end_reason = None
                self._active[record.token] = record
                return
            seek_frames(self._opened_playback(), record.voice, offset_frames)

    def rewind(self, record: _SoundRecord) -> None:
        with self._lock:
            self._require_open()
            status = self._status(record)
            if status.state is VoiceState.STOPPED:
                record.voice = self._create_replacement_voice(
                    record,
                    start=False,
                )
                record.final_status = None
                record.end_reason = None
                self._active[record.token] = record
                return
            rewind(self._opened_playback(), record.voice)
            record.end_reason = None

    def restart(self, record: _SoundRecord) -> None:
        with self._lock:
            self._require_open()
            status = self._status(record)
            if status.state is VoiceState.STOPPED:
                record.voice = self._create_replacement_voice(
                    record,
                    start=True,
                )
                record.final_status = None
                record.end_reason = None
                self._active[record.token] = record
                return
            restart(self._opened_playback(), record.voice)
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
                filter=filter,
                effect_sends=effect_sends,
            )
            status = self._status(record)
            if status.state is VoiceState.STOPPED:
                if record.end_reason is SoundEndReason.SHUTDOWN:
                    self._require_open()
                record.config = updated
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


_default_runtime: _DefaultRuntime | None = None
_default_lock = RLock()


def _get_default_runtime() -> _DefaultRuntime:
    global _default_runtime
    with _default_lock:
        if _default_runtime is None:
            _default_runtime = _DefaultRuntime()
        return _default_runtime


def set_sound_cache_limit(max_bytes: int | None) -> None:
    """Set the implicit file cache byte budget, or ``None`` for no limit."""

    if max_bytes is not None:
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int):
            raise TypeError("max_bytes must be an integer or None")
        if max_bytes < 0:
            raise ValueError("max_bytes cannot be negative")
    _get_default_runtime().set_cache_limit(max_bytes)


def clear_sound_cache(path: AudioPath | None = None) -> int:
    """Evict cached file clips, deferring active entries until they finish."""

    normalized: Path | None = None
    if path is not None:
        if not isinstance(path, (str, PathLike)):
            raise TypeError("path must be a path to a WAV file or None")
        normalized = Path(path).expanduser().resolve()
    return _get_default_runtime().clear_cache(normalized)


def get_sound_cache_info() -> SoundCacheInfo:
    """Return byte usage and activity for the implicit file cache."""

    return _get_default_runtime().cache_info()


@overload
def set_listener(playback: Playback, listener: Listener) -> None: ...


@overload
def set_listener(listener: Listener, /) -> None: ...


def set_listener(
    playback: Playback | Listener, listener: Listener | None = None
) -> None:
    """Set the listener for an explicit session or the convenience runtime."""

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
    """Return the listener for an explicit session or the convenience runtime."""

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
    """Apply a validated batch of partial listener changes and return it."""

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
    """Set acoustics for an explicit session or the convenience runtime."""

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
    """Return acoustics for an explicit session or the convenience runtime."""

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
    """Apply a validated batch of partial acoustic changes and return it."""

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
) -> Voice | PlayingSound:
    """Play an explicit clip, WAV file, or PCM value.

    ``play(playback, clip, config)`` preserves the explicit managed API.
    ``play(sound, config=config)`` starts asynchronous, fire-and-forget playback
    through the default runtime and returns an optional control handle. *sound*
    may be a WAV path or an in-memory :class:`PCM` value. Individual keyword
    controls override the corresponding values in ``config``.
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
    )


def shutdown() -> None:
    """Close and forget the default playback runtime, if it was opened."""

    global _default_runtime
    with _default_lock:
        runtime, _default_runtime = _default_runtime, None
        if runtime is not None:
            runtime.close()


def _shutdown_at_exit() -> None:
    with suppress(Exception):
        shutdown()


atexit.register(_shutdown_at_exit)
