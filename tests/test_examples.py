"""Tests for example-specific import and generated-audio behavior."""

from __future__ import annotations

import runpy
from pathlib import Path

import pytest

from examples.loop_points import SAMPLE_RATE as LOOP_SAMPLE_RATE
from examples.loop_points import SEGMENT_SECONDS, looping_tone_pcm
from examples.play_sine import sine_pcm
from examples.stream_sine import sine_chunks

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


def test_move_sine_can_be_loaded_by_file_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.syspath_prepend(str(EXAMPLES))

    namespace = runpy.run_path(
        str(EXAMPLES / "move_sine.py"),
        run_name="move_sine_example",
    )

    assert callable(namespace["move_sine"])


def test_streamed_sine_chunks_match_buffered_generation() -> None:
    arguments = {"frequency": 440.0, "duration": 0.05}

    assert b"".join(sine_chunks(**arguments)) == sine_pcm(**arguments)


def test_loop_point_example_selects_its_middle_segment() -> None:
    pcm, loop_points = looping_tone_pcm()
    segment_frames = round(LOOP_SAMPLE_RATE * SEGMENT_SECONDS)

    assert pcm.frame_count == segment_frames * 3
    assert loop_points == (segment_frames, segment_frames * 2)
