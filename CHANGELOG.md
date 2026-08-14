# Changelog

Notable user-facing changes to PyALSoft are documented here.

## 1.1.1 - 2026.08.14

## Added

- Added `direct_channels=True` playback, including automatic mono-to-stereo
  expansion for convenience playback, to bypass HRTF virtualization. (@BDraves)

## 1.1.0 - 2026.08.14

### Added

- Added managed `BandPassFilter` support for direct and auxiliary EFX routes.
  (@BDraves)

## 1.0.2 - 2026.08.13

### Changed

- Replaced `play_stationary()` with the `spatialize` keyword on `play()`, which
  now supports non-spatial playback for both convenience and explicit sessions.
  (@BDraves)

## 1.0.1 - 2026.08.13

### Added

- Added `play_stationary()` for non-spatial convenience playback that bypasses
  position, distance, Doppler, and directional-cone processing. (@BDraves)

## 1.0.0 - 2026-08-10

### Added

- Added a configurable bounded cache for file-backed sounds, with inspection
  and eviction controls. (@BDraves)
- Published user guides and an API reference through GitHub Pages. (@BDraves)

### Changed

- Declared PyALSoft production-stable. (@BDraves)

### Fixed

- Serialized managed playback sessions. (@BDraves)
- Corrected generated EFX property semantics. (@BDraves)
- Strengthened buffer-lifetime and playback-context handling. (@BDraves)
