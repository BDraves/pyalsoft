"""PCM formats, sample data, and source-audio metadata."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from os import PathLike

from pyalsoft import bindings

type AudioPath = str | PathLike[str]


class SampleType(Enum):
    """PCM sample representations supported by the managed API.

    Attributes:
        UINT8: Unsigned 8-bit samples, with silence at 128.
        INT16: Signed 16-bit samples, with silence at 0.
        FLOAT32: 32-bit IEEE 754 floating-point samples.
        FLOAT64: 64-bit IEEE 754 floating-point samples.
    """

    UINT8 = "uint8"
    INT16 = "int16"
    FLOAT32 = "float32"
    FLOAT64 = "float64"

    @property
    def byte_width(self) -> int:
        """Number of bytes used by one channel sample."""

        return {
            SampleType.UINT8: 1,
            SampleType.INT16: 2,
            SampleType.FLOAT32: 4,
            SampleType.FLOAT64: 8,
        }[self]


class BufferFormat(Enum):
    """Exact sample layouts accepted by managed OpenAL buffer uploads.

    Unlike [`SampleType`][pyalsoft.SampleType], these values describe both the
    channel layout and encoding. Most values require the OpenAL extension named
    by [`required_extensions`][pyalsoft.BufferFormat.required_extensions].
    """

    MONO_UINT8 = "mono_uint8"
    MONO_INT16 = "mono_int16"
    STEREO_UINT8 = "stereo_uint8"
    STEREO_INT16 = "stereo_int16"
    IMA_ADPCM_MONO16_LOKI = "ima_adpcm_mono16_loki"
    IMA_ADPCM_STEREO16_LOKI = "ima_adpcm_stereo16_loki"
    WAVE = "wave"
    VORBIS = "vorbis"
    QUAD_UINT8_LOKI = "quad_uint8_loki"
    QUAD_INT16_LOKI = "quad_int16_loki"
    MONO_FLOAT32 = "mono_float32"
    STEREO_FLOAT32 = "stereo_float32"
    MONO_FLOAT64 = "mono_float64"
    STEREO_FLOAT64 = "stereo_float64"
    MONO_MULAW = "mono_mulaw"
    STEREO_MULAW = "stereo_mulaw"
    MONO_ALAW = "mono_alaw"
    STEREO_ALAW = "stereo_alaw"
    QUAD_UINT8 = "quad_uint8"
    QUAD_INT16 = "quad_int16"
    QUAD_FLOAT32 = "quad_float32"
    REAR_UINT8 = "rear_uint8"
    REAR_INT16 = "rear_int16"
    REAR_FLOAT32 = "rear_float32"
    SURROUND_5_1_UINT8 = "surround_5_1_uint8"
    SURROUND_5_1_INT16 = "surround_5_1_int16"
    SURROUND_5_1_FLOAT32 = "surround_5_1_float32"
    SURROUND_6_1_UINT8 = "surround_6_1_uint8"
    SURROUND_6_1_INT16 = "surround_6_1_int16"
    SURROUND_6_1_FLOAT32 = "surround_6_1_float32"
    SURROUND_7_1_UINT8 = "surround_7_1_uint8"
    SURROUND_7_1_INT16 = "surround_7_1_int16"
    SURROUND_7_1_FLOAT32 = "surround_7_1_float32"
    QUAD_MULAW = "quad_mulaw"
    REAR_MULAW = "rear_mulaw"
    SURROUND_5_1_MULAW = "surround_5_1_mulaw"
    SURROUND_6_1_MULAW = "surround_6_1_mulaw"
    SURROUND_7_1_MULAW = "surround_7_1_mulaw"
    MONO_IMA4 = "mono_ima4"
    STEREO_IMA4 = "stereo_ima4"
    MONO_MSADPCM = "mono_msadpcm"
    STEREO_MSADPCM = "stereo_msadpcm"
    BFORMAT_2D_UINT8 = "bformat_2d_uint8"
    BFORMAT_2D_INT16 = "bformat_2d_int16"
    BFORMAT_2D_FLOAT32 = "bformat_2d_float32"
    BFORMAT_3D_UINT8 = "bformat_3d_uint8"
    BFORMAT_3D_INT16 = "bformat_3d_int16"
    BFORMAT_3D_FLOAT32 = "bformat_3d_float32"
    BFORMAT_2D_MULAW = "bformat_2d_mulaw"
    BFORMAT_3D_MULAW = "bformat_3d_mulaw"
    UHJ_2_UINT8 = "uhj_2_uint8"
    UHJ_2_INT16 = "uhj_2_int16"
    UHJ_2_FLOAT32 = "uhj_2_float32"
    UHJ_3_UINT8 = "uhj_3_uint8"
    UHJ_3_INT16 = "uhj_3_int16"
    UHJ_3_FLOAT32 = "uhj_3_float32"
    UHJ_4_UINT8 = "uhj_4_uint8"
    UHJ_4_INT16 = "uhj_4_int16"
    UHJ_4_FLOAT32 = "uhj_4_float32"
    UHJ_2_MULAW = "uhj_2_mulaw"
    UHJ_2_ALAW = "uhj_2_alaw"
    UHJ_2_IMA4 = "uhj_2_ima4"
    UHJ_2_MSADPCM = "uhj_2_msadpcm"
    UHJ_3_MULAW = "uhj_3_mulaw"
    UHJ_3_ALAW = "uhj_3_alaw"
    UHJ_4_MULAW = "uhj_4_mulaw"
    UHJ_4_ALAW = "uhj_4_alaw"

    @property
    def native_format(self) -> bindings.enums.ALFormat:
        """Low-level ``AL_FORMAT_*`` value used for upload."""

        return _BUFFER_FORMAT_SPECS[self].native_format

    @property
    def required_extensions(self) -> tuple[str, ...]:
        """Alternative OpenAL extensions that can provide this format.

        An empty tuple identifies a core format. Most extension formats return
        one item. Mono and stereo mu-law can be supplied by either of two
        historical extensions.
        """

        return _BUFFER_FORMAT_SPECS[self].extensions

    @property
    def _required_dependencies(self) -> tuple[str, ...]:
        return _BUFFER_FORMAT_SPECS[self].dependencies

    @property
    def sample_type(self) -> SampleType | None:
        """Decoded fixed-width sample representation, when applicable."""

        return _BUFFER_FORMAT_SPECS[self].sample_type

    @property
    def sample_width_bytes(self) -> int | None:
        """Encoded bytes per channel frame, or ``None`` for opaque/block data."""

        sample_type = self.sample_type
        if sample_type is not None:
            return sample_type.byte_width
        return 1 if self in _ONE_BYTE_FORMATS else None


class AmbisonicLayout(Enum):
    """Channel ordering for B-format buffer data."""

    FUMA = "fuma"
    ACN = "acn"


class AmbisonicScaling(Enum):
    """Coefficient normalization for B-format buffer data."""

    FUMA = "fuma"
    SN3D = "sn3d"
    N3D = "n3d"


@dataclass(frozen=True, slots=True)
class _BufferFormatSpec:
    native_format: bindings.enums.ALFormat
    channels: int | None
    sample_type: SampleType | None
    extensions: tuple[str, ...]
    dependencies: tuple[str, ...] = ()
    ambisonic_dimensions: int | None = None


def _format_spec(
    native_format: bindings.enums.ALFormat,
    channels: int | None,
    sample_type: SampleType | None,
    extension: str | tuple[str, ...] | None,
    ambisonic_dimensions: int | None = None,
    dependencies: tuple[str, ...] = (),
) -> _BufferFormatSpec:
    return _BufferFormatSpec(
        native_format,
        channels,
        sample_type,
        ()
        if extension is None
        else ((extension,) if isinstance(extension, str) else extension),
        dependencies,
        ambisonic_dimensions,
    )


_F = bindings.enums.ALFormat
_BUFFER_FORMAT_SPECS = {
    BufferFormat.MONO_UINT8: _format_spec(_F.FORMAT_MONO8, 1, SampleType.UINT8, None),
    BufferFormat.MONO_INT16: _format_spec(_F.FORMAT_MONO16, 1, SampleType.INT16, None),
    BufferFormat.STEREO_UINT8: _format_spec(
        _F.FORMAT_STEREO8, 2, SampleType.UINT8, None
    ),
    BufferFormat.STEREO_INT16: _format_spec(
        _F.FORMAT_STEREO16, 2, SampleType.INT16, None
    ),
    BufferFormat.IMA_ADPCM_MONO16_LOKI: _format_spec(
        _F.FORMAT_IMA_ADPCM_MONO16_EXT, 1, None, "AL_LOKI_IMA_ADPCM_format"
    ),
    BufferFormat.IMA_ADPCM_STEREO16_LOKI: _format_spec(
        _F.FORMAT_IMA_ADPCM_STEREO16_EXT, 2, None, "AL_LOKI_IMA_ADPCM_format"
    ),
    BufferFormat.WAVE: _format_spec(
        _F.FORMAT_WAVE_EXT, None, None, "AL_LOKI_WAVE_format"
    ),
    BufferFormat.VORBIS: _format_spec(
        _F.FORMAT_VORBIS_EXT, None, None, "AL_EXT_vorbis"
    ),
    BufferFormat.QUAD_UINT8_LOKI: _format_spec(
        _F.FORMAT_QUAD8_LOKI, 4, SampleType.UINT8, "AL_LOKI_quadriphonic"
    ),
    BufferFormat.QUAD_INT16_LOKI: _format_spec(
        _F.FORMAT_QUAD16_LOKI, 4, SampleType.INT16, "AL_LOKI_quadriphonic"
    ),
    BufferFormat.MONO_FLOAT32: _format_spec(
        _F.FORMAT_MONO_FLOAT32, 1, SampleType.FLOAT32, "AL_EXT_float32"
    ),
    BufferFormat.STEREO_FLOAT32: _format_spec(
        _F.FORMAT_STEREO_FLOAT32, 2, SampleType.FLOAT32, "AL_EXT_float32"
    ),
    BufferFormat.MONO_FLOAT64: _format_spec(
        _F.FORMAT_MONO_DOUBLE_EXT, 1, SampleType.FLOAT64, "AL_EXT_double"
    ),
    BufferFormat.STEREO_FLOAT64: _format_spec(
        _F.FORMAT_STEREO_DOUBLE_EXT, 2, SampleType.FLOAT64, "AL_EXT_double"
    ),
    BufferFormat.MONO_MULAW: _format_spec(
        _F.FORMAT_MONO_MULAW_EXT,
        1,
        None,
        ("AL_EXT_MULAW", "AL_EXT_MULAW_MCFORMATS"),
    ),
    BufferFormat.STEREO_MULAW: _format_spec(
        _F.FORMAT_STEREO_MULAW_EXT,
        2,
        None,
        ("AL_EXT_MULAW", "AL_EXT_MULAW_MCFORMATS"),
    ),
    BufferFormat.MONO_ALAW: _format_spec(
        _F.FORMAT_MONO_ALAW_EXT, 1, None, "AL_EXT_ALAW"
    ),
    BufferFormat.STEREO_ALAW: _format_spec(
        _F.FORMAT_STEREO_ALAW_EXT, 2, None, "AL_EXT_ALAW"
    ),
}


def _add_formats(
    formats: tuple[
        tuple[BufferFormat, bindings.enums.ALFormat, int, SampleType | None], ...
    ],
    extension: str,
    *,
    dependencies: tuple[str, ...] = (),
) -> None:
    for managed, native, channels, sample_type in formats:
        _BUFFER_FORMAT_SPECS[managed] = _format_spec(
            native, channels, sample_type, extension, dependencies=dependencies
        )


_add_formats(
    (
        (BufferFormat.QUAD_UINT8, _F.FORMAT_QUAD8, 4, SampleType.UINT8),
        (BufferFormat.QUAD_INT16, _F.FORMAT_QUAD16, 4, SampleType.INT16),
        (BufferFormat.QUAD_FLOAT32, _F.FORMAT_QUAD32, 4, SampleType.FLOAT32),
        (BufferFormat.REAR_UINT8, _F.FORMAT_REAR8, 2, SampleType.UINT8),
        (BufferFormat.REAR_INT16, _F.FORMAT_REAR16, 2, SampleType.INT16),
        (BufferFormat.REAR_FLOAT32, _F.FORMAT_REAR32, 2, SampleType.FLOAT32),
        (BufferFormat.SURROUND_5_1_UINT8, _F.FORMAT_51CHN8, 6, SampleType.UINT8),
        (BufferFormat.SURROUND_5_1_INT16, _F.FORMAT_51CHN16, 6, SampleType.INT16),
        (BufferFormat.SURROUND_5_1_FLOAT32, _F.FORMAT_51CHN32, 6, SampleType.FLOAT32),
        (BufferFormat.SURROUND_6_1_UINT8, _F.FORMAT_61CHN8, 7, SampleType.UINT8),
        (BufferFormat.SURROUND_6_1_INT16, _F.FORMAT_61CHN16, 7, SampleType.INT16),
        (BufferFormat.SURROUND_6_1_FLOAT32, _F.FORMAT_61CHN32, 7, SampleType.FLOAT32),
        (BufferFormat.SURROUND_7_1_UINT8, _F.FORMAT_71CHN8, 8, SampleType.UINT8),
        (BufferFormat.SURROUND_7_1_INT16, _F.FORMAT_71CHN16, 8, SampleType.INT16),
        (BufferFormat.SURROUND_7_1_FLOAT32, _F.FORMAT_71CHN32, 8, SampleType.FLOAT32),
    ),
    "AL_EXT_MCFORMATS",
)
_add_formats(
    (
        (BufferFormat.QUAD_MULAW, _F.FORMAT_QUAD_MULAW, 4, None),
        (BufferFormat.REAR_MULAW, _F.FORMAT_REAR_MULAW, 2, None),
        (BufferFormat.SURROUND_5_1_MULAW, _F.FORMAT_51CHN_MULAW, 6, None),
        (BufferFormat.SURROUND_6_1_MULAW, _F.FORMAT_61CHN_MULAW, 7, None),
        (BufferFormat.SURROUND_7_1_MULAW, _F.FORMAT_71CHN_MULAW, 8, None),
    ),
    "AL_EXT_MULAW_MCFORMATS",
)
_add_formats(
    (
        (BufferFormat.MONO_IMA4, _F.FORMAT_MONO_IMA4, 1, None),
        (BufferFormat.STEREO_IMA4, _F.FORMAT_STEREO_IMA4, 2, None),
    ),
    "AL_EXT_IMA4",
)
_add_formats(
    (
        (BufferFormat.MONO_MSADPCM, _F.FORMAT_MONO_MSADPCM_SOFT, 1, None),
        (BufferFormat.STEREO_MSADPCM, _F.FORMAT_STEREO_MSADPCM_SOFT, 2, None),
    ),
    "AL_SOFT_MSADPCM",
)

for _managed, _native, _dimensions, _sample_type in (
    (BufferFormat.BFORMAT_2D_UINT8, _F.FORMAT_BFORMAT2D_8, 2, SampleType.UINT8),
    (BufferFormat.BFORMAT_2D_INT16, _F.FORMAT_BFORMAT2D_16, 2, SampleType.INT16),
    (
        BufferFormat.BFORMAT_2D_FLOAT32,
        _F.FORMAT_BFORMAT2D_FLOAT32,
        2,
        SampleType.FLOAT32,
    ),
    (BufferFormat.BFORMAT_3D_UINT8, _F.FORMAT_BFORMAT3D_8, 3, SampleType.UINT8),
    (BufferFormat.BFORMAT_3D_INT16, _F.FORMAT_BFORMAT3D_16, 3, SampleType.INT16),
    (
        BufferFormat.BFORMAT_3D_FLOAT32,
        _F.FORMAT_BFORMAT3D_FLOAT32,
        3,
        SampleType.FLOAT32,
    ),
):
    _BUFFER_FORMAT_SPECS[_managed] = _format_spec(
        _native, None, _sample_type, "AL_EXT_BFORMAT", _dimensions
    )
for _managed, _native, _dimensions in (
    (BufferFormat.BFORMAT_2D_MULAW, _F.FORMAT_BFORMAT2D_MULAW, 2),
    (BufferFormat.BFORMAT_3D_MULAW, _F.FORMAT_BFORMAT3D_MULAW, 3),
):
    _BUFFER_FORMAT_SPECS[_managed] = _format_spec(
        _native, None, None, "AL_EXT_MULAW_BFORMAT", _dimensions
    )

_add_formats(
    (
        (BufferFormat.UHJ_2_UINT8, _F.FORMAT_UHJ2CHN8_SOFT, 2, SampleType.UINT8),
        (BufferFormat.UHJ_2_INT16, _F.FORMAT_UHJ2CHN16_SOFT, 2, SampleType.INT16),
        (
            BufferFormat.UHJ_2_FLOAT32,
            _F.FORMAT_UHJ2CHN_FLOAT32_SOFT,
            2,
            SampleType.FLOAT32,
        ),
        (BufferFormat.UHJ_3_UINT8, _F.FORMAT_UHJ3CHN8_SOFT, 3, SampleType.UINT8),
        (BufferFormat.UHJ_3_INT16, _F.FORMAT_UHJ3CHN16_SOFT, 3, SampleType.INT16),
        (
            BufferFormat.UHJ_3_FLOAT32,
            _F.FORMAT_UHJ3CHN_FLOAT32_SOFT,
            3,
            SampleType.FLOAT32,
        ),
        (BufferFormat.UHJ_4_UINT8, _F.FORMAT_UHJ4CHN8_SOFT, 4, SampleType.UINT8),
        (BufferFormat.UHJ_4_INT16, _F.FORMAT_UHJ4CHN16_SOFT, 4, SampleType.INT16),
        (
            BufferFormat.UHJ_4_FLOAT32,
            _F.FORMAT_UHJ4CHN_FLOAT32_SOFT,
            4,
            SampleType.FLOAT32,
        ),
    ),
    "AL_SOFT_UHJ",
)
_add_formats(
    (
        (BufferFormat.UHJ_2_MULAW, _F.FORMAT_UHJ2CHN_MULAW_SOFT, 2, None),
        (BufferFormat.UHJ_3_MULAW, _F.FORMAT_UHJ3CHN_MULAW_SOFT, 3, None),
        (BufferFormat.UHJ_4_MULAW, _F.FORMAT_UHJ4CHN_MULAW_SOFT, 4, None),
    ),
    "AL_SOFT_UHJ_ex",
    dependencies=("AL_SOFT_UHJ", "AL_EXT_MULAW"),
)
_add_formats(
    (
        (BufferFormat.UHJ_2_ALAW, _F.FORMAT_UHJ2CHN_ALAW_SOFT, 2, None),
        (BufferFormat.UHJ_3_ALAW, _F.FORMAT_UHJ3CHN_ALAW_SOFT, 3, None),
        (BufferFormat.UHJ_4_ALAW, _F.FORMAT_UHJ4CHN_ALAW_SOFT, 4, None),
    ),
    "AL_SOFT_UHJ_ex",
    dependencies=("AL_SOFT_UHJ", "AL_EXT_ALAW"),
)
_add_formats(
    ((BufferFormat.UHJ_2_IMA4, _F.FORMAT_UHJ2CHN_IMA4_SOFT, 2, None),),
    "AL_SOFT_UHJ_ex",
    dependencies=("AL_SOFT_UHJ", "AL_EXT_IMA4"),
)
_add_formats(
    ((BufferFormat.UHJ_2_MSADPCM, _F.FORMAT_UHJ2CHN_MSADPCM_SOFT, 2, None),),
    "AL_SOFT_UHJ_ex",
    dependencies=("AL_SOFT_UHJ", "AL_SOFT_MSADPCM"),
)

_IMA4_FORMATS = {
    BufferFormat.MONO_IMA4,
    BufferFormat.STEREO_IMA4,
    BufferFormat.UHJ_2_IMA4,
}
_MSADPCM_FORMATS = {
    BufferFormat.MONO_MSADPCM,
    BufferFormat.STEREO_MSADPCM,
    BufferFormat.UHJ_2_MSADPCM,
}
_BLOCK_FORMATS = _IMA4_FORMATS | _MSADPCM_FORMATS
_ONE_BYTE_FORMATS = {
    format for format in BufferFormat if format.value.endswith(("_mulaw", "_alaw"))
}


def _buffer_channels(format: BufferFormat, ambisonic_order: int) -> int | None:
    spec = _BUFFER_FORMAT_SPECS[format]
    if spec.ambisonic_dimensions == 2:
        return ambisonic_order * 2 + 1
    if spec.ambisonic_dimensions == 3:
        return (ambisonic_order + 1) ** 2
    return spec.channels


@dataclass(frozen=True, slots=True)
class BufferInfo:
    """Format and length information for an extension-format buffer."""

    format: BufferFormat
    channels: int
    sample_rate: int
    frame_count: int
    byte_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.format, BufferFormat):
            raise TypeError("format must be a BufferFormat")
        for name, value in (
            ("channels", self.channels),
            ("sample_rate", self.sample_rate),
            ("frame_count", self.frame_count),
            ("byte_count", self.byte_count),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
            if value <= 0:
                raise ValueError(f"{name} must be positive")

    @property
    def duration_seconds(self) -> float:
        """Duration in decoded source-audio seconds."""

        return self.frame_count / self.sample_rate

    @property
    def sample_type(self) -> SampleType | None:
        """Fixed-width decoded sample type, or ``None`` for encoded data."""

        return self.format.sample_type


@dataclass(frozen=True, slots=True)
class BufferData:
    """Immutable payload for an exact OpenAL buffer format.

    ``frame_count`` is the decoded sample-frame count. It keeps duration,
    seeking, and queue accounting deterministic for compressed formats whose
    byte size does not reveal their decoded length.
    """

    samples: bytes
    format: BufferFormat
    sample_rate: int
    frame_count: int
    channels: int | None = None
    block_alignment: int | None = None
    ambisonic_order: int = 1
    ambisonic_layout: AmbisonicLayout | None = None
    ambisonic_scaling: AmbisonicScaling | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.samples, (bytes, bytearray, memoryview)):
            raise TypeError("samples must be bytes-like")
        samples = bytes(self.samples)
        if not samples:
            raise ValueError("samples cannot be empty")
        if not isinstance(self.format, BufferFormat):
            raise TypeError("format must be a BufferFormat")
        if isinstance(self.sample_rate, bool) or not isinstance(self.sample_rate, int):
            raise TypeError("sample_rate must be an integer")
        if self.sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        if isinstance(self.frame_count, bool) or not isinstance(self.frame_count, int):
            raise TypeError("frame_count must be an integer")
        if self.frame_count <= 0:
            raise ValueError("frame_count must be positive")
        if isinstance(self.ambisonic_order, bool) or not isinstance(
            self.ambisonic_order, int
        ):
            raise TypeError("ambisonic_order must be an integer")
        if not 1 <= self.ambisonic_order <= 14:
            raise ValueError("ambisonic_order must be between 1 and 14")

        spec = _BUFFER_FORMAT_SPECS[self.format]
        is_ambisonic = spec.ambisonic_dimensions is not None
        if not is_ambisonic and self.ambisonic_order != 1:
            raise ValueError("ambisonic_order requires a B-format format")
        if self.ambisonic_layout is not None and not isinstance(
            self.ambisonic_layout, AmbisonicLayout
        ):
            raise TypeError("ambisonic_layout must be an AmbisonicLayout or None")
        if self.ambisonic_scaling is not None and not isinstance(
            self.ambisonic_scaling, AmbisonicScaling
        ):
            raise TypeError("ambisonic_scaling must be an AmbisonicScaling or None")
        if not is_ambisonic and (
            self.ambisonic_layout is not None or self.ambisonic_scaling is not None
        ):
            raise ValueError("ambisonic options require a B-format format")
        if self.ambisonic_order > 3:
            if self.ambisonic_layout is not AmbisonicLayout.ACN:
                raise ValueError("ambisonic orders above 3 require ACN layout")
            if self.ambisonic_scaling not in (
                AmbisonicScaling.SN3D,
                AmbisonicScaling.N3D,
            ):
                raise ValueError("ambisonic orders above 3 require SN3D or N3D scaling")

        inferred_channels = _buffer_channels(self.format, self.ambisonic_order)
        if self.channels is None:
            if inferred_channels is None:
                raise ValueError("channels is required for WAVE and Vorbis buffers")
            channels = inferred_channels
        else:
            if isinstance(self.channels, bool) or not isinstance(self.channels, int):
                raise TypeError("channels must be an integer or None")
            if self.channels <= 0:
                raise ValueError("channels must be positive")
            if inferred_channels is not None and self.channels != inferred_channels:
                raise ValueError(
                    f"channels must be {inferred_channels} for {self.format.value}"
                )
            channels = self.channels

        if self.block_alignment is not None:
            if isinstance(self.block_alignment, bool) or not isinstance(
                self.block_alignment, int
            ):
                raise TypeError("block_alignment must be an integer or None")
            if self.block_alignment <= 0:
                raise ValueError("block_alignment must be positive")
            if self.format not in _BLOCK_FORMATS:
                raise ValueError("block_alignment requires an IMA4 or MSADPCM format")

        if self.format in _BLOCK_FORMATS:
            alignment = self.block_alignment or (
                65 if self.format in _IMA4_FORMATS else 64
            )
            if self.format in _IMA4_FORMATS and alignment % 8 != 1:
                raise ValueError("IMA4 block_alignment must be a multiple of 8 plus 1")
            if self.format in _MSADPCM_FORMATS and alignment % 2:
                raise ValueError("MSADPCM block_alignment must be even")
            if self.frame_count % alignment:
                raise ValueError("frame_count must contain complete compressed blocks")
            bytes_per_channel = (
                (alignment - 1) // 2 + 4
                if self.format in _IMA4_FORMATS
                else (alignment - 2) // 2 + 7
            )
            expected = self.frame_count // alignment * channels * bytes_per_channel
            if len(samples) != expected:
                raise ValueError(
                    f"samples must contain exactly {self.frame_count} decoded "
                    f"frames ({expected} encoded bytes)"
                )

        sample_width = (
            spec.sample_type.byte_width
            if spec.sample_type is not None
            else (1 if self.format in _ONE_BYTE_FORMATS else None)
        )
        if sample_width is not None:
            expected = self.frame_count * channels * sample_width
            if len(samples) != expected:
                raise ValueError(
                    f"samples must contain exactly {self.frame_count} complete frames "
                    f"({expected} bytes)"
                )
        object.__setattr__(self, "samples", samples)
        object.__setattr__(self, "channels", channels)

    @property
    def info(self) -> BufferInfo:
        """Format and decoded-length information for this payload."""

        assert self.channels is not None
        return BufferInfo(
            format=self.format,
            channels=self.channels,
            sample_rate=self.sample_rate,
            frame_count=self.frame_count,
            byte_count=len(self.samples),
        )

    @property
    def duration(self) -> float:
        """Decoded duration in seconds."""

        return self.frame_count / self.sample_rate


def _validate_pcm_layout(
    channels: int, sample_rate: int, sample_type: SampleType
) -> int:
    """Validate a managed PCM layout and return its frame width in bytes."""

    if isinstance(channels, bool) or not isinstance(channels, int):
        raise TypeError("channels must be an integer")
    if channels not in (1, 2, 4, 6, 7, 8):
        raise ValueError("channels must be 1, 2, 4, 6, 7, or 8")
    if isinstance(sample_rate, bool) or not isinstance(sample_rate, int):
        raise TypeError("sample_rate must be an integer")
    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    if not isinstance(sample_type, SampleType):
        raise TypeError("sample_type must be a SampleType")
    if sample_type is SampleType.FLOAT64 and channels not in (1, 2):
        raise ValueError("64-bit floating-point PCM must be mono or stereo")
    return channels * sample_type.byte_width


@dataclass(frozen=True, slots=True)
class SoundInfo:
    """Format and length information for immutable PCM audio.

    Attributes:
        channels: Number of interleaved channels in a standard mono, stereo,
            quad, 5.1, 6.1, or 7.1 layout.
        sample_rate: Number of sample frames per second.
        sample_type: Representation used by each channel sample.
        frame_count: Number of interleaved sample frames; always positive.
        duration_seconds: Duration on the source-audio timeline.
        sample_width_bytes: Number of bytes used by one channel sample.
        bit_depth: Number of bits used by one channel sample.
        frame_width_bytes: Number of bytes used by one interleaved frame.
        byte_count: Total number of sample bytes.

    Raises:
        TypeError: A constructor argument has the wrong type.
        ValueError: The channel count, sample rate, or frame count is unsupported.
    """

    channels: int
    sample_rate: int
    sample_type: SampleType
    frame_count: int

    def __post_init__(self) -> None:
        _validate_pcm_layout(self.channels, self.sample_rate, self.sample_type)
        if isinstance(self.frame_count, bool) or not isinstance(self.frame_count, int):
            raise TypeError("frame_count must be an integer")
        if self.frame_count <= 0:
            raise ValueError("frame_count must be positive")

    @property
    def duration_seconds(self) -> float:
        """Duration in source-audio seconds."""

        return self.frame_count / self.sample_rate

    @property
    def sample_width_bytes(self) -> int:
        """Number of bytes used by one channel sample."""

        return self.sample_type.byte_width

    @property
    def bit_depth(self) -> int:
        """Number of bits used by one channel sample."""

        return self.sample_width_bytes * 8

    @property
    def frame_width_bytes(self) -> int:
        """Number of bytes used by one interleaved sample frame."""

        return self.channels * self.sample_width_bytes

    @property
    def byte_count(self) -> int:
        """Total number of PCM data bytes."""

        return self.frame_count * self.frame_width_bytes


@dataclass(frozen=True, slots=True)
class PCM:
    """Immutable, interleaved PCM sample data ready to upload.

    The constructor copies any bytes-like input to immutable ``bytes``.
    The byte count must contain a whole number of frames.

    Attributes:
        samples: Interleaved sample bytes.
        channels: Number of interleaved channels in a standard mono, stereo,
            quad, 5.1, 6.1, or 7.1 layout.
        sample_rate: Positive number of sample frames per second.
        sample_type: Representation used by each channel sample.
        frame_count: Number of complete sample frames.
        duration: Duration in seconds on the source-audio timeline.
        info: Format and length information as a [`SoundInfo`][pyalsoft.SoundInfo].

    Raises:
        TypeError: A constructor argument has the wrong type.
        ValueError: The samples or format do not describe supported, complete PCM.
    """

    samples: bytes
    channels: int
    sample_rate: int
    sample_type: SampleType = SampleType.INT16

    def __post_init__(self) -> None:
        if not isinstance(self.samples, (bytes, bytearray, memoryview)):
            raise TypeError("samples must be bytes-like")
        samples = bytes(self.samples)
        if not samples:
            raise ValueError("samples cannot be empty")
        frame_width = _validate_pcm_layout(
            self.channels, self.sample_rate, self.sample_type
        )
        if len(samples) % frame_width:
            raise ValueError("samples must contain a whole number of frames")
        object.__setattr__(self, "samples", samples)

    @property
    def frame_count(self) -> int:
        """Number of sample frames in this PCM value."""

        return len(self.samples) // (self.channels * self.sample_type.byte_width)

    @property
    def duration(self) -> float:
        """Duration of this PCM value in seconds."""

        return self.frame_count / self.sample_rate

    @property
    def info(self) -> SoundInfo:
        """Format and length information for this PCM value."""

        return SoundInfo(
            channels=self.channels,
            sample_rate=self.sample_rate,
            sample_type=self.sample_type,
            frame_count=self.frame_count,
        )


def _as_stereo(pcm: PCM) -> PCM:
    """Return *pcm* with mono sample frames duplicated into stereo."""

    if pcm.channels == 2:
        return pcm
    if pcm.channels != 1:
        raise ValueError("direct_channels requires mono or stereo source audio")
    sample_width = pcm.sample_type.byte_width
    frame_width = sample_width * 2
    samples = bytearray(len(pcm.samples) * 2)
    for byte_offset in range(sample_width):
        channel_bytes = pcm.samples[byte_offset::sample_width]
        samples[byte_offset::frame_width] = channel_bytes
        samples[byte_offset + sample_width :: frame_width] = channel_bytes
    return PCM(
        bytes(samples),
        channels=2,
        sample_rate=pcm.sample_rate,
        sample_type=pcm.sample_type,
    )
