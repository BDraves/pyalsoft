"""Exercise representative backend paths against a real OpenAL Soft runtime."""

from __future__ import annotations

import math
import struct

from pyalsoft import bindings


def _mono_sine(frame_count: int, sample_rate: int) -> bytes:
    return b"".join(
        struct.pack(
            "<h",
            round(8_000 * math.sin(2 * math.pi * 440 * frame / sample_rate)),
        )
        for frame in range(frame_count)
    )


def _require_no_al_error(library: bindings.OpenALLibrary, operation: str) -> None:
    error = library.al.get_error()
    if int(error) != bindings.AL_NO_ERROR:
        name = getattr(error, "name", f"0x{int(error):04x}")
        raise RuntimeError(f"{operation} failed with OpenAL {name}")


def run(library: bindings.OpenALLibrary | None = None) -> str:
    """Run deterministic core, object, EFX, and callback checks."""

    selected = library or bindings.load()
    sample_rate = 48_000
    channels = bindings.ALC_STEREO_SOFT
    sample_type = bindings.ALC_SHORT_SOFT
    context_attributes = [
        bindings.ALC_FORMAT_CHANNELS_SOFT,
        channels,
        bindings.ALC_FORMAT_TYPE_SOFT,
        sample_type,
        bindings.ALC_FREQUENCY,
        sample_rate,
    ]
    with bindings.open_loopback_device(library=selected) as device:
        if device.is_extension_present("ALC_EXT_debug"):
            context_attributes.extend(
                [bindings.ALC_CONTEXT_FLAGS_EXT, bindings.ALC_CONTEXT_DEBUG_BIT_EXT]
            )
        if not device.is_render_format_supported(
            sample_rate,
            channels,
            sample_type,
        ):
            raise RuntimeError("OpenAL Soft rejected the conformance render format")

        with device.create_context(context_attributes) as context:  # noqa: SIM117
            with context.activate():
                renderer = context.renderer
                version = context.version
                if not renderer or not version:
                    raise RuntimeError("OpenAL returned incomplete context identity")

                al = selected.al
                for _ in range(16):
                    if int(al.get_error()) == bindings.AL_NO_ERROR:
                        break
                else:
                    raise RuntimeError("OpenAL error state could not be cleared")

                buffer_id = al.gen_buffers()[0]
                source_id = al.gen_sources()[0]
                try:
                    pcm = _mono_sine(512, sample_rate)
                    al.buffer_data(
                        buffer_id,
                        bindings.AL_FORMAT_MONO16,
                        pcm,
                        sample_rate,
                    )
                    buffer = al.buffer(buffer_id)
                    source = al.source(source_id)
                    source.buffer = buffer
                    source.position = (0.0, 0.0, -1.0)
                    source.gain = 0.5
                    if buffer.frequency != sample_rate:
                        raise RuntimeError("typed buffer property returned wrong rate")
                    al.source_play(source_id)

                    rendered = bytearray(512 * 2 * 2)
                    device.render_samples(rendered, 512)
                    if not any(rendered):
                        raise RuntimeError(
                            "loopback render unexpectedly produced silence"
                        )
                    _require_no_al_error(selected, "core loopback rendering")

                    if not device.is_extension_present("ALC_EXT_EFX"):
                        raise RuntimeError("OpenAL Soft did not expose ALC_EXT_EFX")
                    effect_id = al.gen_effects()[0]
                    try:
                        effect = al.effect(effect_id)
                        effect.type = bindings.enums.ALEffectType.EFFECT_REVERB
                        effect.reverb_gain = 0.25
                        if not 0.24 < effect.reverb_gain < 0.26:
                            raise RuntimeError("typed EFX property did not round-trip")
                        _require_no_al_error(selected, "EFX property round-trip")
                    finally:
                        al.delete_effects((effect_id,))

                    if context.library.is_al_extension_present("AL_EXT_debug"):
                        messages: list[str] = []
                        with context.register_debug_callback(
                            lambda _source, _type, _id, _severity, message: (
                                messages.append(message)
                            )
                        ):
                            al.debug_message_insert_ext(
                                bindings.AL_DEBUG_SOURCE_APPLICATION_EXT,
                                bindings.AL_DEBUG_TYPE_MARKER_EXT,
                                1,
                                bindings.AL_DEBUG_SEVERITY_NOTIFICATION_EXT,
                                "pyalsoft conformance",
                            )
                        if messages != ["pyalsoft conformance"]:
                            raise RuntimeError("debug callback did not round-trip")
                finally:
                    al.source_stop(source_id)
                    al.delete_sources((source_id,))
                    al.delete_buffers((buffer_id,))
                    _require_no_al_error(selected, "conformance cleanup")

    return f"backend conformance passed: {renderer} ({version})"


def main() -> None:
    print(run())


if __name__ == "__main__":
    main()
