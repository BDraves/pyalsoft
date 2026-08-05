"""Synchronize the pinned OpenAL Soft sources and Windows runtime."""

from __future__ import annotations

import argparse
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.openal_soft import (  # noqa: E402
    ROOT,
    VENDOR,
    source_configuration,
    verify_checksum,
)

VENDORED_FILES = {
    "sha256": VENDOR / "al.xml",
    "library_source_sha256": VENDOR / "openal-soft-1.25.2.tar.bz2",
    "windows_amd64_sha256": VENDOR / "runtime" / "win_amd64" / "soft_oal.dll",
    "license_sha256": VENDOR / "COPYING",
    "pffft_license_sha256": VENDOR / "LICENSE-pffft",
}

REQUIRED_CONFIG = {
    "source_url",
    "sha256",
    "library_source_url",
    "library_source_sha256",
    "binary_archive_url",
    "binary_archive_sha256",
    "windows_amd64_member",
    "windows_amd64_sha256",
    "license_member",
    "license_sha256",
    "pffft_license_member",
    "pffft_license_sha256",
}


def _config() -> dict[str, str]:
    return source_configuration(REQUIRED_CONFIG)


def _download(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "pyalsoft-vendor-sync"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return bytes(response.read())


def _normalized_registry(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _member(archive: bytes, name: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        try:
            return bundle.read(name)
        except KeyError as error:
            raise ValueError(f"binary archive does not contain {name!r}") from error


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() == data:
        print(f"unchanged {path.relative_to(ROOT)}")
        return
    path.write_bytes(data)
    print(f"updated {path.relative_to(ROOT)}")


def check() -> None:
    config = _config()
    for checksum_key, path in VENDORED_FILES.items():
        if not path.is_file():
            raise FileNotFoundError(f"missing vendored file: {path}")
        data = path.read_bytes()
        if checksum_key == "sha256":
            data = _normalized_registry(data)
        verify_checksum(str(path.relative_to(ROOT)), data, config[checksum_key])
        print(f"verified {path.relative_to(ROOT)}")


def sync() -> None:
    config = _config()
    registry = _normalized_registry(_download(config["source_url"]))
    verify_checksum("OpenAL registry", registry, config["sha256"])

    source_archive = _download(config["library_source_url"])
    verify_checksum(
        "OpenAL Soft source archive",
        source_archive,
        config["library_source_sha256"],
    )

    archive = _download(config["binary_archive_url"])
    verify_checksum(
        "OpenAL Soft binary archive", archive, config["binary_archive_sha256"]
    )

    dll = verify_checksum(
        "OpenAL Soft Win64 DLL",
        _member(archive, config["windows_amd64_member"]),
        config["windows_amd64_sha256"],
    )
    license_text = verify_checksum(
        "OpenAL Soft license",
        _member(archive, config["license_member"]),
        config["license_sha256"],
    )
    pffft_license = verify_checksum(
        "PFFFT license",
        _member(archive, config["pffft_license_member"]),
        config["pffft_license_sha256"],
    )

    _write(VENDORED_FILES["sha256"], registry)
    _write(VENDORED_FILES["library_source_sha256"], source_archive)
    _write(VENDORED_FILES["windows_amd64_sha256"], dll)
    _write(VENDORED_FILES["license_sha256"], license_text)
    _write(VENDORED_FILES["pffft_license_sha256"], pffft_license)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the checked-in files without downloading anything",
    )
    args = parser.parse_args()
    if args.check:
        check()
    else:
        sync()


if __name__ == "__main__":
    main()
