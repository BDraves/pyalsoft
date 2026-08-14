"""Tests for managed audio values and validation."""

from __future__ import annotations

import pytest

from pyalsoft import (
    AudioBackendError,
    HRTFStatus,
    PlaybackConfig,
    PlaybackDevice,
    PlaybackOpenError,
    bindings,
    get_playback_info,
    list_playback_devices,
    open_playback,
)
from tests._support.managed_backend import FakeLibrary, as_library


def test_devices_are_enumerated_and_consumed_by_open_playback() -> None:
    library = FakeLibrary()

    devices = list_playback_devices(library=as_library(library))

    assert devices == (
        PlaybackDevice("Speakers", is_default=True),
        PlaybackDevice("USB Headset"),
    )
    assert library.alc.string_list_queries == [bindings.ALC_ALL_DEVICES_SPECIFIER]

    with open_playback(
        devices[1],
        config=PlaybackConfig(hrtf=True),
        library=as_library(library),
    ) as playback:
        assert library.alc.opened_device_name == "USB Headset"
        assert library.alc.context_attributes == (bindings.ALC_HRTF_SOFT, 1)
        assert get_playback_info(playback).device_name == "USB Headset"


def test_device_enumeration_falls_back_to_core_specifiers() -> None:
    library = FakeLibrary()
    library.alc.extensions.remove("ALC_ENUMERATE_ALL_EXT")

    devices = list_playback_devices(library=as_library(library))

    assert devices[0].is_default
    assert library.alc.string_list_queries == [bindings.ALC_DEVICE_SPECIFIER]


def test_device_enumeration_reports_alc_errors() -> None:
    library = FakeLibrary()
    library.alc.string_list_error = bindings.ALC_INVALID_ENUM

    with pytest.raises(AudioBackendError, match="ALC INVALID_ENUM"):
        list_playback_devices(library=as_library(library))


def test_device_enumeration_clears_stale_alc_errors() -> None:
    library = FakeLibrary()
    library.alc.error = bindings.ALC_INVALID_VALUE

    assert list_playback_devices(library=as_library(library))


@pytest.mark.parametrize(("enabled", "native"), [(True, 1), (False, 0)])
def test_playback_config_requests_hrtf_when_supported(
    enabled: bool, native: int
) -> None:
    library = FakeLibrary()

    with open_playback(
        config=PlaybackConfig(hrtf=enabled), library=as_library(library)
    ):
        assert library.alc.context_attributes == (bindings.ALC_HRTF_SOFT, native)


def test_open_playback_reports_a_refused_native_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library = FakeLibrary()
    monkeypatch.setattr(library.alc, "open_device", lambda _name: None)

    with pytest.raises(PlaybackOpenError, match="open.*playback device"):
        open_playback(library=as_library(library))

    assert library.alc.destroyed_contexts == []
    assert library.alc.closed_devices == []


def test_open_playback_closes_device_when_context_creation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library = FakeLibrary()

    def refuse_context(
        _device: object,
        _attributes: tuple[int, ...] | None,
    ) -> None:
        return None

    monkeypatch.setattr(library.alc, "create_context", refuse_context)

    with pytest.raises(PlaybackOpenError, match="create.*context"):
        open_playback(library=as_library(library))

    assert library.alc.destroyed_contexts == []
    assert library.alc.closed_devices == [library.alc.device]
    assert library.alc.current_context is library.alc.previous_context


def test_open_playback_destroys_context_when_activation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library = FakeLibrary()

    def refuse_context(context: object | None) -> bool:
        if context is library.alc.context:
            return False
        library.alc.current_context = context
        return True

    monkeypatch.setattr(library.alc, "make_context_current", refuse_context)

    with pytest.raises(PlaybackOpenError, match="make.*context current"):
        open_playback(library=as_library(library))

    assert library.alc.destroyed_contexts == [library.alc.context]
    assert library.alc.closed_devices == [library.alc.device]
    assert library.alc.current_context is library.alc.previous_context


def test_playback_info_reports_backend_result_and_unavailable_hrtf() -> None:
    library = FakeLibrary()
    with open_playback(library=as_library(library)) as playback:
        info = get_playback_info(playback)

        assert info.renderer == "Fake OpenAL Renderer"
        assert info.version == "1.1 Fake OpenAL"
        assert info.hrtf_status is HRTFStatus.ENABLED
        assert info.hrtf_name == "Built-in HRTF"

    library = FakeLibrary()
    library.alc.extensions.remove("ALC_SOFT_HRTF")
    with open_playback(
        config=PlaybackConfig(hrtf=True), library=as_library(library)
    ) as playback:
        info = get_playback_info(playback)

        assert library.alc.context_attributes is None
        assert info.hrtf_status is HRTFStatus.UNAVAILABLE
        assert info.hrtf_name is None


def test_playback_info_preserves_unknown_future_hrtf_status() -> None:
    library = FakeLibrary()
    library.alc.hrtf_status = 0x7FFF

    with open_playback(library=as_library(library)) as playback:
        assert get_playback_info(playback).hrtf_status is HRTFStatus.UNKNOWN


def test_playback_info_reports_alc_errors() -> None:
    library = FakeLibrary()
    library.alc.hrtf_query_error = bindings.ALC_INVALID_ENUM

    with (
        open_playback(library=as_library(library)) as playback,
        pytest.raises(AudioBackendError, match="ALC INVALID_ENUM"),
    ):
        get_playback_info(playback)
