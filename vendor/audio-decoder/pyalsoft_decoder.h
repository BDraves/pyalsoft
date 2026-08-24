#ifndef PYALSOFT_DECODER_H
#define PYALSOFT_DECODER_H

#include <stddef.h>
#include <stdint.h>

#if defined(_WIN32)
#define PYALSOFT_DECODER_API __declspec(dllexport)
#else
#define PYALSOFT_DECODER_API __attribute__((visibility("default")))
#endif

#ifdef __cplusplus
extern "C" {
#endif

enum pyalsoft_decoder_codec {
    PYALSOFT_CODEC_WAV = 1,
    PYALSOFT_CODEC_FLAC = 2,
    PYALSOFT_CODEC_MP3 = 3,
    PYALSOFT_CODEC_VORBIS = 4
};

enum pyalsoft_decoder_sample_format {
    PYALSOFT_SAMPLE_UINT8 = 1,
    PYALSOFT_SAMPLE_INT16 = 2,
    PYALSOFT_SAMPLE_FLOAT32 = 3
};

enum pyalsoft_decoder_error_code {
    PYALSOFT_DECODER_SUCCESS = 0,
    PYALSOFT_DECODER_INVALID_ARGUMENT = 1,
    PYALSOFT_DECODER_INVALID_DATA = 2,
    PYALSOFT_DECODER_UNSUPPORTED_FORMAT = 3,
    PYALSOFT_DECODER_UNSUPPORTED_CHANNELS = 4,
    PYALSOFT_DECODER_INVALID_FRAME_COUNT = 5,
    PYALSOFT_DECODER_ALLOCATION_FAILED = 6,
    PYALSOFT_DECODER_TRUNCATED = 7,
    PYALSOFT_DECODER_INTERNAL_ERROR = 8,
    PYALSOFT_DECODER_OUTPUT_TOO_LARGE = 9
};

struct pyalsoft_decoder_info {
    uint32_t channels;
    uint32_t sample_rate;
    uint32_t sample_format;
    uint64_t frame_count;
};

struct pyalsoft_decoder_error {
    int32_t code;
    char message[256];
};

PYALSOFT_DECODER_API int32_t pyalsoft_decoder_probe(
    const void *data,
    size_t data_size,
    int32_t codec,
    int32_t sample_format,
    struct pyalsoft_decoder_info *info,
    struct pyalsoft_decoder_error *error
);

PYALSOFT_DECODER_API int32_t pyalsoft_decoder_decode(
    const void *data,
    size_t data_size,
    int32_t codec,
    int32_t sample_format,
    size_t maximum_pcm_size,
    struct pyalsoft_decoder_info *info,
    void **pcm,
    size_t *pcm_size,
    struct pyalsoft_decoder_error *error
);

PYALSOFT_DECODER_API void pyalsoft_decoder_free(void *pcm);

#ifdef __cplusplus
}
#endif

#endif
