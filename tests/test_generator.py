"""Tests for the language-neutral registry parser and generator."""

import xml.etree.ElementTree as ET

import pytest

from tools.generate_bindings import (
    DEFAULT_DOCS_OUTPUT,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_REGISTRY,
    DEFAULT_SOURCE,
    RegistryError,
    _validate_supported_xml,
    load_semantic_overrides,
    load_source_info,
    parse_registry,
    render_documentation,
    render_outputs,
    verify_registry,
)


def test_current_registry_has_expected_surface() -> None:
    registry = parse_registry()

    assert len(registry.types) >= 49
    assert len(registry.defines) >= 10
    assert len(registry.enums) >= 825
    assert len(registry.commands) >= 332
    assert len(registry.api_sets) >= 62
    assert len(registry.comments) == 1
    assert len(registry.notes) >= 17

    command_names = {command.name for command in registry.commands}
    api_set_names = {api_set.name for api_set in registry.api_sets}
    assert {"alBufferData", "alGetProcAddress", "alcGetProcAddress"} <= command_names
    assert {"AL_VERSION_1_1", "ALC_EXT_EFX", "ALC_SOFT_loopback"} <= api_set_names


def test_current_registry_preserves_rich_xml_metadata() -> None:
    registry = parse_registry()

    boolean = next(item for item in registry.types if item.name == "ALboolean")
    assert boolean.comment == "8-bit boolean"

    relative = next(
        item for item in registry.enums if item.name == "AL_SOURCE_RELATIVE"
    )
    assert relative.comment == "Relative source."
    assert relative.comments == ("Specifies if the source uses relative coordinates.",)
    assert relative.properties[0].objects == ("source",)
    assert relative.properties[0].value_types == ("ALboolean",)
    assert relative.properties[0].groups == ("Boolean",)
    assert relative.properties[0].default == "AL_FALSE"

    special = next(item for item in registry.enums if item.name == "AL_NONE")
    assert special.block_group == "SpecialNumbers"

    get_error = next(item for item in registry.commands if item.name == "alGetError")
    assert get_error.return_group == "ErrorCode"
    assert get_error.comments == (
        "Obtain the first error generated in the AL context since the last call "
        "to this function.",
    )

    direct_is_source = next(
        item for item in registry.commands if item.name == "alIsSourceDirect"
    )
    assert direct_is_source.comment == "Verify an ID is for a valid source."
    assert direct_is_source.command_attribute == direct_is_source.comment

    direct_context = next(
        item for item in registry.api_sets if item.name == "AL_EXT_direct_context"
    )
    assert "ALC_EXT_EFX" in {
        requirement.depends
        for requirement in direct_context.requirements
        if requirement.depends is not None
    }


def test_all_current_xml_annotations_are_accounted_for() -> None:
    registry = parse_registry()
    parameters = [
        parameter for command in registry.commands for parameter in command.parameters
    ]
    requirements = [
        requirement
        for api_set in registry.api_sets
        for requirement in api_set.requirements
    ]

    assert sum(item.comment is not None for item in registry.types) == 31
    assert sum(item.block_group is not None for item in registry.enums) == 6
    assert sum(item.comment is not None for item in registry.enums) == 72
    assert sum(len(item.comments) for item in registry.enums) == 27
    assert sum(len(item.properties) for item in registry.enums) == 184
    assert sum(item.deprecated is not None for item in registry.enums) == 4
    assert sum(item.length is not None for item in parameters) == 62
    assert sum(item.group is not None for item in parameters) == 234
    assert sum(item.object_class is not None for item in parameters) == 189
    assert sum(item.return_group is not None for item in registry.commands) == 4
    assert sum(item.comment is not None for item in registry.commands) == 33
    assert sum(len(item.comments) for item in registry.commands) == 9
    assert sum(item.command_attribute is not None for item in registry.commands) == 1
    assert sum(item.comment is not None for item in requirements) == 77
    assert sum(item.depends is not None for item in requirements) == 13
    assert sum(len(item.comments) for item in registry.api_sets) == 2


def test_parser_rejects_unhandled_xml_metadata() -> None:
    root = ET.fromstring(
        '<registry future="meaningful"><types namespace="AL" /></registry>'
    )
    with pytest.raises(RegistryError, match="unsupported attribute.*future"):
        _validate_supported_xml(root)


def test_repeated_api_set_blocks_are_merged() -> None:
    registry = parse_registry()

    ima4 = [api_set for api_set in registry.api_sets if api_set.name == "AL_EXT_IMA4"]
    assert len(ima4) == 1
    assert len(ima4[0].requirements) == 1


def test_committed_generated_files_are_current() -> None:
    source = load_source_info(DEFAULT_SOURCE)
    digest = verify_registry(DEFAULT_REGISTRY, source)
    registry = parse_registry(DEFAULT_REGISTRY)

    for name, expected in render_outputs(registry, source, digest).items():
        generated = DEFAULT_OUTPUT_DIR / name
        assert generated.read_text(encoding="utf-8") == expected

    assert DEFAULT_DOCS_OUTPUT.read_text(encoding="utf-8") == render_documentation(
        registry,
        source,
        digest,
        load_semantic_overrides(),
    )
