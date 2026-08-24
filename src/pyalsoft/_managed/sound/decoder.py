"""Private signature detection and native static-audio decoding."""

from __future__ import annotations

import ctypes
import os
import platform
import struct
import sys
from dataclasses import dataclass
from enum import IntEnum
from importlib.metadata import PackageNotFoundError, distribution
from os import PathLike
from pathlib import Path
from threading import Lock
from typing import Any

from pyalsoft._managed.audio import PCM, AudioPath, SampleType, SoundInfo
from pyalsoft._managed.errors import AudioFileError


class _Codec(IntEnum):
    WAV = 1
    FLAC = 2
    MP3 = 3
    VORBIS = 4


class _NativeSampleFormat(IntEnum):
    UINT8 = 1
    INT16 = 2
    FLOAT32 = 3


_SAMPLE_TYPES = {
    _NativeSampleFormat.UINT8: SampleType.UINT8,
    _NativeSampleFormat.INT16: SampleType.INT16,
    _NativeSampleFormat.FLOAT32: SampleType.FLOAT32,
}


class _NativeInfo(ctypes.Structure):
    _fields_ = [
        ("channels", ctypes.c_uint32),
        ("sample_rate", ctypes.c_uint32),
        ("sample_format", ctypes.c_uint32),
        ("frame_count", ctypes.c_uint64),
    ]


class _NativeError(ctypes.Structure):
    _fields_ = [
        ("code", ctypes.c_int32),
        ("message", ctypes.c_char * 256),
    ]


@dataclass(frozen=True, slots=True)
class _DetectedAudio:
    codec: _Codec
    sample_format: _NativeSampleFormat


def _decoder_library_name() -> str:
    if sys.platform == "win32":
        return "pyalsoft_decoder.dll"
    if sys.platform == "darwin":
        return "libpyalsoft_decoder.dylib"
    if sys.platform.startswith("linux"):
        return "libpyalsoft_decoder.so"
    raise AudioFileError(f"static audio decoding is unsupported on {sys.platform}")


def _decoder_library_path() -> Path | None:
    library_name = _decoder_library_name()
    package_path = Path(__file__).parents[2] / "_native" / library_name
    if package_path.is_file():
        return package_path.resolve()

    # An explicit local native build is usable without modifying the source
    # package. Installed distributions never depend on this development path.
    project_root = Path(__file__).parents[4]
    machine = platform.machine().casefold()
    architecture = "aarch64" if machine in {"arm64", "aarch64"} else "x86_64"
    if sys.platform == "win32":
        runtime_id = "win_amd64"
    elif sys.platform == "darwin":
        runtime_id = f"macos_{'arm64' if architecture == 'aarch64' else 'x86_64'}"
    else:
        runtime_id = f"linux_{architecture}"
    staged_path = project_root / "build" / "native" / runtime_id / library_name
    if (project_root / "pyproject.toml").is_file() and staged_path.is_file():
        return staged_path.resolve()
    try:
        installed_path = Path(
            str(
                distribution("pyalsoft").locate_file(
                    Path("pyalsoft") / "_native" / library_name
                )
            )
        )
    except PackageNotFoundError:
        return None
    return installed_path.resolve() if installed_path.is_file() else None


class _NativeDecoder:
    """Typed access to PyALSoft's private decoder C ABI."""

    def __init__(self, path: Path) -> None:
        try:
            library = ctypes.CDLL(os.fspath(path))
        except OSError as error:
            raise AudioFileError(
                f"could not load bundled audio decoder {path}: {error}"
            ) from error
        self._library: Any = library
        self._library.pyalsoft_decoder_probe.argtypes = [
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_int32,
            ctypes.c_int32,
            ctypes.POINTER(_NativeInfo),
            ctypes.POINTER(_NativeError),
        ]
        self._library.pyalsoft_decoder_probe.restype = ctypes.c_int32
        self._library.pyalsoft_decoder_decode.argtypes = [
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_int32,
            ctypes.c_int32,
            ctypes.POINTER(_NativeInfo),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_size_t),
            ctypes.POINTER(_NativeError),
        ]
        self._library.pyalsoft_decoder_decode.restype = ctypes.c_int32
        self._library.pyalsoft_decoder_free.argtypes = [ctypes.c_void_p]
        self._library.pyalsoft_decoder_free.restype = None

    @staticmethod
    def _input(data: bytes) -> tuple[Any, ctypes.c_void_p]:
        buffer = ctypes.create_string_buffer(data)
        return buffer, ctypes.cast(buffer, ctypes.c_void_p)

    @staticmethod
    def _raise_error(path: Path, native_error: _NativeError) -> None:
        message = (
            bytes(native_error.message).split(b"\0", 1)[0].decode("utf-8", "replace")
        )
        detail = message or f"native decoder error {native_error.code}"
        raise AudioFileError(f"could not decode audio file {path}: {detail}")

    @staticmethod
    def _sound_info(info: _NativeInfo, path: Path) -> SoundInfo:
        try:
            sample_format = _NativeSampleFormat(info.sample_format)
            return SoundInfo(
                channels=info.channels,
                sample_rate=info.sample_rate,
                sample_type=_SAMPLE_TYPES[sample_format],
                frame_count=info.frame_count,
            )
        except (TypeError, ValueError) as error:
            raise AudioFileError(f"unsupported audio file {path}: {error}") from error

    def probe(self, data: bytes, detected: _DetectedAudio, path: Path) -> SoundInfo:
        input_buffer, input_pointer = self._input(data)
        info = _NativeInfo()
        error = _NativeError()
        result = self._library.pyalsoft_decoder_probe(
            input_pointer,
            len(data),
            int(detected.codec),
            int(detected.sample_format),
            ctypes.byref(info),
            ctypes.byref(error),
        )
        del input_buffer
        if result != 0:
            self._raise_error(path, error)
        return self._sound_info(info, path)

    def decode(self, data: bytes, detected: _DetectedAudio, path: Path) -> PCM:
        input_buffer, input_pointer = self._input(data)
        info = _NativeInfo()
        output = ctypes.c_void_p()
        output_size = ctypes.c_size_t()
        error = _NativeError()
        result = self._library.pyalsoft_decoder_decode(
            input_pointer,
            len(data),
            int(detected.codec),
            int(detected.sample_format),
            ctypes.byref(info),
            ctypes.byref(output),
            ctypes.byref(output_size),
            ctypes.byref(error),
        )
        del input_buffer
        if result != 0:
            self._raise_error(path, error)
        try:
            samples = ctypes.string_at(output, output_size.value)
        finally:
            self._library.pyalsoft_decoder_free(output)
        sound_info = self._sound_info(info, path)
        try:
            return PCM(
                samples=samples,
                channels=sound_info.channels,
                sample_rate=sound_info.sample_rate,
                sample_type=sound_info.sample_type,
            )
        except (TypeError, ValueError) as error:
            raise AudioFileError(f"unsupported audio file {path}: {error}") from error


_native_decoder: _NativeDecoder | None = None
_native_decoder_lock = Lock()


def _get_native_decoder() -> _NativeDecoder:
    global _native_decoder
    with _native_decoder_lock:
        if _native_decoder is None:
            path = _decoder_library_path()
            if path is None:
                raise AudioFileError(
                    "the bundled static audio decoder is missing; reinstall PyALSoft "
                    "for this platform"
                )
            _native_decoder = _NativeDecoder(path)
        return _native_decoder


def _wav_sample_format(data: bytes, path: Path) -> _NativeSampleFormat:
    declared_size = int.from_bytes(data[4:8], "little") + 8
    if declared_size > len(data):
        raise AudioFileError(
            f"truncated WAV audio file {path}: expected {declared_size} bytes, "
            f"read {len(data)}"
        )
    offset = 12
    sample_format: _NativeSampleFormat | None = None
    while offset + 8 <= len(data):
        chunk_id = data[offset : offset + 4]
        chunk_size = int.from_bytes(data[offset + 4 : offset + 8], "little")
        chunk_start = offset + 8
        chunk_end = chunk_start + chunk_size
        if chunk_end > len(data):
            raise AudioFileError(f"truncated WAV chunk in audio file {path}")
        if chunk_id == b"fmt ":
            if chunk_size < 16:
                raise AudioFileError(f"invalid WAV format chunk in audio file {path}")
            format_tag, _, _, _, _, bits = struct.unpack_from(
                "<HHIIHH", data, chunk_start
            )
            if format_tag == 0xFFFE:
                if chunk_size < 40:
                    raise AudioFileError(
                        f"invalid extensible WAV format chunk in audio file {path}"
                    )
                format_tag = int.from_bytes(
                    data[chunk_start + 24 : chunk_start + 26], "little"
                )
            if format_tag == 1:
                try:
                    sample_format = {
                        8: _NativeSampleFormat.UINT8,
                        16: _NativeSampleFormat.INT16,
                        24: _NativeSampleFormat.FLOAT32,
                        32: _NativeSampleFormat.FLOAT32,
                    }[bits]
                except KeyError as error:
                    raise AudioFileError(
                        f"unsupported {bits}-bit PCM WAV file: {path}"
                    ) from error
            if format_tag == 3:
                if bits in (32, 64):
                    sample_format = _NativeSampleFormat.FLOAT32
                else:
                    raise AudioFileError(
                        f"unsupported {bits}-bit floating-point WAV file: {path}"
                    )
            elif format_tag != 1:
                raise AudioFileError(f"unsupported compressed WAV audio file {path}")
        offset = chunk_end + (chunk_size & 1)
    if sample_format is not None:
        return sample_format
    raise AudioFileError(f"WAV audio file has no format chunk: {path}")


def _flac_sample_format(data: bytes, path: Path) -> _NativeSampleFormat:
    if len(data) < 42:
        raise AudioFileError(f"truncated FLAC audio file: {path}")
    block_type = data[4] & 0x7F
    block_size = int.from_bytes(data[5:8], "big")
    if block_type != 0 or block_size != 34 or len(data) < 8 + block_size:
        raise AudioFileError(f"invalid FLAC STREAMINFO block: {path}")
    packed = int.from_bytes(data[18:26], "big")
    bits_per_sample = ((packed >> 36) & 0x1F) + 1
    if not 4 <= bits_per_sample <= 32:
        raise AudioFileError(
            f"unsupported {bits_per_sample}-bit FLAC audio file: {path}"
        )
    return (
        _NativeSampleFormat.INT16
        if bits_per_sample <= 16
        else _NativeSampleFormat.FLOAT32
    )


def _looks_like_mp3_frame(data: bytes, offset: int) -> bool:
    if offset + 4 > len(data):
        return False
    header = int.from_bytes(data[offset : offset + 4], "big")
    return (
        (header >> 21) & 0x7FF == 0x7FF
        and (header >> 19) & 0x3 != 0x1
        and (header >> 17) & 0x3 != 0
        and (header >> 12) & 0xF not in (0, 0xF)
        and (header >> 10) & 0x3 != 0x3
    )


def _detect_ogg_codec(data: bytes, path: Path) -> _Codec:
    offset = 0
    packet = bytearray()
    while offset < len(data):
        if offset + 27 > len(data) or data[offset : offset + 4] != b"OggS":
            raise AudioFileError(f"truncated or invalid Ogg audio file: {path}")
        segment_count = data[offset + 26]
        table_start = offset + 27
        payload_start = table_start + segment_count
        if payload_start > len(data):
            raise AudioFileError(f"truncated Ogg segment table: {path}")
        segments = data[table_start:payload_start]
        payload_end = payload_start + sum(segments)
        if payload_end > len(data):
            raise AudioFileError(f"truncated Ogg page payload: {path}")
        payload_offset = payload_start
        for segment_size in segments:
            segment_end = payload_offset + segment_size
            packet.extend(data[payload_offset:segment_end])
            payload_offset = segment_end
            if segment_size < 255:
                if packet.startswith(b"OpusHead"):
                    raise AudioFileError(f"Ogg Opus audio is not supported: {path}")
                if packet.startswith(b"\x01vorbis"):
                    return _Codec.VORBIS
                raise AudioFileError(f"unknown or unsupported Ogg codec: {path}")
        offset = payload_end
    raise AudioFileError(f"truncated Ogg identification packet: {path}")


def _detect_audio(data: bytes, path: Path) -> _DetectedAudio:
    if data.startswith(b"RIFF") and data[8:12] == b"WAVE":
        return _DetectedAudio(_Codec.WAV, _wav_sample_format(data, path))
    if data.startswith(b"fLaC"):
        return _DetectedAudio(_Codec.FLAC, _flac_sample_format(data, path))
    if data.startswith(b"OggS"):
        return _DetectedAudio(
            _detect_ogg_codec(data, path), _NativeSampleFormat.FLOAT32
        )

    frame_offset = 0
    has_id3 = data.startswith(b"ID3")
    if has_id3 and len(data) >= 10:
        size_bytes = data[6:10]
        if all(value < 0x80 for value in size_bytes):
            frame_offset = 10 + sum(
                value << shift
                for value, shift in zip(size_bytes, (21, 14, 7, 0), strict=True)
            )
    search_end = min(len(data) - 3, frame_offset + 65_536)
    if has_id3 or any(
        _looks_like_mp3_frame(data, offset)
        for offset in range(frame_offset, max(frame_offset, search_end))
    ):
        return _DetectedAudio(_Codec.MP3, _NativeSampleFormat.FLOAT32)

    suffix = path.suffix or "<none>"
    raise AudioFileError(
        f"unsupported or unrecognized audio file {path} (suffix {suffix!r})"
    )


def _read_audio_file(path: Path) -> tuple[bytes, _DetectedAudio]:
    try:
        data = path.read_bytes()
    except OSError as error:
        raise AudioFileError(f"could not read audio file {path}: {error}") from error
    detected = _detect_audio(data, path)
    return data, detected


def _normalize_audio_path(path: AudioPath) -> Path:
    if not isinstance(path, (str, PathLike)):
        raise TypeError("sound must be a path to a supported audio file")
    return Path(path).expanduser().resolve()


def _decode_audio(path: Path) -> PCM:
    data, detected = _read_audio_file(path)
    return _get_native_decoder().decode(data, detected, path)


def load_audio(path: AudioPath) -> PCM:
    """Decode a supported static audio file into immutable PCM audio.

    WAV, FLAC, MP3, and Ogg Vorbis files are detected from their contents and
    decoded completely in memory. This function performs no playback-device
    work. Source sample rates are retained.

    Args:
        path: Path to a supported audio file.

    Returns:
        Complete decoded PCM audio.

    Raises:
        TypeError: ``path`` is not string or path-like.
        AudioFileError: The file cannot be read, decoded, or represented by the
            supported static-audio layouts.
    """

    return _decode_audio(_normalize_audio_path(path))


def get_sound_info(path: AudioPath) -> SoundInfo:
    """Read decoded format and length information without opening a device.

    The returned sample type and bit depth describe the decoded PCM that
    PyALSoft will upload, rather than a compressed bitrate.

    Args:
        path: Path to a supported WAV, FLAC, MP3, or Ogg Vorbis file.

    Returns:
        Decoded channel layout, sample rate, sample type, and frame count.

    Raises:
        TypeError: ``path`` is not string or path-like.
        AudioFileError: The file cannot be read, decoded, or represented by the
            supported static-audio layouts.
    """

    normalized = _normalize_audio_path(path)
    data, detected = _read_audio_file(normalized)
    return _get_native_decoder().probe(data, detected, normalized)
