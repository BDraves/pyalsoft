"""Fixtures shared by managed convenience-playback tests."""

from collections.abc import Iterator

import pytest

import pyalsoft._managed.sound.runtime as runtime_module
from pyalsoft import open_playback, shutdown
from tests._support.managed_backend import FakeLibrary, as_library


@pytest.fixture
def default_library(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeLibrary]:
    shutdown()
    library = FakeLibrary()
    monkeypatch.setattr(
        runtime_module,
        "open_playback",
        lambda: open_playback(library=as_library(library)),
    )
    yield library
    shutdown()
