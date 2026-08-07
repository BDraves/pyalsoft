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
    if selected.is_alc_extension_present("ALC_SOFT_system_events"):
        system_registration = selected.register_system_event_callback(
            lambda _event, _device_type, _device, _message: None,
            event_types=(bindings.ALC_EVENT_TYPE_DEVICE_ADDED_SOFT,),
        )
        selected.clear_system_event_callback()
        if not system_registration.closed:
            raise RuntimeError("system-event callback did not close")

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
    callback_supported = False
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

                    if context.library.is_al_extension_present(
                        "AL_SOFT_callback_buffer"
                    ):
                        callback_supported = True
                        callback_buffer = al.gen_buffers()[0]
                        callback_calls = 0

                        def fill_callback(view: memoryview) -> int:
                            nonlocal callback_calls
                            callback_calls += 1
                            view[:] = struct.pack("<h", 2_000) * (len(view) // 2)
                            return len(view)

                        try:
                            al.source_stop(source_id)
                            al.sourcei(source_id, bindings.AL_BUFFER, 0)
                            with context.register_buffer_callback(
                                callback_buffer,
                                bindings.AL_FORMAT_MONO16,
                                sample_rate,
                                fill_callback,
                            ):
                                al.sourcei(
                                    source_id,
                                    bindings.AL_BUFFER,
                                    callback_buffer,
                                )
                                al.source_play(source_id)
                                callback_render = bytearray(128 * 2 * 2)
                                device.render_samples(callback_render, 128)
                                al.source_stop(source_id)
                                al.sourcei(source_id, bindings.AL_BUFFER, 0)
                            if callback_calls == 0 or not any(callback_render):
                                raise RuntimeError(
                                    "callback buffer did not render sample data"
                                )
                            _require_no_al_error(
                                selected,
                                "callback buffer rendering",
                            )
                        finally:
                            al.source_stop(source_id)
                            al.sourcei(source_id, bindings.AL_BUFFER, 0)
                            al.delete_buffers((callback_buffer,))

                    if context.library.is_al_extension_present("AL_EXT_STATIC_BUFFER"):
                        static_buffer = al.gen_buffers()[0]
                        static_pcm = struct.pack("<h", 1_500) * 128
                        try:
                            al.source_stop(source_id)
                            al.sourcei(source_id, bindings.AL_BUFFER, 0)
                            context.set_static_buffer_data(
                                static_buffer,
                                bindings.AL_FORMAT_MONO16,
                                static_pcm,
                                sample_rate,
                            )
                            al.sourcei(source_id, bindings.AL_BUFFER, static_buffer)
                            al.source_play(source_id)
                            static_render = bytearray(128 * 2 * 2)
                            device.render_samples(static_render, 128)
                            if not any(static_render):
                                raise RuntimeError(
                                    "static buffer did not render sample data"
                                )
                            _require_no_al_error(
                                selected,
                                "static buffer rendering",
                            )
                        finally:
                            al.source_stop(source_id)
                            al.sourcei(source_id, bindings.AL_BUFFER, 0)
                            al.delete_buffers((static_buffer,))

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

        if callback_supported:
            teardown_context = device.create_context(context_attributes)
            with teardown_context.activate():
                teardown_buffer = selected.al.gen_buffers()[0]
                teardown_source = selected.al.gen_sources()[0]
                teardown_registration = teardown_context.register_buffer_callback(
                    teardown_buffer,
                    bindings.AL_FORMAT_MONO16,
                    sample_rate,
                    lambda view: len(view),
                )
                selected.al.sourcei(
                    teardown_source,
                    bindings.AL_BUFFER,
                    teardown_buffer,
                )
            teardown_context.close()
            if not teardown_registration.closed:
                raise RuntimeError(
                    "context teardown did not finalize its callback buffer"
                )

    return f"backend conformance passed: {renderer} ({version})"


def main() -> None:
    print(run())


if __name__ == "__main__":
    main()
