"""Shared OpenAL details used by the managed playback and capture APIs."""

from __future__ import annotations

from pyalsoft import bindings
from pyalsoft._managed.audio import (
    AmbisonicLayout,
    AmbisonicScaling,
    BufferData,
    BufferFormat,
    SampleType,
)
from pyalsoft._managed.errors import AudioBackendError

_FORMAT_BY_LAYOUT = {
    (1, SampleType.UINT8): bindings.enums.ALFormat.FORMAT_MONO8,
    (1, SampleType.INT16): bindings.enums.ALFormat.FORMAT_MONO16,
    (2, SampleType.UINT8): bindings.enums.ALFormat.FORMAT_STEREO8,
    (2, SampleType.INT16): bindings.enums.ALFormat.FORMAT_STEREO16,
    (1, SampleType.FLOAT32): bindings.enums.ALFormat.FORMAT_MONO_FLOAT32,
    (2, SampleType.FLOAT32): bindings.enums.ALFormat.FORMAT_STEREO_FLOAT32,
    (1, SampleType.FLOAT64): bindings.enums.ALFormat.FORMAT_MONO_DOUBLE_EXT,
    (2, SampleType.FLOAT64): bindings.enums.ALFormat.FORMAT_STEREO_DOUBLE_EXT,
    (4, SampleType.UINT8): bindings.enums.ALFormat.FORMAT_QUAD8,
    (4, SampleType.INT16): bindings.enums.ALFormat.FORMAT_QUAD16,
    (4, SampleType.FLOAT32): bindings.enums.ALFormat.FORMAT_QUAD32,
    (6, SampleType.UINT8): bindings.enums.ALFormat.FORMAT_51CHN8,
    (6, SampleType.INT16): bindings.enums.ALFormat.FORMAT_51CHN16,
    (6, SampleType.FLOAT32): bindings.enums.ALFormat.FORMAT_51CHN32,
    (7, SampleType.UINT8): bindings.enums.ALFormat.FORMAT_61CHN8,
    (7, SampleType.INT16): bindings.enums.ALFormat.FORMAT_61CHN16,
    (7, SampleType.FLOAT32): bindings.enums.ALFormat.FORMAT_61CHN32,
    (8, SampleType.UINT8): bindings.enums.ALFormat.FORMAT_71CHN8,
    (8, SampleType.INT16): bindings.enums.ALFormat.FORMAT_71CHN16,
    (8, SampleType.FLOAT32): bindings.enums.ALFormat.FORMAT_71CHN32,
}

_PCM_EXTENSION_BY_LAYOUT = {
    (1, SampleType.FLOAT32): "AL_EXT_float32",
    (2, SampleType.FLOAT32): "AL_EXT_float32",
    (1, SampleType.FLOAT64): "AL_EXT_double",
    (2, SampleType.FLOAT64): "AL_EXT_double",
    **{
        (channels, sample_type): "AL_EXT_MCFORMATS"
        for channels in (4, 6, 7, 8)
        for sample_type in (SampleType.UINT8, SampleType.INT16, SampleType.FLOAT32)
    },
}

_AMBISONIC_LAYOUT_TO_AL = {
    AmbisonicLayout.FUMA: bindings.AL_FUMA_SOFT,
    AmbisonicLayout.ACN: bindings.AL_ACN_SOFT,
}
_AMBISONIC_SCALING_TO_AL = {
    AmbisonicScaling.FUMA: bindings.AL_FUMA_SOFT,
    AmbisonicScaling.SN3D: bindings.AL_SN3D_SOFT,
    AmbisonicScaling.N3D: bindings.AL_N3D_SOFT,
}


def _require_al_extension(
    library: bindings.OpenALLibrary,
    extension: str | tuple[str, ...] | None,
    feature: str,
) -> None:
    if extension is None:
        return
    extensions = (extension,) if isinstance(extension, str) else extension
    if not extensions or any(
        library.is_al_extension_present(name) for name in extensions
    ):
        return
    requirement = extensions[0] if len(extensions) == 1 else " or ".join(extensions)
    raise AudioBackendError(f"{feature} requires the {requirement} extension")


def _require_pcm_layout(
    library: bindings.OpenALLibrary, channels: int, sample_type: SampleType
) -> None:
    _require_al_extension(
        library,
        _PCM_EXTENSION_BY_LAYOUT.get((channels, sample_type)),
        f"{channels}-channel {sample_type.value} PCM",
    )


def _prepare_buffer_data(
    library: bindings.OpenALLibrary, identifier: int, data: BufferData
) -> None:
    """Check and apply extension-backed properties before a buffer upload."""

    _require_al_extension(
        library,
        data.format.required_extensions,
        data.format.value,
    )
    for dependency in data.format._required_dependencies:
        _require_al_extension(library, dependency, data.format.value)
    if data.block_alignment is not None:
        _require_al_extension(
            library,
            "AL_SOFT_block_alignment",
            "compressed-format block alignment",
        )
        library.al.bufferi(
            identifier,
            bindings.AL_UNPACK_BLOCK_ALIGNMENT_SOFT,
            data.block_alignment,
        )
    if data.ambisonic_layout is not None or data.ambisonic_scaling is not None:
        _require_al_extension(
            library,
            "AL_SOFT_bformat_ex",
            "ambisonic layout and scaling",
        )
    if data.ambisonic_layout is not None:
        library.al.bufferi(
            identifier,
            bindings.AL_AMBISONIC_LAYOUT_SOFT,
            _AMBISONIC_LAYOUT_TO_AL[data.ambisonic_layout],
        )
    if data.ambisonic_scaling is not None:
        library.al.bufferi(
            identifier,
            bindings.AL_AMBISONIC_SCALING_SOFT,
            _AMBISONIC_SCALING_TO_AL[data.ambisonic_scaling],
        )
    if data.ambisonic_order != 1:
        _require_al_extension(
            library,
            "AL_SOFT_bformat_hoa",
            "higher-order ambisonic buffers",
        )
        library.al.bufferi(
            identifier,
            bindings.AL_UNPACK_AMBISONIC_ORDER_SOFT,
            data.ambisonic_order,
        )


def _buffer_format_for_pcm(channels: int, sample_type: SampleType) -> BufferFormat:
    return {
        (1, SampleType.UINT8): BufferFormat.MONO_UINT8,
        (1, SampleType.INT16): BufferFormat.MONO_INT16,
        (2, SampleType.UINT8): BufferFormat.STEREO_UINT8,
        (2, SampleType.INT16): BufferFormat.STEREO_INT16,
        (1, SampleType.FLOAT32): BufferFormat.MONO_FLOAT32,
        (2, SampleType.FLOAT32): BufferFormat.STEREO_FLOAT32,
        (1, SampleType.FLOAT64): BufferFormat.MONO_FLOAT64,
        (2, SampleType.FLOAT64): BufferFormat.STEREO_FLOAT64,
        (4, SampleType.UINT8): BufferFormat.QUAD_UINT8,
        (4, SampleType.INT16): BufferFormat.QUAD_INT16,
        (4, SampleType.FLOAT32): BufferFormat.QUAD_FLOAT32,
        (6, SampleType.UINT8): BufferFormat.SURROUND_5_1_UINT8,
        (6, SampleType.INT16): BufferFormat.SURROUND_5_1_INT16,
        (6, SampleType.FLOAT32): BufferFormat.SURROUND_5_1_FLOAT32,
        (7, SampleType.UINT8): BufferFormat.SURROUND_6_1_UINT8,
        (7, SampleType.INT16): BufferFormat.SURROUND_6_1_INT16,
        (7, SampleType.FLOAT32): BufferFormat.SURROUND_6_1_FLOAT32,
        (8, SampleType.UINT8): BufferFormat.SURROUND_7_1_UINT8,
        (8, SampleType.INT16): BufferFormat.SURROUND_7_1_INT16,
        (8, SampleType.FLOAT32): BufferFormat.SURROUND_7_1_FLOAT32,
    }[(channels, sample_type)]


def _clear_alc_errors(
    library: bindings.OpenALLibrary, device: object | None = None
) -> None:
    for _ in range(16):
        if int(library.alc.get_error(device)) == bindings.ALC_NO_ERROR:
            return
    raise AudioBackendError("OpenAL ALC error state could not be cleared")


def _check_alc_error(
    library: bindings.OpenALLibrary,
    device: object | None,
    operation: str,
) -> None:
    code = int(library.alc.get_error(device))
    if code == bindings.ALC_NO_ERROR:
        return
    try:
        name = bindings.enums.ALCContextErrorCode(code).name
    except ValueError:
        name = f"unknown error 0x{code:04x}"
    raise AudioBackendError(f"{operation} failed: OpenAL ALC {name}")
