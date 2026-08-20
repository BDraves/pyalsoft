"""Configure several voices with the managed advanced source API."""

from __future__ import annotations

from dataclasses import dataclass
from math import radians

from pyalsoft import (
    PCM,
    DirectChannelsMode,
    DistanceModel,
    EffectSend,
    PlaybackClock,
    Resampler,
    Reverb,
    SpatializationMode,
    StereoMode,
    VoiceClock,
    VoiceConfig,
    VoiceLatency,
    get_playback_clock,
    get_voice_clock,
    get_voice_latency,
    list_resamplers,
    open_playback,
    play,
    release,
    upload,
)
from pyalsoft.bindings import OpenALLibrary


@dataclass(frozen=True, slots=True)
class AdvancedSourceReport:
    """Timing and resampler information observed by the example."""

    resamplers: tuple[Resampler, ...]
    voice_latency: VoiceLatency
    voice_clock: VoiceClock
    playback_clock: PlaybackClock


def _stereo_silence(frame_count: int, sample_rate: int) -> PCM:
    return PCM(
        bytes(frame_count * 2 * 2),
        channels=2,
        sample_rate=sample_rate,
    )


def advanced_sources(
    library: OpenALLibrary | None = None,
) -> AdvancedSourceReport:
    """Exercise spatial, direct-channel, stereo, resampler, and timing controls."""

    sample_rate = 48_000
    mono = PCM(bytes(4_800 * 2), channels=1, sample_rate=sample_rate)
    stereo = _stereo_silence(4_800, sample_rate)

    with open_playback(library=library) as playback:
        mono_clip = upload(playback, mono)
        stereo_clip = upload(playback, stereo)
        resamplers = list_resamplers(playback)
        selected_resampler = next(
            (value for value in resamplers if value.name == "Cubic Spline"),
            next(value for value in resamplers if value.is_default),
        )

        machine = play(
            playback,
            mono_clip,
            VoiceConfig(
                position=(8.0, 0.0, -12.0),
                distance_model=DistanceModel.EXPONENT_CLAMPED,
                radius=2.5,
                spatialization=SpatializationMode.ENABLED,
                resampler=selected_resampler,
                air_absorption_factor=1.0,
                room_rolloff_factor=0.7,
                effect_sends=(EffectSend(effect=Reverb(gain=0.25, decay_time=1.4)),),
            ),
        )

        mastered_music = play(
            playback,
            stereo_clip,
            VoiceConfig(
                spatialization=SpatializationMode.DISABLED,
                direct_channels=DirectChannelsMode.REMIX_UNMATCHED,
            ),
        )

        enveloping_ambience = play(
            playback,
            stereo_clip,
            VoiceConfig(
                stereo_mode=StereoMode.SUPER_STEREO,
                super_stereo_width=0.65,
            ),
        )

        virtual_speakers = play(
            playback,
            stereo_clip,
            VoiceConfig(
                stereo_angles=(radians(70.0), radians(-70.0)),
            ),
        )

        report = AdvancedSourceReport(
            resamplers=resamplers,
            voice_latency=get_voice_latency(playback, machine),
            voice_clock=get_voice_clock(playback, machine),
            playback_clock=get_playback_clock(playback),
        )

        for voice in (
            machine,
            mastered_music,
            enveloping_ambience,
            virtual_speakers,
        ):
            release(playback, voice)
        release(playback, mono_clip)
        release(playback, stereo_clip)
        return report


if __name__ == "__main__":
    result = advanced_sources()
    print("resamplers:", ", ".join(value.name for value in result.resamplers))
    print("source latency:", result.voice_latency.output_latency_seconds, "seconds")
    print("device clock:", result.playback_clock.device_time_ns, "nanoseconds")
