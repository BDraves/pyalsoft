# Owned backend API reference

This page documents the hand-written lifetime and extension layer in
`pyalsoft.bindings`. The generated AL/ALC command wrappers, typed objects,
constants, enums, and registry metadata remain in the
[low-level bindings reference](reference.md). See
[owned backend handles](backend.md) for task-oriented examples and native
lifetime guidance.

This remains a low-level API: ordinary generated calls and state queries do not
automatically translate the AL or ALC error state into Python exceptions. The
owned methods document the places where lifetime-sensitive failures are checked
and raised explicitly.

## Library loading and extensions

::: pyalsoft.bindings
    options:
      heading_level: 3
      members:
        - LibraryPath
        - ForeignFunction
        - OpenALLibrary
        - Extension
        - load
      show_root_heading: false
      show_root_members_full_path: true

## Owned devices and contexts

::: pyalsoft.bindings
    options:
      heading_level: 3
      members:
        - open_device
        - open_loopback_device
        - open_capture_device
        - Device
        - PlaybackDevice
        - LoopbackDevice
        - CaptureDevice
        - Context
      show_root_heading: false
      show_root_members_full_path: true

## Callback types and registrations

::: pyalsoft.bindings
    options:
      heading_level: 3
      members:
        - EventCallback
        - DebugCallback
        - SystemEventCallback
        - BufferCallback
        - FoldbackCallback
        - CallbackRegistration
        - FoldbackRegistration
      show_root_heading: false
      show_root_members_full_path: true

## Exceptions

::: pyalsoft.bindings
    options:
      heading_level: 3
      members:
        - OpenALError
        - LibraryNotFoundError
        - FunctionUnavailableError
        - ExtensionUnavailableError
        - ContextRequiredError
        - ContextMismatchError
        - ALCHandleError
        - DeviceOpenError
        - DeviceCloseError
        - ContextCreateError
        - ContextActivationError
        - HandleClosedError
        - NativeCallError
        - CallbackControlError
      show_root_heading: false
      show_root_members_full_path: true
