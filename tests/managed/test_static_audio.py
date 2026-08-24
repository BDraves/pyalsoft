"""Tests for signature-driven WAV, FLAC, MP3, and Ogg Vorbis decoding."""

from __future__ import annotations

import shutil
import struct
import wave
from pathlib import Path

import pytest

from pyalsoft import (
    AudioBackendError,
    AudioFileError,
    DirectChannelsMode,
    SampleType,
    clear_sound_cache,
    get_sound_cache_info,
    get_sound_info,
    load_audio,
    open_playback,
    play,
    release,
    upload,
)
from pyalsoft._managed.sound import decoder as decoder_module
from tests._support.managed_backend import FakeLibrary, as_library

FIXTURES = Path(__file__).parents[1] / "fixtures" / "audio"


def _write_extensible_wave(path: Path, channels: int, channel_mask: int) -> None:
    bits = 16
    sample_rate = 8_000
    block_alignment = channels * bits // 8
    subformat = bytes.fromhex("0100000000001000800000aa00389b71")
    format_chunk = (
        struct.pack(
            "<HHIIHH",
            0xFFFE,
            channels,
            sample_rate,
            sample_rate * block_alignment,
            block_alignment,
            bits,
        )
        + struct.pack("<HHI", 22, bits, channel_mask)
        + subformat
    )
    samples = bytes(block_alignment * 2)
    riff_size = 4 + 8 + len(format_chunk) + 8 + len(samples)
    path.write_bytes(
        b"RIFF"
        + struct.pack("<I", riff_size)
        + b"WAVEfmt "
        + struct.pack("<I", len(format_chunk))
        + format_chunk
        + b"data"
        + struct.pack("<I", len(samples))
        + samples
    )


@pytest.mark.parametrize(
    ("filename", "channels", "sample_rate", "sample_type", "frame_count"),
    [
        ("tone-s16.flac", 1, 8_000, SampleType.INT16, 320),
        ("tone-s24.flac", 2, 12_000, SampleType.FLOAT32, 480),
        ("tone.mp3", 1, 22_050, SampleType.FLOAT32, 2_205),
        ("tone.ogg", 2, 16_000, SampleType.FLOAT32, 1_280),
    ],
)
def test_compressed_audio_metadata_and_samples(
    filename: str,
    channels: int,
    sample_rate: int,
    sample_type: SampleType,
    frame_count: int,
) -> None:
    path = FIXTURES / filename

    info = get_sound_info(path)
    pcm = load_audio(path)

    assert info == pcm.info
    assert info.channels == channels
    assert info.sample_rate == sample_rate
    assert info.sample_type is sample_type
    assert info.frame_count == frame_count
    assert any(pcm.samples)


def test_sixteen_bit_flac_decodes_losslessly() -> None:
    flac = load_audio(FIXTURES / "tone-s16.flac")
    wave_pcm = load_audio(FIXTURES / "tone-s16.wav")

    assert flac == wave_pcm


def test_flac_metadata_probe_does_not_load_the_native_decoder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_native_probe() -> None:
        raise AssertionError("FLAC metadata probe loaded the native decoder")

    monkeypatch.setattr(decoder_module, "_get_native_decoder", fail_native_probe)

    assert get_sound_info(FIXTURES / "tone-s16.flac").frame_count == 320


def test_canonical_multichannel_wave_layout_is_accepted(tmp_path: Path) -> None:
    path = tmp_path / "quad.wav"
    _write_extensible_wave(path, channels=4, channel_mask=0x0033)

    assert get_sound_info(path).channels == 4
    assert load_audio(path).frame_count == 2


@pytest.mark.parametrize("channel_mask", [0, 0x000F])
def test_ambiguous_multichannel_wave_layout_is_rejected(
    tmp_path: Path, channel_mask: int
) -> None:
    path = tmp_path / "ambiguous.wav"
    _write_extensible_wave(path, channels=4, channel_mask=channel_mask)

    with pytest.raises(AudioFileError, match="channel mask"):
        load_audio(path)


def test_non_extensible_multichannel_wave_layout_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "unmapped.wav"
    with wave.open(str(path), "wb") as output:
        output.setnchannels(4)
        output.setsampwidth(2)
        output.setframerate(8_000)
        output.writeframes(bytes(16))

    with pytest.raises(AudioFileError, match="extensible channel mask"):
        load_audio(path)


def test_decoded_audio_size_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(decoder_module, "_MAX_AUDIO_BYTES", 2_000)

    with pytest.raises(AudioFileError, match="configured size limit"):
        load_audio(FIXTURES / "tone.mp3")


@pytest.mark.parametrize("suffix", [".wrong", ""])
def test_content_detection_does_not_require_a_matching_suffix(
    tmp_path: Path, suffix: str
) -> None:
    path = tmp_path / f"asset{suffix}"
    shutil.copyfile(FIXTURES / "tone.ogg", path)

    assert get_sound_info(path).sample_rate == 16_000


def test_id3_prefixed_mp3_is_detected_from_content() -> None:
    path = FIXTURES / "tone.mp3"

    assert path.read_bytes().startswith(b"ID3")
    assert get_sound_info(path).sample_type is SampleType.FLOAT32


@pytest.mark.parametrize(
    ("filename", "message"),
    [
        ("unsupported-opus.ogg", "Ogg Opus"),
        ("unsupported-6ch.flac", "mono or stereo"),
        ("unsupported-6ch.ogg", "mono or stereo"),
    ],
)
def test_unsupported_compressed_layouts_are_clear(filename: str, message: str) -> None:
    with pytest.raises(AudioFileError, match=message):
        load_audio(FIXTURES / filename)


@pytest.mark.parametrize("contents", [b"", b"not audio", b"OggS unknown codec"])
def test_unknown_or_invalid_content_is_rejected(
    tmp_path: Path, contents: bytes
) -> None:
    path = tmp_path / "sound.mp3"
    path.write_bytes(contents)

    with pytest.raises(AudioFileError):
        load_audio(path)


def test_unknown_ogg_codec_has_a_specific_error(tmp_path: Path) -> None:
    path = tmp_path / "unknown.ogg"
    page_header = (
        b"OggS" + b"\0\x02" + bytes(8) + b"test" + bytes(4) + bytes(4) + b"\x01\x04"
    )
    path.write_bytes(page_header + b"test")

    with pytest.raises(AudioFileError, match="unknown or unsupported Ogg codec"):
        load_audio(path)


@pytest.mark.parametrize("filename", ["tone-s16.flac", "tone.mp3", "tone.ogg"])
def test_truncated_compressed_input_is_rejected(tmp_path: Path, filename: str) -> None:
    source = (FIXTURES / filename).read_bytes()
    path = tmp_path / filename
    path.write_bytes(source[: len(source) // 3])

    with pytest.raises(AudioFileError):
        load_audio(path)


def _write_float_wave(path: Path, bits: int, values: tuple[float, ...]) -> None:
    sample_code = "f" if bits == 32 else "d"
    samples = struct.pack(f"<{len(values)}{sample_code}", *values)
    fmt = struct.pack("<HHIIHH", 3, 1, 11_025, 11_025 * bits // 8, bits // 8, bits)
    riff_size = 4 + 8 + len(fmt) + 8 + len(samples)
    path.write_bytes(
        b"RIFF"
        + struct.pack("<I", riff_size)
        + b"WAVEfmt "
        + struct.pack("<I", len(fmt))
        + fmt
        + b"data"
        + struct.pack("<I", len(samples))
        + samples
    )


@pytest.mark.parametrize(
    ("sample_width", "expected_type"),
    [
        (1, SampleType.UINT8),
        (2, SampleType.INT16),
        (3, SampleType.FLOAT32),
        (4, SampleType.FLOAT32),
    ],
)
def test_pcm_wave_depth_policy(
    tmp_path: Path, sample_width: int, expected_type: SampleType
) -> None:
    path = tmp_path / f"pcm-{sample_width * 8}.wav"
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(sample_width)
        output.setframerate(12_345)
        output.writeframes(bytes(sample_width * 4))

    pcm = load_audio(path)

    assert pcm.sample_type is expected_type
    assert pcm.sample_rate == 12_345
    assert pcm.frame_count == 4


@pytest.mark.parametrize("bits", [32, 64])
def test_floating_point_wave_decodes_to_float32(tmp_path: Path, bits: int) -> None:
    path = tmp_path / f"float-{bits}.wav"
    values = (-1.0, -0.5, 0.0, 0.5)
    _write_float_wave(path, bits, values)

    pcm = load_audio(path)

    assert pcm.sample_type is SampleType.FLOAT32
    assert struct.unpack("<4f", pcm.samples) == pytest.approx(values)


def test_audio_with_no_complete_frames_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "empty.wav"
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8_000)

    with pytest.raises(AudioFileError, match="no complete sample frames"):
        load_audio(path)


@pytest.mark.parametrize("filename", ["tone-s16.flac", "tone.mp3", "tone.ogg"])
def test_convenience_play_decodes_before_opening_the_device(
    tmp_path: Path,
    default_library: FakeLibrary,
    filename: str,
) -> None:
    source = (FIXTURES / filename).read_bytes()
    path = tmp_path / filename
    path.write_bytes(source[:16])

    with pytest.raises(AudioFileError):
        play(path)

    assert default_library.alc.current_context is default_library.alc.previous_context


@pytest.mark.parametrize(
    "filename", ["tone-s16.wav", "tone-s16.flac", "tone.mp3", "tone.ogg"]
)
def test_explicit_upload_accepts_every_static_file_format(
    default_library: FakeLibrary, filename: str
) -> None:
    with open_playback(library=as_library(default_library)) as playback:
        clip = upload(playback, FIXTURES / filename, loop_points=(0, 2))

        assert clip.info == get_sound_info(FIXTURES / filename)
        release(playback, clip)


@pytest.mark.parametrize(
    "filename", ["tone-s16.wav", "tone-s16.flac", "tone.mp3", "tone.ogg"]
)
def test_cached_playback_rebuilds_and_restarts_every_static_format(
    default_library: FakeLibrary, filename: str
) -> None:
    del default_library
    path = FIXTURES / filename
    sound = play(path, looping=True)

    sound.seek_frames(1)
    sound.direct_channels = DirectChannelsMode.DROP_UNMATCHED
    sound.restart()

    assert sound.looping
    assert sound.offset_frames == 0
    expected_clips = 2 if sound.info.channels == 1 else 1
    assert get_sound_cache_info().clip_count == expected_clips
    assert clear_sound_cache(path) == expected_clips - 1
    assert get_sound_cache_info().pending_eviction_count == 1
    sound.stop()
    assert get_sound_cache_info().clip_count == 0
    reloaded = play(path)
    assert reloaded.info == sound.info
    reloaded.stop()


def test_float_file_upload_reports_missing_openal_capability(
    default_library: FakeLibrary,
) -> None:
    default_library.al_extensions.discard("AL_EXT_float32")

    with (
        open_playback(library=as_library(default_library)) as playback,
        pytest.raises(AudioBackendError, match="AL_EXT_float32"),
    ):
        upload(playback, FIXTURES / "tone.mp3")
