"""Runtime support for generated OpenAL extension capability objects."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import TYPE_CHECKING

from pyalsoft.bindings._generated.registry import API_SETS
from pyalsoft.bindings._specs import ApiSetSpec

if TYPE_CHECKING:
    from pyalsoft.bindings._library import ForeignFunction, OpenALLibrary

_EXTENSIONS = {item.name: item for item in API_SETS if item.kind == "extension"}


class Extension:
    """Capabilities and runtime access for one registry extension.

    Do not construct instances directly. Obtain them from
    ``OpenALLibrary.extensions`` by registry name or generated snake-case
    attribute. Declaration metadata is available without querying the runtime.

    Attributes:
        library: Loaded library used for runtime queries and command resolution.
        name: Registry extension name.
        apis: API namespaces declared by the extension, such as ``"al"`` or
            ``"alc"``.
        commands: Commands declared by all requirements of the extension.
        enums: Enum names declared by all requirements of the extension.
        types: C type names declared by all requirements of the extension.
        dependencies: Non-empty registry dependency expressions.
    """

    def __init__(self, library: OpenALLibrary, spec: ApiSetSpec) -> None:
        self.library = library
        self.spec = spec

    @property
    def name(self) -> str:
        """Registry extension name."""

        return self.spec.name

    @property
    def apis(self) -> tuple[str, ...]:
        """API namespaces declared by this extension."""

        return self.spec.apis

    @property
    def commands(self) -> tuple[str, ...]:
        """Command names declared by this extension, without duplicates."""

        return tuple(
            dict.fromkeys(
                member.name
                for requirement in self.spec.requirements
                for member in requirement.members
                if member.kind == "command"
            )
        )

    @property
    def enums(self) -> tuple[str, ...]:
        """Enum names declared by this extension, without duplicates."""

        return tuple(
            dict.fromkeys(
                member.name
                for requirement in self.spec.requirements
                for member in requirement.members
                if member.kind == "enum"
            )
        )

    @property
    def types(self) -> tuple[str, ...]:
        """C type names declared by this extension, without duplicates."""

        return tuple(
            dict.fromkeys(
                member.name
                for requirement in self.spec.requirements
                for member in requirement.members
                if member.kind == "type"
            )
        )

    @property
    def dependencies(self) -> tuple[str, ...]:
        """Registry dependency expressions, without duplicates."""

        return tuple(
            dict.fromkeys(
                requirement.depends
                for requirement in self.spec.requirements
                if requirement.depends is not None
            )
        )

    def is_present(self, device: object | None = None) -> bool:
        """Check whether the extension is available for a context or device.

        ALC extensions use ``device`` or the null-device scope. AL extensions use
        the current context. For dual-API extensions, supplying a device selects
        the ALC query.

        Args:
            device: Native ALC device handle, or ``None`` when using the current
                AL context or null-device scope.

        Returns:
            Whether the selected runtime scope reports this extension.

        Raises:
            ContextRequiredError: An AL query has no current context.
            FunctionUnavailableError: A required core query is unavailable.
        """

        if self.name.startswith("ALC_") or (device is not None and "alc" in self.apis):
            return self.library.is_alc_extension_present(self.name, device)
        return self.library.is_al_extension_present(self.name)

    def require(self, device: object | None = None) -> None:
        """Require this extension for a context or device scope.

        Args:
            device: Native ALC device handle, or ``None`` when using the current
                AL context or null-device scope.

        Raises:
            ContextRequiredError: An AL query has no current context.
            ExtensionUnavailableError: The selected scope does not report this
                extension.
            FunctionUnavailableError: A required core query is unavailable.
        """

        if self.is_present(device):
            return
        from pyalsoft.bindings._library import ExtensionUnavailableError

        raise ExtensionUnavailableError(
            f"OpenAL extension {self.name!r} is not available"
        )

    def get_function(
        self, name: str, *, device: object | None = None
    ) -> ForeignFunction:
        """Resolve a command declared by this extension.

        Args:
            name: Exact C command name declared by this extension.
            device: Native ALC device used for device-scoped resolution.

        Returns:
            Typed, cached native command callable.

        Raises:
            KeyError: ``name`` is not declared by this extension.
            ContextRequiredError: Resolution requires a current AL context.
            ExtensionUnavailableError: This extension is not present.
            FunctionUnavailableError: The native entry point is unavailable.
        """

        if name not in self.commands:
            raise KeyError(f"{name!r} is not declared by {self.name}")
        return self.library.get_function(name, device=device)

    def __repr__(self) -> str:
        return f"Extension(name={self.name!r}, apis={self.apis!r})"


class ExtensionNamespace(Mapping[str, Extension]):
    """Mapping base used by the generated extension capability namespace.

    Iteration yields every registry extension name. Values are created lazily and
    cached per loaded library.
    """

    def __init__(self, library: OpenALLibrary) -> None:
        self.library = library
        self._cache: dict[str, Extension] = {}

    def _get(self, name: str) -> Extension:
        extension = self._cache.get(name)
        if extension is None:
            extension = Extension(self.library, _EXTENSIONS[name])
            self._cache[name] = extension
        return extension

    def __getitem__(self, name: str) -> Extension:
        return self._get(name)

    def __iter__(self) -> Iterator[str]:
        return iter(_EXTENSIONS)

    def __len__(self) -> int:
        return len(_EXTENSIONS)


__all__ = ["Extension", "ExtensionNamespace"]
