"""WAV header inspection and PCM decoding."""

from __future__ import annotations

import wave
from os import PathLike
from pathlib import Path

from pyalsoft._managed.audio import PCM, AudioPath, SampleType, SoundInfo
from pyalsoft._managed.errors import AudioFileError


def _wave_info(source: wave.Wave_read, path: Path) -> SoundInfo:
    """Validate a WAV header and return its supported PCM layout."""

    if source.getcomptype() != "NONE":
        raise AudioFileError(f"unsupported compressed WAV file: {path}")
    sample_width = source.getsampwidth()
    try:
        sample_type = {
            1: SampleType.UINT8,
            2: SampleType.INT16,
        }[sample_width]
    except KeyError as error:
        raise AudioFileError(
            f"unsupported {sample_width * 8}-bit WAV file: {path}"
        ) from error

    try:
        return SoundInfo(
            channels=source.getnchannels(),
            sample_rate=source.getframerate(),
            sample_type=sample_type,
            frame_count=source.getnframes(),
        )
    except (TypeError, ValueError) as error:
        raise AudioFileError(f"unsupported WAV file {path}: {error}") from error


def _read_wave(path: Path) -> PCM:
    try:
        with wave.open(str(path), "rb") as source:
            info = _wave_info(source, path)
            samples = source.readframes(info.frame_count)
    except (EOFError, OSError, wave.Error) as error:
        raise AudioFileError(f"could not read WAV file {path}: {error}") from error

    if len(samples) != info.byte_count:
        raise AudioFileError(
            f"truncated WAV file {path}: expected {info.byte_count} sample bytes, "
            f"read {len(samples)}"
        )

    try:
        return PCM(
            samples=samples,
            channels=info.channels,
            sample_rate=info.sample_rate,
            sample_type=info.sample_type,
        )
    except (TypeError, ValueError) as error:
        raise AudioFileError(f"unsupported WAV file {path}: {error}") from error


def get_sound_info(path: AudioPath) -> SoundInfo:
    """Read WAV format and length information without opening an audio device.

    The managed file API accepts uncompressed mono or stereo WAV files containing
    unsigned 8-bit or signed 16-bit PCM and at least one complete frame.

    Args:
        path: Path to the WAV file. User-directory markers are expanded and the
            path is resolved before reading.

    Returns:
        Validated channel layout, sample rate, sample type, and length.

    Raises:
        TypeError: ``path`` is not string or path-like.
        AudioFileError: The file cannot be read or uses an unsupported WAV format.
    """

    if not isinstance(path, (str, PathLike)):
        raise TypeError("sound must be a path to a WAV file")
    normalized = Path(path).expanduser().resolve()
    try:
        with wave.open(str(normalized), "rb") as source:
            return _wave_info(source, normalized)
    except (EOFError, OSError, wave.Error) as error:
        raise AudioFileError(
            f"could not read WAV file {normalized}: {error}"
        ) from error
