"""High-level handle for one convenience playback instance."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from pyalsoft._managed.audio import PCM, SampleType, SoundInfo
from pyalsoft._managed.effects import _OMITTED_FILTER, EffectSend, Filter
from pyalsoft._managed.errors import AudioError
from pyalsoft._managed.resources import (
    Clip,
    SoundEndReason,
    Voice,
    VoiceState,
    VoiceStatus,
)
from pyalsoft._managed.spatial import (
    Vector3,
    VoiceConfig,
)

if TYPE_CHECKING:
    from pyalsoft._managed.sound.runtime import _DefaultRuntime


@dataclass(slots=True)
class _SoundRecord:
    token: object
    voice: Voice
    clip: Clip | None
    info: SoundInfo
    path: Path | None
    pcm: PCM | None
    config: VoiceConfig
    spatialize: bool | None = None
    direct_channels: bool = False
    cache_key: tuple[Path, bool] | None = None
    final_status: VoiceStatus | None = None
    end_reason: SoundEndReason | None = None


@dataclass(frozen=True, slots=True)
class _CachedSoundClip:
    clip: Clip
    info: SoundInfo


@dataclass(slots=True, eq=False)
class PlayingSound:
    """One playback instance returned by [`play`][pyalsoft.play].

    The default playback runtime owns the native resources, so discarding this
    object does not stop the sound. Its methods are convenient delegates to the
    function-oriented managed API. Handles retain their final status after
    natural completion, an explicit stop, device loss, or runtime shutdown.

    Do not construct instances directly. Use [`play`][pyalsoft.play] with a WAV
    path or [`PCM`][pyalsoft.PCM] value.
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
        such a sound raises [`AudioError`][pyalsoft.AudioError].

        Raises:
            AudioError: This sound was created from in-memory PCM rather than a
                file.
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
        """Pause the sound if it is currently playing.

        Calling this when the sound is not currently playing is harmless.
        """

        self._runtime.pause(self._record)

    def resume(self) -> None:
        """Resume the sound if it is paused.

        Raises:
            InvalidVoiceStateError: The sound is not paused.
        """

        self._runtime.resume(self._record)

    def stop(self) -> None:
        """Stop the sound and release its playback voice.

        Calling this for a terminal sound is harmless. The handle retains its
        status with an end reason of ``SoundEndReason.STOPPED``.
        """

        self._runtime.stop(self._record)

    def seek(self, offset_seconds: float) -> None:
        """Move the playhead to an offset in source-audio seconds.

        Seeking a terminal sound creates a new voice in the initial state but
        does not start playback.

        Args:
            offset_seconds: Finite offset greater than or equal to zero and
                strictly less than the source duration.

        Raises:
            TypeError: ``offset_seconds`` is not numeric.
            ValueError: ``offset_seconds`` is non-finite or outside the source.
            InvalidVoiceStateError: The convenience runtime has been shut down.
        """

        self._runtime.seek(self._record, offset_seconds)

    def seek_frames(self, offset_frames: int) -> None:
        """Move the playhead to an exact sample-frame offset.

        Seeking a terminal sound creates a new voice in the initial state.

        Args:
            offset_frames: Integer frame index greater than or equal to zero and
                strictly less than [`frame_count`][pyalsoft.PlayingSound.frame_count].

        Raises:
            TypeError: ``offset_frames`` is not an integer.
            ValueError: ``offset_frames`` is outside the source.
            InvalidVoiceStateError: The convenience runtime has been shut down.
        """

        self._runtime.seek_frames(self._record, offset_frames)

    def rewind(self) -> None:
        """Move the playhead to the beginning and enter the initial state.

        A terminal sound receives a new voice but does not begin playing.

        Raises:
            InvalidVoiceStateError: The convenience runtime has been shut down.
        """

        self._runtime.rewind(self._record)

    def restart(self) -> None:
        """Start the sound again from its beginning.

        A terminal sound receives a new voice and becomes active again.

        Raises:
            InvalidVoiceStateError: The convenience runtime has been shut down.
        """

        self._runtime.restart(self._record)

    def set_config(self, config: VoiceConfig) -> None:
        """Apply a complete immutable voice configuration.

        For a terminal sound, the configuration is stored for a later restart.

        Args:
            config: Complete replacement configuration.

        Raises:
            TypeError: ``config`` is not a [`VoiceConfig`][pyalsoft.VoiceConfig].
            InvalidVoiceStateError: The runtime was shut down while this sound was
                active.
            AudioBackendError: OpenAL cannot apply the configuration or EFX.
        """

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
        """Validate and apply a batch of source-control changes.

        ``None`` leaves most fields unchanged. ``filter`` is the exception:
        passing ``None`` removes the direct filter, while omitting it leaves the
        filter unchanged. Pass an empty ``effect_sends`` sequence to remove all
        auxiliary routes. Changes are stored for later restart when the sound is
        terminal.

        Args:
            position: New 3D position.
            velocity: New velocity used for Doppler shift.
            direction: New attenuation-cone direction.
            gain: New non-negative linear gain.
            pitch: New playback-rate multiplier from 0.5 through 2.0.
            looping: Whether the source repeats.
            relative: Whether coordinates are listener-relative.
            min_gain: New lower gain clamp.
            max_gain: New upper gain clamp.
            reference_distance: New distance with unity attenuation.
            max_distance: New outer distance for clamped models.
            rolloff_factor: New distance-attenuation multiplier.
            cone_inner_angle: New full inner cone angle in degrees.
            cone_outer_angle: New full outer cone angle in degrees.
            cone_outer_gain: New gain outside the outer cone.
            filter: Replacement direct EFX filter, or ``None`` to remove it.
            effect_sends: Replacement auxiliary routes; an empty sequence removes
                them all.

        Raises:
            TypeError: A value has the wrong type.
            ValueError: A value is non-finite or outside its supported range.
            InvalidVoiceStateError: The runtime was shut down while this sound was
                active.
            AudioBackendError: OpenAL cannot apply the configuration or EFX.
        """

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
