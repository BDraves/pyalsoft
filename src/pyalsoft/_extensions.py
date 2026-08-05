"""Runtime support for generated OpenAL extension capability objects."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import TYPE_CHECKING

from pyalsoft._generated.registry import API_SETS
from pyalsoft._specs import ApiSetSpec

if TYPE_CHECKING:
    from pyalsoft._library import ForeignFunction, OpenALLibrary

_EXTENSIONS = {item.name: item for item in API_SETS if item.kind == "extension"}


class Extension:
    """Capabilities and runtime access for one registry extension."""

    def __init__(self, library: OpenALLibrary, spec: ApiSetSpec) -> None:
        self.library = library
        self.spec = spec

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def apis(self) -> tuple[str, ...]:
        return self.spec.apis

    @property
    def commands(self) -> tuple[str, ...]:
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
        return tuple(
            dict.fromkeys(
                requirement.depends
                for requirement in self.spec.requirements
                if requirement.depends is not None
            )
        )

    def is_present(self, device: object | None = None) -> bool:
        """Check whether the extension is available for a context or device."""

        if self.name.startswith("ALC_") or (device is not None and "alc" in self.apis):
            return self.library.is_alc_extension_present(self.name, device)
        return self.library.is_al_extension_present(self.name)

    def require(self, device: object | None = None) -> None:
        """Raise :class:`ExtensionUnavailableError` when unavailable."""

        if self.is_present(device):
            return
        from pyalsoft._library import ExtensionUnavailableError

        raise ExtensionUnavailableError(
            f"OpenAL extension {self.name!r} is not available"
        )

    def get_function(
        self, name: str, *, device: object | None = None
    ) -> ForeignFunction:
        """Resolve a command declared by this extension."""

        if name not in self.commands:
            raise KeyError(f"{name!r} is not declared by {self.name}")
        return self.library.get_function(name, device=device)

    def __repr__(self) -> str:
        return f"Extension(name={self.name!r}, apis={self.apis!r})"


class ExtensionNamespace(Mapping[str, Extension]):
    """Mapping base used by the generated extension capability namespace."""

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
