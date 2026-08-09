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

Extension entry points are cached by the resolver and native scope that
produced them. AL functions are scoped to the effective current context;
ALC functions are scoped to the supplied device (or the null-device scope).
Owned contexts and devices invalidate their entries when closed. Applications
that destroy or reconfigure contexts and devices through the raw generated API
should call `library.clear_extension_cache()` before a native handle address can
be reused.

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
Closing a registration first unregisters it, then waits for any in-flight
invocations before releasing the ctypes trampoline. A callback must not close
its own registration; that attempt is rejected and retained as a callback
error.

System audio-device events are global to the loaded OpenAL implementation, so
their registration is owned by `OpenALLibrary` rather than a device:

```python
with library.register_system_event_callback(
    lambda event, device_type, device, message: print(message),
    event_types=[bindings.ALC_EVENT_TYPE_DEVICE_ADDED_SOFT],
) as registration:
    ...
```

Only one system-event callback can be active for a native OpenAL library.
Replacing it closes the previous registration. The callback may run on a
background system thread; it should enqueue minimal immutable data and return.
It must not call AL or ALC functions. Call
`library.clear_system_event_callback()` to unregister it even when the returned
registration was not retained.

`Context.register_buffer_callback` owns a callback for one buffer. It accepts
either an integer identifier or a typed `Buffer`; typed buffers must come from
the same `OpenALLibrary`. It passes a writable `memoryview` that is valid only
until the callback returns, and the callback returns the number of bytes
written. Exceptions and invalid return sizes are retained on the registration
and reported to OpenAL as zero bytes written:

```python
def fill(samples: memoryview) -> int:
    samples[:] = next_audio_block(len(samples))
    return len(samples)


with context.register_buffer_callback(
    buffer_id,
    bindings.AL_FORMAT_MONO16,
    48_000,
    fill,
) as registration:
    ...
```

This API makes the Python/native lifetime and exception boundary safe; it does
not make Python execution hard real-time safe. `AL_SOFT_callback_buffer`
callbacks run in the audio mixing path and must avoid blocking, I/O, allocation,
and unbounded work. A native callback remains the appropriate choice when hard
real-time behavior is required.

Explicitly closing a buffer callback asks OpenAL to replace its callback-backed
storage and can fail while the buffer remains attached to a source. Context
teardown handles this case safely by retaining the trampoline until the native
context, sources, and buffers have been destroyed.

`Context.start_foldback` retains both the foldback callback and the exact
writable `ALfloat` backing allocation. The registration exposes that allocation
as `registration.memory`, including after closure so captured samples remain
available. It validates the extension's supported mono/stereo modes, minimum
count, and minimum storage capacity. Closing it requests a stop and waits for
OpenAL's STOP event before releasing the native callback. Foldback callbacks
must return promptly and must not call AL/ALC functions or close their own
registration.

## Retained static buffers

`AL_EXT_STATIC_BUFFER` stores the supplied pointer instead of copying its
contents. Use `Context.set_static_buffer_data` with a same-library typed
`Buffer` or integer identifier and `bytes`, `bytearray`, or a contiguous
`memoryview` so the context retains the exact native backing:

```python
context.set_static_buffer_data(
    buffer_id,
    bindings.AL_FORMAT_MONO16,
    pcm,
    48_000,
)
```

Writable buffers are borrowed and pinned against resizing; read-only buffers
receive a retained native copy. The storage is released after the context is
destroyed. Raw callback, foldback, direct-context, and static-buffer commands
remain available through the generated namespaces, but callers using those
commands own every associated callback and backing-storage lifetime. Generated
static-buffer commands borrow the exact address of a writable buffer or ctypes
allocation; keep that storage alive and do not resize it while OpenAL may use
the buffer. Use `Context.set_static_buffer_data` when the binding should manage
that lifetime or when the input is immutable.
