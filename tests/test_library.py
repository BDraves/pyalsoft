"""Tests for dynamic OpenAL function loading and extension resolution."""

from __future__ import annotations

import ctypes
import ctypes.util
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import pytest

from pyalsoft.bindings import _library as runtime
from pyalsoft.bindings._generated.functions import PROTOTYPES
from pyalsoft.bindings._library import (
    ExtensionUnavailableError,
    ForeignFunction,
    FunctionUnavailableError,
    LibraryNotFoundError,
    OpenALLibrary,
)


class FakePrototype:
    def __init__(
        self,
        direct: ForeignFunction | None = None,
        addresses: Mapping[int, ForeignFunction] | None = None,
    ) -> None:
        self.direct = direct
        self.addresses = dict(addresses or {})
        self.sources: list[object] = []

    def __call__(self, source: object) -> ForeignFunction:
        self.sources.append(source)
        if isinstance(source, tuple):
            if self.direct is None:
                raise AttributeError("missing direct export")
            return self.direct
        if not isinstance(source, int) or source not in self.addresses:
            raise ValueError("unknown function address")
        return self.addresses[source]


def make_library(
    monkeypatch: pytest.MonkeyPatch,
    prototypes: Mapping[str, FakePrototype],
) -> OpenALLibrary:
    monkeypatch.setattr(
        runtime,
        "_load_shared_library",
        lambda _path: (object(), "fake-openal"),
    )
    for name, prototype in prototypes.items():
        monkeypatch.setitem(PROTOTYPES, name, prototype)
    return OpenALLibrary()


@pytest.mark.parametrize(
    ("system", "fallback"),
    [
        ("win32", "OpenAL32.dll"),
        ("darwin", "libopenal.1.dylib"),
        ("linux", "libopenal.so.1"),
    ],
)
def test_candidates_prefer_the_bundled_runtime(
    monkeypatch: pytest.MonkeyPatch,
    system: str,
    fallback: str,
) -> None:
    monkeypatch.setattr(sys, "platform", system)
    monkeypatch.setattr(runtime, "_bundled_library_path", lambda: "bundled-library")
    monkeypatch.setattr(
        ctypes.util,
        "find_library",
        lambda _name: "system-library",
    )

    candidates = runtime._library_candidates()
    assert candidates[0] == "bundled-library"
    assert "system-library" in candidates
    assert fallback in candidates


def test_bundled_runtime_is_found_in_editable_distribution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source" / "pyalsoft" / "bindings" / "_library.py"
    installed = tmp_path / "site-packages" / "pyalsoft" / "_native" / "soft_oal.dll"
    installed.parent.mkdir(parents=True)
    installed.touch()

    class EditableDistribution:
        def locate_file(self, path: Path) -> Path:
            assert path == Path("pyalsoft/_native/soft_oal.dll")
            return tmp_path / "site-packages" / path

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(runtime, "__file__", str(source))
    monkeypatch.setattr(runtime, "distribution", lambda _name: EditableDistribution())

    assert runtime._bundled_library_path() == str(installed.resolve())


def test_core_export_is_typed_and_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    def open_device(_name: object) -> int:
        return 0x1234

    prototype = FakePrototype(open_device)
    library = make_library(monkeypatch, {"alcOpenDevice": prototype})

    assert library.get_function("alcOpenDevice") is open_device
    assert library.alcOpenDevice is open_device
    assert len(prototype.sources) == 1


def test_generated_string_list_wrapper_preserves_embedded_nuls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    encoded = ctypes.create_string_buffer(b"Speakers\0USB Headset\0\0")
    library = make_library(monkeypatch, {})

    def get_pointer_return_function(
        name: str, *, device: object | None = None
    ) -> ForeignFunction:
        assert name == "alcGetString"
        assert device is None

        def get_strings(_device: object, _parameter: object) -> int:
            return ctypes.addressof(encoded)

        return get_strings

    monkeypatch.setattr(
        library,
        "_get_pointer_return_function",
        get_pointer_return_function,
    )

    assert library.alc.get_strings(None, 0x1013) == ("Speakers", "USB Headset")


def test_al_extension_uses_current_context(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []

    def extension_function() -> None:
        pass

    def is_present(extension: object) -> bytes:
        calls.append(("check", extension))
        return b"\x01"

    def get_proc_address(name: object) -> int:
        calls.append(("resolve", name))
        return 0x2000

    prototypes = {
        "alcGetCurrentContext": FakePrototype(lambda: 0x1000),
        "alIsExtensionPresent": FakePrototype(is_present),
        "alGetProcAddress": FakePrototype(get_proc_address),
        "alDeferUpdatesSOFT": FakePrototype(addresses={0x2000: extension_function}),
    }
    library = make_library(monkeypatch, prototypes)

    assert library.get_function("alDeferUpdatesSOFT") is extension_function
    assert calls == [
        ("check", b"AL_SOFT_deferred_updates"),
        ("resolve", b"alDeferUpdatesSOFT"),
    ]


def test_alc_extension_uses_supplied_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, object, object]] = []

    def extension_function(_device: object) -> None:
        pass

    def is_present(device: object, extension: object) -> bytes:
        calls.append(("check", device, extension))
        return b"\x01"

    def get_proc_address(device: object, name: object) -> int:
        calls.append(("resolve", device, name))
        return 0x3000

    prototypes = {
        "alcIsExtensionPresent": FakePrototype(is_present),
        "alcGetProcAddress": FakePrototype(get_proc_address),
        "alcDevicePauseSOFT": FakePrototype(addresses={0x3000: extension_function}),
    }
    library = make_library(monkeypatch, prototypes)
    device = ctypes.c_void_p(0x1234)

    assert (
        library.get_function("alcDevicePauseSOFT", device=device) is extension_function
    )
    assert calls == [
        ("check", device, b"ALC_SOFT_pause_device"),
        ("resolve", device, b"alcDevicePauseSOFT"),
    ]


def test_efx_al_command_checks_the_current_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, object, object]] = []

    def extension_function(_count: object, _effects: object) -> None:
        pass

    def is_present(device: object, extension: object) -> bytes:
        calls.append(("check", device, extension))
        return b"\x01"

    def get_proc_address(name: object) -> int:
        calls.append(("resolve", None, name))
        return 0x4000

    prototypes = {
        "alcGetCurrentContext": FakePrototype(lambda: 0x1000),
        "alcGetContextsDevice": FakePrototype(lambda _context: 0x1100),
        "alcIsExtensionPresent": FakePrototype(is_present),
        "alGetProcAddress": FakePrototype(get_proc_address),
        "alGenEffects": FakePrototype(addresses={0x4000: extension_function}),
    }
    library = make_library(monkeypatch, prototypes)

    assert library.get_function("alGenEffects") is extension_function
    assert calls == [
        ("check", 0x1100, b"ALC_EXT_EFX"),
        ("resolve", None, b"alGenEffects"),
    ]


def test_generated_extension_capabilities_are_discoverable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, object]] = []

    def is_present(device: object, extension: object) -> bytes:
        calls.append((device, extension))
        return b"\x01"

    library = make_library(
        monkeypatch,
        {"alcIsExtensionPresent": FakePrototype(is_present)},
    )
    efx = library.extensions.alc_ext_efx

    assert efx is library.extensions["ALC_EXT_EFX"]
    assert efx.name == "ALC_EXT_EFX"
    assert efx.apis == ("al", "alc")
    assert "alGenEffects" in efx.commands
    assert "AL_EFFECT_TYPE" in efx.enums
    assert efx.is_present(0x1000)
    assert calls == [(0x1000, b"ALC_EXT_EFX")]
    with pytest.raises(KeyError, match="not declared"):
        efx.get_function("alGenSources")


def test_direct_context_extension_can_resolve_through_alc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, object, object]] = []

    def extension_function(_context: object) -> int:
        return 0

    def is_present(device: object, extension: object) -> bytes:
        calls.append(("check", device, extension))
        return b"\x01"

    def get_proc_address(device: object, name: object) -> int:
        calls.append(("resolve", device, name))
        return 0x5000

    prototypes = {
        "alcGetCurrentContext": FakePrototype(lambda: None),
        "alcIsExtensionPresent": FakePrototype(is_present),
        "alcGetProcAddress": FakePrototype(get_proc_address),
        "alGetErrorDirect": FakePrototype(addresses={0x5000: extension_function}),
    }
    library = make_library(monkeypatch, prototypes)
    device = ctypes.c_void_p(0x1234)

    assert library.get_function("alGetErrorDirect", device=device) is extension_function
    assert calls == [
        ("check", device, b"AL_EXT_direct_context"),
        ("resolve", device, b"alGetErrorDirect"),
    ]

    calls.clear()
    assert library.al.get_error_direct(0x1000, resolution_device=device) == 0
    assert calls == [
        ("check", device, b"AL_EXT_direct_context"),
        ("resolve", device, b"alGetErrorDirect"),
    ]


def test_unavailable_extension_is_not_resolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prototypes = {
        "alcGetCurrentContext": FakePrototype(lambda: 0x1000),
        "alIsExtensionPresent": FakePrototype(lambda _extension: b"\x00"),
        "alGetProcAddress": FakePrototype(lambda _name: 0x2000),
        "alDeferUpdatesSOFT": FakePrototype(addresses={0x2000: lambda: None}),
    }
    library = make_library(monkeypatch, prototypes)

    with pytest.raises(ExtensionUnavailableError, match="AL_SOFT_deferred_updates"):
        library.get_function("alDeferUpdatesSOFT")
    assert prototypes["alGetProcAddress"].sources == []


def test_unknown_command_has_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    library = make_library(monkeypatch, {})

    with pytest.raises(FunctionUnavailableError, match="unknown OpenAL command"):
        library.get_function("alDefinitelyNotReal")


def test_null_extension_entry_point_has_a_clear_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prototypes = {
        "alcGetCurrentContext": FakePrototype(lambda: 0x1000),
        "alIsExtensionPresent": FakePrototype(lambda _extension: b"\x01"),
        "alGetProcAddress": FakePrototype(lambda _name: None),
    }
    library = make_library(monkeypatch, prototypes)

    with pytest.raises(FunctionUnavailableError, match="null entry point"):
        library.get_function("alDeferUpdatesSOFT")


def test_missing_shared_library_has_a_clear_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_library(_name: str) -> None:
        raise OSError("not found")

    monkeypatch.setattr(runtime, "_library_candidates", lambda: ("missing.dll",))
    monkeypatch.setattr(ctypes, "CDLL", reject_library)

    with pytest.raises(LibraryNotFoundError, match="missing.dll: not found"):
        OpenALLibrary()


def test_python_command_namespace_marshals_arrays(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, ...]] = []

    def generate(count: int, identifiers: object) -> None:
        output = cast(Any, identifiers)
        calls.append(("generate", count))
        for index in range(count):
            output[index] = 20 + index

    def delete(count: int, identifiers: object) -> None:
        values = cast(Any, identifiers)
        calls.append(("delete", count, tuple(values[index] for index in range(count))))

    library = make_library(
        monkeypatch,
        {
            "alGenSources": FakePrototype(generate),
            "alDeleteSources": FakePrototype(delete),
        },
    )

    assert library.al is library.al
    assert library.al.gen_sources(3) == (20, 21, 22)
    library.al.delete_sources([20, 21, 22])
    assert calls == [("generate", 3), ("delete", 3, (20, 21, 22))]


def test_python_command_namespace_returns_output_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def get_three(
        _source: int,
        _parameter: int,
        first: object,
        second: object,
        third: object,
    ) -> None:
        cast(Any, first)[0] = 1.0
        cast(Any, second)[0] = 2.0
        cast(Any, third)[0] = 3.0

    def get_vector(_source: int, _parameter: int, values: object) -> None:
        output = cast(Any, values)
        for index, value in enumerate((4.0, 5.0, 6.0)):
            output[index] = value

    library = make_library(
        monkeypatch,
        {
            "alGetSource3f": FakePrototype(get_three),
            "alGetSourcefv": FakePrototype(get_vector),
        },
    )

    assert library.al.get_source3f(7, 0x1004) == (1.0, 2.0, 3.0)
    assert library.al.get_sourcefv(7, 0x1004, result_size=3) == (4.0, 5.0, 6.0)


def test_python_command_namespace_infers_buffer_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[int, bytes, int]] = []

    def buffer_data(
        _buffer: int,
        format_: int,
        data: object,
        size: int,
        sample_rate: int,
    ) -> None:
        values = cast(Any, data)
        captured.append((format_, bytes(values[:size]), sample_rate))

    library = make_library(
        monkeypatch,
        {"alBufferData": FakePrototype(buffer_data)},
    )
    library.al.buffer_data(4, 0x1101, b"\x01\x02\x03\x04", 44_100)

    assert captured == [(0x1101, b"\x01\x02\x03\x04", 44_100)]


def test_python_command_namespace_encodes_strings_and_attribute_lists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, ...]] = []

    def open_device(name: object) -> int:
        calls.append(("open", name))
        return 0x1000

    def create_context(device: object, attributes: object) -> int:
        values = cast(Any, attributes)
        calls.append(("context", device, tuple(values[index] for index in range(3))))
        return 0x2000

    library = make_library(
        monkeypatch,
        {
            "alcOpenDevice": FakePrototype(open_device),
            "alcCreateContext": FakePrototype(create_context),
        },
    )

    device = library.alc.open_device("Default")
    assert device == 0x1000
    assert library.alc.create_context(device, [0x1007, 48_000]) == 0x2000
    assert calls == [
        ("open", b"Default"),
        ("context", 0x1000, (0x1007, 48_000, 0)),
    ]


def test_generated_object_properties_dispatch_and_convert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, ...]] = []

    def set_float(source: int, parameter: int, value: float) -> None:
        calls.append(("set-float", source, parameter, value))

    def get_float(source: int, parameter: int, output: object) -> None:
        calls.append(("get-float", source, parameter))
        cast(Any, output)[0] = 1.5

    def set_integer(source: int, parameter: int, value: int) -> None:
        calls.append(("set-int", source, parameter, value))

    def get_integer(source: int, parameter: int, output: object) -> None:
        calls.append(("get-int", source, parameter))
        cast(Any, output)[0] = 42

    library = make_library(
        monkeypatch,
        {
            "alSourcef": FakePrototype(set_float),
            "alGetSourcef": FakePrototype(get_float),
            "alSourcei": FakePrototype(set_integer),
            "alGetSourcei": FakePrototype(get_integer),
        },
    )
    source = library.al.source(7)

    source.pitch = 1.25
    assert source.pitch == 1.5
    related_buffer = source.buffer
    assert related_buffer is not None
    assert related_buffer.identifier == 42
    source.buffer = library.al.buffer(9)
    with pytest.raises(AttributeError, match="read-only"):
        source.state = 0  # type: ignore[assignment]

    assert calls == [
        ("set-float", 7, 0x1003, 1.25),
        ("get-float", 7, 0x1003),
        ("get-int", 7, 0x1009),
        ("set-int", 7, 0x1009, 9),
    ]
