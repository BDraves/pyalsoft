# Changelog

Notable user-facing changes to PyALSoft are documented here.

## Unreleased

### Added

- Added managed static-clip loop regions through `upload(loop_points=...)`,
  backed by `AL_SOFT_loop_points`. (@BDraves)
- Added reusable managed effect buses with live immutable configuration,
  shared voice and stream sends, optional effect-slot chaining, and deterministic
  ownership checks. (@BDraves)
- Completed managed EFX listener, source, and slot controls and added dedicated
  dialogue and low-frequency effect configurations. (@BDraves)
- Added managed offline rendering for mono, stereo, surround, and B-format
  output, reusing the existing playback resource API. (@BDraves)
- Added managed playback-device pause and resume operations. (@BDraves)

## 1.5.0 - 2026.08.19

### Added

- Added managed static and streaming buffer support for float, double,
  multichannel, ADPCM, mu-law, A-law, ambisonic B-format, and UHJ extension
  formats, including compressed block alignment and ambisonic buffer metadata.
  Vorbis, native WAVE, and LOKI formats are also available with OpenAL
  implementations that advertise those legacy extensions. Managed capture now
  also accepts OpenAL Soft's float32 and multichannel capture formats.
  (@BDraves)

### Changed

- `Clip.info` now returns `BufferInfo` for clips uploaded from `BufferData`, and
  continues to return `SoundInfo` for clips uploaded from `PCM`. Its annotated
  return type is therefore `SoundInfo | BufferInfo`. (@BDraves)

## 1.4.0 - 2026.08.19

### Added

- Added managed delayed and device-clock-scheduled voice and stream starts
  through `play()`, `restart()`, and `start_stream()`, including convenience
  `PlayingSound` support. (@BDraves)
- Added nested `defer_updates()` transactions for applying listener, source,
  effect, play, and pause changes together across explicit sessions and the
  convenience runtime. (@BDraves)
- Added managed per-source distance models, physical radius, explicit
  spatialization, direct-channel routing and remixing, stereo angles, runtime
  resampler selection, air absorption, room rolloff, UHJ Super Stereo controls,
  and precise source/device latency and clock queries. (@BDraves)
- Exposed every advanced source control through `play()`, `PlayingSound`
  properties, and `PlayingSound.update()`, including explicit clearing of
  nullable overrides and second-based `PlaybackClock` conversions. (@BDraves)

### Fixed

- Corrected stopped-voice configuration when another playback context is
  current, and made mono convenience sounds rebuild their backing clip when
  direct-channel routing changes before or during playback. (@BDraves)

## 1.3.0 - 2026.08.16

### Added

- Added typed playback device and context configuration for sample and refresh
  rates, synchronous processing, source and EFX send budgets, named HRTF
  profiles, output limiting, and output layouts, together with effective-state
  reporting through `PlaybackInfo`. (@BDraves)
- Added live playback reconfiguration with patch and full-replacement semantics,
  requested-state inspection, and preservation of existing clips, voices, and
  streams while resetting device settings. (@BDraves)

## 1.2.0 - 2026.08.14

### Added

- Added managed configurations for every core EFX effect, with declarative
  validation and native parameter mapping. (@BDraves)

## 1.1.1 - 2026.08.14

### Added

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
