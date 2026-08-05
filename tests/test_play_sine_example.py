"""Tests for the end-to-end sine-wave example."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

import pytest

from examples.move_sine import move_sine
from examples.play_sine import SAMPLE_RATE, play_sine, sine_pcm
from pyalsoft import bindings

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
WINDOWS_RUNTIME = (
    ROOT / "vendor" / "openal-soft" / "runtime" / "win_amd64" / "soft_oal.dll"
)


def test_move_sine_can_be_loaded_by_file_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.syspath_prepend(str(EXAMPLES))

    namespace = runpy.run_path(
        str(EXAMPLES / "move_sine.py"),
        run_name="move_sine_example",
    )

    assert callable(namespace["move_sine"])


def test_sine_pcm_is_mono_16_bit_audio() -> None:
    duration = 0.05

    pcm = sine_pcm(frequency=440.0, duration=duration)

    assert len(pcm) == round(SAMPLE_RATE * duration) * 2
    assert pcm[:2] == b"\x00\x00"
    assert any(pcm)


@pytest.mark.parametrize("name", ["frequency", "duration"])
def test_sine_pcm_rejects_nonpositive_inputs(name: str) -> None:
    arguments = {"frequency": 440.0, "duration": 0.05, name: 0.0}

    with pytest.raises(ValueError, match=rf"{name} must be positive"):
        sine_pcm(**arguments)


@pytest.mark.integration
@pytest.mark.skipif(
    sys.platform != "win32" or not WINDOWS_RUNTIME.is_file(),
    reason="requires the checked-in Windows OpenAL Soft runtime",
)
def test_play_sine_with_bundled_null_driver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALSOFT_DRIVERS", "null")
    library = bindings.load(WINDOWS_RUNTIME)

    play_sine(library, duration=0.05)
    move_sine(library, duration=0.05)
