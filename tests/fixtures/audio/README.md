# Static audio fixtures

These short files are project-owned test assets generated from FFmpeg's sine
source. They contain no third-party recording:

```text
sine frequency: 440 Hz
duration: 0.1 seconds or less
```

The format-specific tests validate decoded layout, source sample rate, sample
representation, and sample content. The Opus file is present only to verify a
clear unsupported-codec error.
