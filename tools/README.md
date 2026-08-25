# Repository tools

The files in this directory are development and release entry points. They are
kept as separate commands because they have different dependencies and side
effects; shared implementation belongs in importable modules below them.

## Commands

| Entry point | Purpose | Side effects |
| --- | --- | --- |
| `generate_bindings.py` | Parse `al.xml` and generate typed bindings, metadata, and reference documentation. | Writes generated Python, `docs/reference.md`, and the README version badge unless `--check` is used. |
| `changelog.py` | Extract one version's notes from `CHANGELOG.md` for publication. | Read-only; writes the selected notes to standard output. |
| `update_openal_soft.py` | Discover or select a stable OpenAL Soft release, validate its complete artifact set, update the pin, regenerate bindings, and report public API impact. | Downloads upstream release assets and updates the complete `vendor/openal-soft` dependency, generated Python, reference documentation, badge, notices, and changelog. |
| `sync_openal_soft.py` | Verify or download the pinned OpenAL Soft registry, source archive, Windows runtime, and licenses. | `--check` is read-only; the default mode uses the network and updates `vendor/openal-soft`. |
| `build_openal_soft.py` | Build or stage the native runtime for the current platform. | Extracts and compiles under `build/`; uses at most two compiler jobs by default. `--jobs` or `PYALSOFT_BUILD_JOBS` can change the limit, and `PYALSOFT_NATIVE_ROOT` can redirect the staged runtime. |
| `build_audio_decoder.py` | Build and stage the decoder-only miniaudio/stb_vorbis helper for the current platform. | Verifies pinned source checksums and compiles under `build/`; `--vendor-runtime` also updates the current platform's checked-in runtime. |
| `smoke_test_runtime.py` | Exercise device/context creation using the runtime contained in an installed wheel. | Opens the OpenAL null device. |
| `freeze_test.py` | Build a one-file PyInstaller executable from the runtime smoke test and run it. | Uses a temporary build directory. |

`semantic_overrides.toml` is generator input, not an executable tool. It records
reviewed corrections for semantics that the upstream XML cannot express.

Run `uv run python tools/update_openal_soft.py` to update to GitHub's latest
published stable release, or pass `--version X.Y.Z` to select one explicitly.
The command stages and validates every input before replacing tracked files.
Pass `--report PATH` to save its provenance, generated API-diff, and PyALSoft
SemVer recommendation as Markdown. It deliberately does not change the
PyALSoft project version or publish a release.

## Internal structure

`bindings/` implements the binding generator as a pipeline:

1. `paths.py`, `models.py`, and `config.py` define inputs and neutral data.
2. `registry.py` validates and parses XML.
3. `c_types.py` resolves C declarations and constants.
4. `semantics.py` infers Python names, properties, and safe wrapper signatures.
5. `render_ctypes.py`, `render_api.py`, and `render_docs.py` render artifacts.
6. `outputs.py` assembles/checks files; `cli.py` handles arguments.

`openal_soft.py` is the shared support layer for vendoring, native builds, and
wheel assembly. It owns manifest validation, checksums, and platform targeting.
`audio_decoder.py` provides the corresponding source verification and platform
targeting for the private static-audio decoder.
`PYALSOFT_NATIVE_ROOT`, when set, must be an absolute path that both the native
builder and wheel build can access.

Native compilation is explicitly limited to two concurrent jobs by default.
Pass `--jobs N` for a one-off build or set `PYALSOFT_BUILD_JOBS=N` for wheel
builds and other indirect invocations. Values must be positive integers; avoid
raising the limit on memory-constrained WSL or CI workers.

The thin top-level scripts are intentionally stable: contributor commands, CI,
and downstream imports do not need to know the internal package layout.
