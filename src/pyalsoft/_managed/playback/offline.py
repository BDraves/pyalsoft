"""Managed device-independent offline rendering."""

from __future__ import annotations

from pyalsoft import bindings
from pyalsoft._managed._backend import _check_alc_error
from pyalsoft._managed.audio import AmbisonicLayout, AmbisonicScaling
from pyalsoft._managed.errors import AudioBackendError, PlaybackOpenError
from pyalsoft._managed.playback.session import (
    Playback,
    _active_playbacks,
    _active_playbacks_lock,
    _close_playback_device,
    _load_playback_library,
    _playback_for_context,
    _prepare_al,
    _serialized_playback,
)
from pyalsoft._managed.resources import (
    PlaybackConfig,
    RenderChannelLayout,
    RenderConfig,
    RenderSampleType,
)

_CHANNELS_TO_ALC = {
    RenderChannelLayout.MONO: bindings.ALC_MONO_SOFT,
    RenderChannelLayout.STEREO: bindings.ALC_STEREO_SOFT,
    RenderChannelLayout.QUAD: bindings.ALC_QUAD_SOFT,
    RenderChannelLayout.SURROUND_5_1: bindings.ALC_5POINT1_SOFT,
    RenderChannelLayout.SURROUND_6_1: bindings.ALC_6POINT1_SOFT,
    RenderChannelLayout.SURROUND_7_1: bindings.ALC_7POINT1_SOFT,
    RenderChannelLayout.BFORMAT_3D: bindings.ALC_BFORMAT3D_SOFT,
}
_SAMPLE_TYPES_TO_ALC = {
    RenderSampleType.INT8: bindings.ALC_BYTE_SOFT,
    RenderSampleType.UINT8: bindings.ALC_UNSIGNED_BYTE_SOFT,
    RenderSampleType.INT16: bindings.ALC_SHORT_SOFT,
    RenderSampleType.UINT16: bindings.ALC_UNSIGNED_SHORT_SOFT,
    RenderSampleType.INT32: bindings.ALC_INT_SOFT,
    RenderSampleType.UINT32: bindings.ALC_UNSIGNED_INT_SOFT,
    RenderSampleType.FLOAT32: bindings.ALC_FLOAT_SOFT,
}
_AMBISONIC_LAYOUT_TO_ALC = {
    AmbisonicLayout.FUMA: bindings.ALC_FUMA_SOFT,
    AmbisonicLayout.ACN: bindings.ALC_ACN_SOFT,
}
_AMBISONIC_SCALING_TO_ALC = {
    AmbisonicScaling.FUMA: bindings.ALC_FUMA_SOFT,
    AmbisonicScaling.SN3D: bindings.ALC_SN3D_SOFT,
    AmbisonicScaling.N3D: bindings.ALC_N3D_SOFT,
}
_DEFAULT_RENDER_CONFIG = RenderConfig()


class OfflinePlayback(Playback):
    """Managed playback session whose output is rendered into memory."""

    __slots__ = ("_render_config",)

    def __init__(
        self,
        library: bindings.OpenALLibrary,
        device: object,
        context: object,
        config: PlaybackConfig,
        previous_context: object | None,
        previous_playback: Playback | None,
        *,
        render_config: RenderConfig,
    ) -> None:
        super().__init__(
            library,
            device,
            context,
            config,
            previous_context,
            previous_playback,
        )
        self._render_config = render_config

    @property
    def render_config(self) -> RenderConfig:
        """Immutable output format selected when this session opened."""

        return self._render_config


def _render_context_attributes(config: RenderConfig) -> tuple[int, ...]:
    attributes = [
        bindings.ALC_FORMAT_CHANNELS_SOFT,
        _CHANNELS_TO_ALC[config.channels],
        bindings.ALC_FORMAT_TYPE_SOFT,
        _SAMPLE_TYPES_TO_ALC[config.sample_type],
        bindings.ALC_FREQUENCY,
        config.sample_rate,
    ]
    if config.channels is RenderChannelLayout.BFORMAT_3D:
        attributes.extend(
            (
                bindings.ALC_AMBISONIC_ORDER_SOFT,
                config.ambisonic_order,
                bindings.ALC_AMBISONIC_LAYOUT_SOFT,
                _AMBISONIC_LAYOUT_TO_ALC[config.ambisonic_layout],
                bindings.ALC_AMBISONIC_SCALING_SOFT,
                _AMBISONIC_SCALING_TO_ALC[config.ambisonic_scaling],
            )
        )
    return tuple(attributes)


def open_offline_playback(
    config: RenderConfig = _DEFAULT_RENDER_CONFIG,
    *,
    library: bindings.OpenALLibrary | None = None,
) -> OfflinePlayback:
    """Open a managed playback session for deterministic offline rendering."""

    if not isinstance(config, RenderConfig):
        raise TypeError("config must be a RenderConfig")
    selected = _load_playback_library(library)
    with _active_playbacks_lock, selected._context_lock:
        if not selected.alc.is_extension_present(None, "ALC_SOFT_loopback"):
            raise PlaybackOpenError(
                "offline playback requires the ALC_SOFT_loopback extension"
            )
        _check_alc_error(selected, None, "query offline rendering support")
        previous_context = selected.alc.get_current_context()
        previous_playback = _playback_for_context(selected, previous_context)
        device = selected.alc.loopback_open_device_soft(None)
        if not device:
            raise PlaybackOpenError("could not open an offline rendering device")
        context: object | None = None
        try:
            if config.channels is RenderChannelLayout.BFORMAT_3D:
                if not selected.alc.is_extension_present(
                    device, "ALC_SOFT_loopback_bformat"
                ):
                    raise PlaybackOpenError(
                        "B-format rendering requires ALC_SOFT_loopback_bformat"
                    )
                maximum_order = selected.alc.get_integerv(
                    device,
                    bindings.ALC_MAX_AMBISONIC_ORDER_SOFT,
                    1,
                )[0]
                _check_alc_error(selected, device, "query maximum ambisonic order")
                if config.ambisonic_order > maximum_order:
                    raise PlaybackOpenError(
                        "the offline device supports ambisonic orders through "
                        f"{maximum_order}, not {config.ambisonic_order}"
                    )
            native_channels = _CHANNELS_TO_ALC[config.channels]
            native_type = _SAMPLE_TYPES_TO_ALC[config.sample_type]
            if not selected.alc.is_render_format_supported_soft(
                device,
                config.sample_rate,
                native_channels,
                native_type,
            ):
                raise PlaybackOpenError(
                    "the offline device does not support the requested render format"
                )
            _check_alc_error(selected, device, "validate offline render format")
            context = selected.alc.create_context(
                device,
                _render_context_attributes(config),
            )
            if not context:
                raise PlaybackOpenError("could not create an offline OpenAL context")
            if not selected.alc.make_context_current(context):
                raise PlaybackOpenError("could not activate the offline OpenAL context")
        except Exception:
            if context is not None:
                selected.alc.destroy_context(context)
            _close_playback_device(selected, device)
            raise

        playback = OfflinePlayback(
            selected,
            device,
            context,
            PlaybackConfig(sample_rate=config.sample_rate),
            previous_context,
            previous_playback,
            render_config=config,
        )
        _active_playbacks.add(playback)
        return playback


def render_samples(playback: OfflinePlayback, frame_count: int) -> bytes:
    """Render exactly ``frame_count`` output frames and return interleaved bytes."""

    if not isinstance(playback, OfflinePlayback):
        raise TypeError("playback must be an OfflinePlayback")
    return _render_samples(playback, frame_count)


@_serialized_playback
def _render_samples(playback: Playback, frame_count: int) -> bytes:
    if isinstance(frame_count, bool) or not isinstance(frame_count, int):
        raise TypeError("frame_count must be an integer")
    if frame_count <= 0:
        raise ValueError("frame_count must be positive")
    assert isinstance(playback, OfflinePlayback)
    byte_count = frame_count * playback.render_config.frame_width_bytes
    try:
        output = bytearray(byte_count)
    except (MemoryError, OverflowError) as error:
        raise ValueError("rendered output is too large") from error
    _prepare_al(playback)
    playback._library.alc.render_samples_soft(
        playback._device,
        output,
        frame_count,
    )
    _check_alc_error(
        playback._library,
        playback._device,
        "render offline samples",
    )
    return bytes(output)


@_serialized_playback
def pause_playback_device(playback: Playback) -> None:
    """Pause processing for a playback device without changing voice states."""

    if not playback._library.alc.is_extension_present(
        playback._device, "ALC_SOFT_pause_device"
    ):
        raise AudioBackendError(
            "device pause requires the ALC_SOFT_pause_device extension"
        )
    playback._library.alc.device_pause_soft(playback._device)
    _check_alc_error(playback._library, playback._device, "pause playback device")


@_serialized_playback
def resume_playback_device(playback: Playback) -> None:
    """Resume processing for a playback device without changing voice states."""

    if not playback._library.alc.is_extension_present(
        playback._device, "ALC_SOFT_pause_device"
    ):
        raise AudioBackendError(
            "device resume requires the ALC_SOFT_pause_device extension"
        )
    playback._library.alc.device_resume_soft(playback._device)
    _check_alc_error(playback._library, playback._device, "resume playback device")
