# Repository tools

The files in this directory are development and release entry points. They are
kept as separate commands because they have different dependencies and side
effects; shared implementation belongs in importable modules below them.

## Commands

| Entry point | Purpose | Side effects |
| --- | --- | --- |
| `generate_bindings.py` | Parse `al.xml` and generate typed bindings, metadata, and reference documentation. | Writes generated Python, `docs/reference.md`, and the README version badge unless `--check` is used. |
| `sync_openal_soft.py` | Verify or download the pinned OpenAL Soft registry, source archive, Windows runtime, and licenses. | `--check` is read-only; the default mode uses the network and updates `vendor/openal-soft`. |
| `build_openal_soft.py` | Build or stage the native runtime for the current platform. | Extracts and compiles under `build/`; `PYALSOFT_NATIVE_ROOT` can redirect the staged runtime. |
| `smoke_test_runtime.py` | Exercise device/context creation using the runtime contained in an installed wheel. | Opens the OpenAL null device. |
| `freeze_test.py` | Build a one-file PyInstaller executable from the runtime smoke test and run it. | Uses a temporary build directory. |

`semantic_overrides.toml` is generator input, not an executable tool. It records
reviewed corrections for semantics that the upstream XML cannot express.

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
`PYALSOFT_NATIVE_ROOT`, when set, must be an absolute path that both the native
builder and wheel build can access.

The thin top-level scripts are intentionally stable: contributor commands, CI,
and downstream imports do not need to know the internal package layout.
