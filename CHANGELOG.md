# Changelog

Notable user-facing changes to PyALSoft are documented here.

## Unreleased

## 1.0.1 - 2026.08.13

### Added

- Added `play_stationary()` for non-spatial convenience playback that bypasses
  position, distance, Doppler, directional-cone, and HRTF processing. (@BDraves)

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
