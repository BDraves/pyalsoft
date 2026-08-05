"""Integration tests against a real platform OpenAL runtime."""

from __future__ import annotations

import os
from typing import cast

import pytest

from pyalsoft import bindings

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("PYALSOFT_RUN_SYSTEM_TESTS") != "1",
        reason="set PYALSOFT_RUN_SYSTEM_TESTS=1 to test a native OpenAL runtime",
    ),
]


def test_system_runtime_can_create_a_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Load OpenAL Soft and exercise its device and context lifecycle."""

    monkeypatch.setenv("ALSOFT_DRIVERS", "null")
    path = os.environ.get("PYALSOFT_TEST_LIBRARY")
    library = bindings.load(path)

    device = cast(object | None, library.alcOpenDevice(None))
    assert device, f"could not open the null device with {library.library_name!r}"

    context: object | None = None
    try:
        context = cast(object | None, library.alcCreateContext(device, None))
        assert context, f"could not create a context with {library.library_name!r}"
        assert library.alcMakeContextCurrent(context)

        version = cast(bytes | None, library.alGetString(bindings.AL_VERSION))
        assert version
    finally:
        library.alcMakeContextCurrent(None)
        if context:
            library.alcDestroyContext(context)
        assert library.alcCloseDevice(device)
