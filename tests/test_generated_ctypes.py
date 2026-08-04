"""Smoke tests for the generated ctypes declarations."""

import ctypes

import pyalsoft
from pyalsoft._generated import types
from pyalsoft._generated.functions import (
    COMMAND_EXTENSIONS,
    EXTENSION_APIS,
    PROTOTYPES,
)


def test_representative_base_and_opaque_types() -> None:
    assert types.ALuint is ctypes.c_uint
    assert types.ALfloat is ctypes.c_float
    assert types.ALint64SOFT is ctypes.c_int64
    assert issubclass(types.ALCdevice, ctypes.Structure)
    assert issubclass(types.ALCcontext, ctypes.Structure)


def test_representative_command_prototype() -> None:
    prototype = PROTOTYPES["alBufferData"]

    assert prototype._restype_ is None
    assert tuple(prototype._argtypes_) == (
        types.ALuint,
        types.ALenum,
        ctypes.c_void_p,
        types.ALsizei,
        types.ALsizei,
    )


def test_callbacks_and_extension_metadata_are_concrete() -> None:
    assert callable(types.ALEVENTPROCSOFT)
    assert types.ALEVENTPROCSOFT._restype_ is None
    assert COMMAND_EXTENSIONS["alGenEffects"] == "ALC_EXT_EFX"
    assert EXTENSION_APIS["ALC_EXT_EFX"] == ("al", "alc")


def test_public_package_reexports_generated_values_and_types() -> None:
    assert pyalsoft.AL_FORMAT_MONO16 == 0x1101
    assert pyalsoft.ALuint is ctypes.c_uint
