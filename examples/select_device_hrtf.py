from __future__ import annotations

import argparse
import time
from typing import TYPE_CHECKING, cast

from pyalsoft import (
    PCM,
    PlaybackConfig,
    PlaybackDevice,
    PlaybackOutputMode,
    VoiceConfig,
    VoiceState,
    get_playback_info,
    get_voice_status,
    list_hrtf_profiles,
    list_playback_devices,
    open_playback,
    play,
    upload,
)

if TYPE_CHECKING:
    from examples.play_sine import SAMPLE_RATE, sine_pcm
elif __package__:
    from .play_sine import SAMPLE_RATE, sine_pcm
else:
    from play_sine import SAMPLE_RATE, sine_pcm


def _requested_device_name() -> str | None:
    parser = argparse.ArgumentParser(
        description="List outputs, select one by name, and request HRTF."
    )
    parser.add_argument(
        "device",
        nargs="?",
        help="exact playback device name; defaults to the system default",
    )
    return cast(str | None, parser.parse_args().device)


def _select_device(
    devices: tuple[PlaybackDevice, ...], requested_name: str | None
) -> PlaybackDevice | None:
    if requested_name is None:
        return next((device for device in devices if device.is_default), None)
    selected = next(
        (device for device in devices if device.name == requested_name), None
    )
    if selected is None:
        raise SystemExit(f"unknown playback device: {requested_name}")
    return selected


def main() -> None:
    """Select an output, request HRTF, and play one positional sound."""

    requested_name = _requested_device_name()
    devices = list_playback_devices()
    for device in devices:
        marker = " (default)" if device.is_default else ""
        print(f"{device.name}{marker}")

    selected_device = _select_device(devices, requested_name)

    # PlaybackConfig describes preferences requested when creating the OpenAL
    # context. Optional extension requests are ignored when unsupported. The
    # resulting PlaybackInfo reports what the backend actually did.
    hrtf_profiles = list_hrtf_profiles(selected_device)
    config = PlaybackConfig(
        sample_rate=48_000,
        max_auxiliary_sends=2,
        hrtf=True,
        hrtf_name=hrtf_profiles[0] if hrtf_profiles else None,
        output_limiter=True,
        output_mode=PlaybackOutputMode.STEREO_HRTF,
    )
    with open_playback(selected_device, config=config) as playback:
        info = get_playback_info(playback)
        print(f"Opened: {info.device_name}")
        print(f"Renderer: {info.renderer}")
        print(f"OpenAL: {info.version}")
        print(f"Sample rate: {info.sample_rate} Hz")
        print(
            f"Output mode: {info.output_mode.value if info.output_mode else 'unknown'}"
        )
        print(f"Output limiter: {info.output_limiter}")
        print(f"Auxiliary sends per source: {info.max_auxiliary_sends}")
        print(f"HRTF: {info.hrtf_status.value}")
        if info.hrtf_name is not None:
            print(f"HRTF profile: {info.hrtf_name}")

        pcm = PCM(
            samples=sine_pcm(frequency=440.0, duration=1.0),
            channels=1,
            sample_rate=SAMPLE_RATE,
        )
        clip = upload(playback, pcm)
        voice = play(
            playback,
            clip,
            VoiceConfig(position=(2.0, 0.0, -1.0)),
        )

        deadline = time.monotonic() + 3.0
        while get_voice_status(playback, voice).state is VoiceState.PLAYING:
            if time.monotonic() >= deadline:
                raise RuntimeError("timed out waiting for playback to finish")
            time.sleep(0.01)

        # The clip and voice belong to this Playback. Leaving the with block
        # releases both, destroys its context, and closes its output device.


if __name__ == "__main__":
    main()
