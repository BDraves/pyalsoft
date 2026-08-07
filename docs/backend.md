# Owned backend handles

`pyalsoft.bindings` has two complementary low-level interfaces:

- `library.al` and `library.alc` are generated, function-oriented wrappers for
  every command in the vendored OpenAL registry.
- The owned handle API adds deterministic device and context lifetimes without
  hiding the underlying native pointers.

The generated API remains available for applications that already manage ALC
lifetimes themselves.

## Playback contexts

`open_device` returns an owned playback device. Contexts created from it are
closed before the device, including when an exception leaves a `with` block:

```python
from pyalsoft import bindings

with bindings.open_device() as device:
    with device.create_context() as context:
        with context.activate():
            print(context.vendor, context.renderer, context.version)
            print(device.name, device.version)

            buffer_ids = context.library.al.gen_buffers()
            # Continue with generated commands or typed AL objects here.
```

`Context.activate()` restores the previous process-wide context. Pass
`thread_local=True` to use `ALC_EXT_thread_local_context`; the extension is
checked before changing thread state.

The native pointers are available as `device.handle` and `context.handle` for
generated commands that do not yet have a convenience method. Accessing either
handle after closure raises `HandleClosedError`.

## Context and device state

The handle properties cover state that the registry can describe but cannot
attach to generated AL object descriptors. Examples include:

- context identity, extensions, Doppler settings, speed of sound, distance
  model, and default filter order;
- device identity, ALC version, connection state, HRTF state and specifiers,
  output limiter and mode, device clock and latency, ambisonic order, and debug
  context flags.

Extension-backed properties raise `ExtensionUnavailableError` when queried on
an implementation that does not provide the required extension. Unknown future
enum values are returned as integers rather than causing a conversion failure.

## Loopback rendering

`open_loopback_device` provides deterministic, device-independent rendering
through `ALC_SOFT_loopback`:

```python
from pyalsoft import bindings

attributes = [
    bindings.ALC_FORMAT_CHANNELS_SOFT,
    bindings.ALC_STEREO_SOFT,
    bindings.ALC_FORMAT_TYPE_SOFT,
    bindings.ALC_SHORT_SOFT,
    bindings.ALC_FREQUENCY,
    48_000,
]

with bindings.open_loopback_device() as device:
    with device.create_context(attributes) as context:
        with context.activate():
            output = bytearray(512 * 2 * 2)
            # Start sources before rendering.
            device.render_samples(output, 512)
```

The wheel test suite runs `tools/conformance_test_runtime.py` against this path
to exercise real core playback, typed object properties, EFX, extension
resolution, and debug callback marshalling without requiring audio hardware.

## Capture devices

Core ALC capture devices use a distinct owned type and matching close command:

```python
from pyalsoft import bindings

with bindings.open_capture_device(
    48_000,
    bindings.AL_FORMAT_MONO16,
    4_800,
) as capture:
    capture.start()
    available = capture.available_samples
    samples = bytearray(available * 2)
    capture.read_samples(samples, available)
```

The caller owns capture and loopback output storage. Pass a writable
`bytearray`, writable `memoryview`, ctypes allocation, or another object accepted
by the generated command wrapper.

## Native callbacks

`Context.register_event_callback` and `Context.register_debug_callback` retain
their ctypes trampolines until the returned `CallbackRegistration` is closed.
They also prevent Python exceptions from crossing a native callback boundary:

```python
with context.register_debug_callback(
    lambda source, type, id, severity, message: print(message)
) as registration:
    # Run OpenAL operations that may produce debug messages.
    ...

registration.raise_if_failed()
```

Callback exceptions are available through `registration.errors` and can be
re-raised as a `BaseExceptionGroup` in an application-controlled thread.
Registry-level system-event, buffer, and foldback callbacks remain available
through the generated namespaces for applications that need them.
