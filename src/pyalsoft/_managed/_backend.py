"""Shared OpenAL details used by the managed playback and capture APIs."""

from __future__ import annotations

from pyalsoft import bindings
from pyalsoft._managed.audio import SampleType
from pyalsoft._managed.errors import AudioBackendError

_FORMAT_BY_LAYOUT = {
    (1, SampleType.UINT8): bindings.enums.ALFormat.FORMAT_MONO8,
    (1, SampleType.INT16): bindings.enums.ALFormat.FORMAT_MONO16,
    (2, SampleType.UINT8): bindings.enums.ALFormat.FORMAT_STEREO8,
    (2, SampleType.INT16): bindings.enums.ALFormat.FORMAT_STEREO16,
}


def _clear_alc_errors(
    library: bindings.OpenALLibrary, device: object | None = None
) -> None:
    for _ in range(16):
        if int(library.alc.get_error(device)) == bindings.ALC_NO_ERROR:
            return
    raise AudioBackendError("OpenAL ALC error state could not be cleared")


def _check_alc_error(
    library: bindings.OpenALLibrary,
    device: object | None,
    operation: str,
) -> None:
    code = int(library.alc.get_error(device))
    if code == bindings.ALC_NO_ERROR:
        return
    try:
        name = bindings.enums.ALCContextErrorCode(code).name
    except ValueError:
        name = f"unknown error 0x{code:04x}"
    raise AudioBackendError(f"{operation} failed: OpenAL ALC {name}")
