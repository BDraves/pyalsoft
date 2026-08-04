"""Smoke tests for generated constants and registry metadata."""

from pyalsoft._generated import constants
from pyalsoft._generated.registry import (
    API_SETS,
    COMMANDS,
    DEFINES,
    ENUMS,
    REGISTRY_COMMENTS,
    REGISTRY_NOTES,
    TYPES,
)
from pyalsoft._generated.semantics import (
    COMMAND_WRAPPERS,
    ENUM_GROUPS,
    OBJECT_PROPERTIES,
    OBJECT_PROPERTIES_BY_KEY,
)

import pyalsoft


def test_representative_constants() -> None:
    assert constants.AL_NONE == 0
    assert constants.AL_INVALID_ENUM == 0xA002
    assert constants.AL_ILLEGAL_ENUM == constants.AL_INVALID_ENUM
    assert constants.AL_REVERB_MIN_DENSITY == 0.0
    assert constants.AL_MIN_METERS_PER_UNIT > 0.0
    assert constants.ALC_EXT_CAPTURE == 1
    assert constants.AL_EXT_FOLDBACK_NAME == "AL_EXT_FOLDBACK"


def test_generated_metadata_is_populated() -> None:
    assert len(TYPES) >= 49
    assert len(DEFINES) >= 10
    assert sum(item.python_value is not None for item in DEFINES) == 5
    assert len(ENUMS) >= 825
    assert len(COMMANDS) >= 332
    assert len(API_SETS) >= 62
    assert REGISTRY_COMMENTS[0].startswith("Copyright")
    assert len(REGISTRY_NOTES) >= 17

    buffer_data = next(
        command for command in COMMANDS if command.name == "alBufferData"
    )
    assert buffer_data.export == "al"
    assert buffer_data.return_type == "void"
    assert [parameter.name for parameter in buffer_data.parameters] == [
        "buffer",
        "format",
        "data",
        "size",
        "samplerate",
    ]


def test_generated_metadata_includes_xml_annotations() -> None:
    relative = next(item for item in ENUMS if item.name == "AL_SOURCE_RELATIVE")
    assert relative.comment == "Relative source."
    assert relative.comments == ("Specifies if the source uses relative coordinates.",)
    assert relative.properties[0].objects == ("source",)
    assert relative.properties[0].value_types == ("ALboolean",)
    assert relative.properties[0].default == "AL_FALSE"

    get_error = next(item for item in COMMANDS if item.name == "alGetError")
    assert get_error.return_group == "ErrorCode"
    assert get_error.comments

    direct_context = next(
        item for item in API_SETS if item.name == "AL_EXT_direct_context"
    )
    assert any(
        requirement.depends == "ALC_EXT_EFX"
        for requirement in direct_context.requirements
    )

    foldback_name = next(
        item for item in DEFINES if item.name == "AL_EXT_FOLDBACK_NAME"
    )
    assert foldback_name.replacement == '"AL_EXT_FOLDBACK"'
    assert foldback_name.python_value == "AL_EXT_FOLDBACK"


def test_public_package_exposes_registry_and_define_constants() -> None:
    assert pyalsoft.registry.ENUMS is ENUMS
    assert pyalsoft.registry.__name__ == "pyalsoft.registry"
    assert pyalsoft.ALC_VERSION_0_1 == 1


def test_generated_semantic_model_covers_commands_and_properties() -> None:
    assert len(COMMAND_WRAPPERS) == len(COMMANDS)
    assert len(ENUM_GROUPS) >= 100
    assert len(OBJECT_PROPERTIES) >= 184

    pitch = OBJECT_PROPERTIES_BY_KEY[("source", "AL_PITCH")]
    assert pitch.python_name == "pitch"
    assert pitch.getter == "alGetSourcef"
    assert pitch.setter == "alSourcef"

    state = OBJECT_PROPERTIES_BY_KEY[("source", "AL_SOURCE_STATE")]
    assert state.getter == "alGetSourcei"
    assert state.setter is None

    special = OBJECT_PROPERTIES_BY_KEY[("source", "AL_AUXILIARY_SEND_FILTER")]
    assert special.getter is None


def test_public_package_exposes_semantic_enums_and_objects() -> None:
    assert pyalsoft.ALSourceState.PLAYING == pyalsoft.AL_PLAYING
    assert pyalsoft.ALSourceFloat3.DIRECTION == pyalsoft.AL_DIRECTION
    assert "DIRECTION" not in pyalsoft.ALListenerFloat3.__members__
    assert pyalsoft.registry.OBJECT_PROPERTIES is OBJECT_PROPERTIES
    assert pyalsoft.Source.object_name == "source"
    assert not hasattr(pyalsoft.Listener, "direction")
