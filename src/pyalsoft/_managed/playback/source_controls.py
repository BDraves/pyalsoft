"""Managed mappings for extension-backed source controls."""

from __future__ import annotations

from math import pi

from pyalsoft import bindings
from pyalsoft._managed._values import _finite_float
from pyalsoft._managed.audio import BufferFormat
from pyalsoft._managed.errors import AudioBackendError
from pyalsoft._managed.playback.session import (
    _DISTANCE_MODEL_TO_AL,
    Playback,
    _check_al_error,
    _prepare_al,
    _serialized_playback,
)
from pyalsoft._managed.spatial import (
    DirectChannelsMode,
    Resampler,
    SpatializationMode,
    StereoMode,
    VoiceConfig,
)

_SPATIALIZATION_TO_AL = {
    SpatializationMode.AUTO: bindings.AL_AUTO_SOFT,
    SpatializationMode.ENABLED: bindings.AL_TRUE,
    SpatializationMode.DISABLED: bindings.AL_FALSE,
}
_DIRECT_CHANNELS_TO_AL = {
    DirectChannelsMode.OFF: bindings.AL_FALSE,
    DirectChannelsMode.DROP_UNMATCHED: bindings.AL_DROP_UNMATCHED_SOFT,
    DirectChannelsMode.REMIX_UNMATCHED: bindings.AL_REMIX_UNMATCHED_SOFT,
}
_STEREO_MODE_TO_AL = {
    StereoMode.NORMAL: bindings.AL_NORMAL_SOFT,
    StereoMode.SUPER_STEREO: bindings.AL_SUPER_STEREO_SOFT,
}
_DEFAULT_STEREO_ANGLES = (pi / 6.0, -pi / 6.0)
_MAX_DELAY_FRAMES = 1 << 31
_AL_INT64_MAX = (1 << 63) - 1
_STEREO_BUFFER_FORMATS = {
    BufferFormat.STEREO_UINT8,
    BufferFormat.STEREO_INT16,
    BufferFormat.IMA_ADPCM_STEREO16_LOKI,
    BufferFormat.STEREO_FLOAT32,
    BufferFormat.STEREO_FLOAT64,
    BufferFormat.STEREO_MULAW,
    BufferFormat.STEREO_ALAW,
    BufferFormat.STEREO_IMA4,
    BufferFormat.STEREO_MSADPCM,
    BufferFormat.WAVE,
    BufferFormat.VORBIS,
}


def _require_al_extension(playback: Playback, extension: str, feature: str) -> None:
    if not playback._library.is_al_extension_present(extension):
        raise AudioBackendError(f"{feature} requires the {extension} extension")


def _require_alc_extension(playback: Playback, extension: str, feature: str) -> None:
    if not playback._library.alc.is_extension_present(playback._device, extension):
        raise AudioBackendError(f"{feature} requires the {extension} extension")


def _validate_playback_timing(
    delay_seconds: float,
    delay_frames: int | None,
    start_time_ns: int | None,
) -> tuple[float, int | None, int | None]:
    delay_seconds = _finite_float("delay_seconds", delay_seconds)
    if delay_seconds < 0.0:
        raise ValueError("delay_seconds cannot be negative")
    if delay_frames is not None:
        if isinstance(delay_frames, bool) or not isinstance(delay_frames, int):
            raise TypeError("delay_frames must be an integer or None")
        if delay_frames < 0:
            raise ValueError("delay_frames cannot be negative")
        if delay_frames > _MAX_DELAY_FRAMES:
            raise ValueError(f"delay_frames cannot exceed {_MAX_DELAY_FRAMES}")
        if delay_seconds != 0.0:
            raise ValueError("delay_seconds and delay_frames cannot both be set")
    if start_time_ns is not None:
        if isinstance(start_time_ns, bool) or not isinstance(start_time_ns, int):
            raise TypeError("start_time_ns must be an integer or None")
        if not 0 <= start_time_ns <= _AL_INT64_MAX:
            raise ValueError(f"start_time_ns must be between 0 and {_AL_INT64_MAX}")
    return delay_seconds, delay_frames, start_time_ns


def _require_source_start_delay(playback: Playback, feature: str) -> None:
    _require_al_extension(playback, "AL_SOFT_source_start_delay", feature)


def _apply_start_delay(
    playback: Playback,
    identifier: int,
    delay_seconds: float,
    delay_frames: int | None,
) -> None:
    if delay_seconds == 0.0 and not delay_frames:
        return
    _require_source_start_delay(playback, "delayed playback")
    if delay_frames is not None:
        playback._library.al.sourcei(
            identifier, bindings.AL_SAMPLE_OFFSET, -delay_frames
        )
    else:
        playback._library.al.sourcef(identifier, bindings.AL_SEC_OFFSET, -delay_seconds)


def _start_source(
    playback: Playback, identifier: int, start_time_ns: int | None
) -> None:
    if start_time_ns is None:
        playback._library.al.source_play(identifier)
        return
    _require_source_start_delay(playback, "scheduled playback")
    playback._library.al.source_play_at_time_soft(identifier, start_time_ns)


def _resamplers(playback: Playback) -> tuple[Resampler, ...]:
    _require_al_extension(
        playback, "AL_SOFT_source_resampler", "source resampler selection"
    )
    al = playback._library.al
    count = int(al.get_integer(bindings.AL_NUM_RESAMPLERS_SOFT))
    default = int(al.get_integer(bindings.AL_DEFAULT_RESAMPLER_SOFT))
    if count < 1 or not 0 <= default < count:
        raise AudioBackendError("OpenAL returned invalid source resampler metadata")
    values: list[Resampler] = []
    for index in range(count):
        name = al.get_stringi_soft(bindings.AL_RESAMPLER_NAME_SOFT, index)
        if not name:
            raise AudioBackendError(
                f"OpenAL returned no name for source resampler {index}"
            )
        values.append(Resampler(index, name, is_default=index == default))
    _check_al_error(playback, "enumerate source resamplers")
    return tuple(values)


@_serialized_playback
def list_resamplers(playback: Playback) -> tuple[Resampler, ...]:
    """Return the source resamplers provided by the active OpenAL implementation."""

    _prepare_al(playback)
    return _resamplers(playback)


def _enable_source_distance_models(playback: Playback) -> None:
    if playback._source_distance_model_enabled:
        return
    _require_al_extension(
        playback,
        "AL_EXT_source_distance_model",
        "per-source distance models",
    )
    al = playback._library.al
    inherited = int(al.get_integer(bindings.AL_DISTANCE_MODEL))
    al.enable(bindings.AL_SOURCE_DISTANCE_MODEL)
    for token, identifier in playback._voices.items():
        config = playback._voice_configs[token]
        model = (
            inherited
            if config.distance_model is None
            else _DISTANCE_MODEL_TO_AL[config.distance_model]
        )
        al.sourcei(identifier, bindings.AL_DISTANCE_MODEL, model)
    for record in playback._streams.values():
        model = (
            inherited
            if record.config.distance_model is None
            else _DISTANCE_MODEL_TO_AL[record.config.distance_model]
        )
        al.sourcei(record.identifier, bindings.AL_DISTANCE_MODEL, model)
    playback._source_distance_model_enabled = True


def _validate_source_layout(
    config: VoiceConfig,
    channels: int,
    format: BufferFormat | None = None,
) -> None:
    is_stereo = channels == 2 and (format is None or format in _STEREO_BUFFER_FORMATS)
    if config.direct_channels is not DirectChannelsMode.OFF and not is_stereo:
        raise ValueError("direct_channels requires a stereo clip or stream source")
    if config.stereo_angles is not None and not is_stereo:
        raise ValueError("stereo_angles requires a stereo source")
    if config.stereo_mode is StereoMode.SUPER_STEREO and not is_stereo:
        raise ValueError("Super Stereo processing requires a stereo source")


def _changed(
    previous: VoiceConfig | None,
    field: str,
    default: object,
    config: VoiceConfig,
) -> bool:
    current = getattr(config, field)
    if previous is None:
        return bool(current != default)
    return bool(current != getattr(previous, field))


def _apply_advanced_source_config(
    playback: Playback,
    identifier: int,
    config: VoiceConfig,
    *,
    previous: VoiceConfig | None,
) -> None:
    """Apply changed extension-backed fields without requiring unused extensions."""

    al = playback._library.al

    if (
        playback._source_distance_model_enabled
        or config.distance_model is not None
        or (previous is not None and previous.distance_model is not None)
    ):
        if not playback._source_distance_model_enabled:
            _enable_source_distance_models(playback)
        native_model = (
            int(al.get_integer(bindings.AL_DISTANCE_MODEL))
            if config.distance_model is None
            else _DISTANCE_MODEL_TO_AL[config.distance_model]
        )
        al.sourcei(identifier, bindings.AL_DISTANCE_MODEL, native_model)

    if _changed(previous, "radius", 0.0, config):
        _require_al_extension(playback, "AL_EXT_SOURCE_RADIUS", "source radius")
        al.sourcef(identifier, bindings.AL_SOURCE_RADIUS, config.radius)

    if _changed(previous, "spatialization", SpatializationMode.AUTO, config):
        _require_al_extension(
            playback,
            "AL_SOFT_source_spatialize",
            "explicit spatialization",
        )
        al.sourcei(
            identifier,
            bindings.AL_SOURCE_SPATIALIZE_SOFT,
            _SPATIALIZATION_TO_AL[config.spatialization],
        )

    if _changed(previous, "direct_channels", DirectChannelsMode.OFF, config):
        _require_al_extension(
            playback,
            "AL_SOFT_direct_channels",
            "direct channel playback",
        )
        if config.direct_channels is DirectChannelsMode.REMIX_UNMATCHED:
            _require_al_extension(
                playback,
                "AL_SOFT_direct_channels_remix",
                "direct channel remixing",
            )
        al.sourcei(
            identifier,
            bindings.AL_DIRECT_CHANNELS_SOFT,
            _DIRECT_CHANNELS_TO_AL[config.direct_channels],
        )

    if _changed(previous, "stereo_angles", None, config):
        _require_al_extension(playback, "AL_EXT_STEREO_ANGLES", "custom stereo angles")
        al.sourcefv(
            identifier,
            bindings.AL_STEREO_ANGLES,
            config.stereo_angles or _DEFAULT_STEREO_ANGLES,
        )

    if _changed(previous, "resampler", None, config):
        available = _resamplers(playback)
        if config.resampler is None:
            selected = next(value for value in available if value.is_default)
        else:
            matches = tuple(
                value
                for value in available
                if value.index == config.resampler.index
                and value.name == config.resampler.name
            )
            if not matches:
                raise ValueError(
                    "resampler is not available from this playback session"
                )
            selected = matches[0]
        al.sourcei(
            identifier,
            bindings.AL_SOURCE_RESAMPLER_SOFT,
            selected.index,
        )

    efx_fields = (
        (
            "air_absorption_factor",
            bindings.AL_AIR_ABSORPTION_FACTOR,
        ),
        ("room_rolloff_factor", bindings.AL_ROOM_ROLLOFF_FACTOR),
        (
            "cone_outer_gain_high_frequency",
            bindings.AL_CONE_OUTER_GAINHF,
        ),
    )
    for field, parameter in efx_fields:
        default = 1.0 if field == "cone_outer_gain_high_frequency" else 0.0
        if _changed(previous, field, default, config):
            _require_alc_extension(playback, "ALC_EXT_EFX", field.replace("_", " "))
            al.sourcef(identifier, parameter, getattr(config, field))

    efx_boolean_fields = (
        (
            "direct_filter_gain_high_frequency_auto",
            bindings.AL_DIRECT_FILTER_GAINHF_AUTO,
        ),
        (
            "auxiliary_send_filter_gain_auto",
            bindings.AL_AUXILIARY_SEND_FILTER_GAIN_AUTO,
        ),
        (
            "auxiliary_send_filter_gain_high_frequency_auto",
            bindings.AL_AUXILIARY_SEND_FILTER_GAINHF_AUTO,
        ),
    )
    for field, parameter in efx_boolean_fields:
        if _changed(previous, field, True, config):
            _require_alc_extension(playback, "ALC_EXT_EFX", field.replace("_", " "))
            al.sourcei(identifier, parameter, int(getattr(config, field)))

    if _changed(previous, "stereo_mode", StereoMode.NORMAL, config):
        _require_al_extension(playback, "AL_SOFT_UHJ", "Super Stereo processing")
        al.sourcei(
            identifier,
            bindings.AL_STEREO_MODE_SOFT,
            _STEREO_MODE_TO_AL[config.stereo_mode],
        )

    if _changed(previous, "super_stereo_width", None, config):
        _require_al_extension(playback, "AL_SOFT_UHJ", "Super Stereo width")
        default_width = playback._super_stereo_width_defaults.get(identifier)
        if default_width is None:
            default_width = float(
                al.get_sourcef(identifier, bindings.AL_SUPER_STEREO_WIDTH_SOFT)
            )
            playback._super_stereo_width_defaults[identifier] = default_width
        al.sourcef(
            identifier,
            bindings.AL_SUPER_STEREO_WIDTH_SOFT,
            default_width
            if config.super_stereo_width is None
            else config.super_stereo_width,
        )
