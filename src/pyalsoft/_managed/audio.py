"""PCM formats, sample data, and source-audio metadata."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from os import PathLike

type AudioPath = str | PathLike[str]


class SampleType(Enum):
    """PCM sample representations supported by the managed API.

    Attributes:
        UINT8: Unsigned 8-bit samples, with silence at 128.
        INT16: Signed 16-bit samples, with silence at 0.
    """

    UINT8 = "uint8"
    INT16 = "int16"

    @property
    def byte_width(self) -> int:
        """Number of bytes used by one channel sample."""

        return 1 if self is SampleType.UINT8 else 2


def _validate_pcm_layout(
    channels: int, sample_rate: int, sample_type: SampleType
) -> int:
    """Validate a managed PCM layout and return its frame width in bytes."""

    if isinstance(channels, bool) or not isinstance(channels, int):
        raise TypeError("channels must be an integer")
    if channels not in (1, 2):
        raise ValueError("channels must be 1 or 2")
    if isinstance(sample_rate, bool) or not isinstance(sample_rate, int):
        raise TypeError("sample_rate must be an integer")
    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    if not isinstance(sample_type, SampleType):
        raise TypeError("sample_type must be a SampleType")
    return channels * sample_type.byte_width


@dataclass(frozen=True, slots=True)
class SoundInfo:
    """Format and length information for immutable PCM audio.

    Attributes:
        channels: Number of interleaved channels, either 1 (mono) or 2 (stereo).
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
        channels: Number of channels, either 1 (mono) or 2 (stereo).
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
