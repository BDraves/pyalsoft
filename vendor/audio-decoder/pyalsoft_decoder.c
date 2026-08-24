#include "pyalsoft_decoder.h"

#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define STB_VORBIS_HEADER_ONLY
#include "stb_vorbis.c"
#undef STB_VORBIS_HEADER_ONLY

#define MA_NO_DEVICE_IO
#define MA_NO_ENCODING
#define MA_NO_ENGINE
#define MA_NO_GENERATION
#define MA_NO_NODE_GRAPH
#define MA_NO_RESOURCE_MANAGER
#define MA_NO_THREADING
#define MINIAUDIO_IMPLEMENTATION
#include "miniaudio.h"

#include "stb_vorbis.c"

static int32_t pyalsoft_fail(
    struct pyalsoft_decoder_error *error,
    int32_t code,
    const char *message
) {
    if (error != NULL) {
        error->code = code;
        if (message == NULL) {
            error->message[0] = '\0';
        } else {
            (void)snprintf(error->message, sizeof(error->message), "%s", message);
        }
    }
    return code;
}

static void pyalsoft_clear_error(struct pyalsoft_decoder_error *error) {
    if (error != NULL) {
        error->code = PYALSOFT_DECODER_SUCCESS;
        error->message[0] = '\0';
    }
}

static size_t pyalsoft_sample_size(int32_t sample_format) {
    switch (sample_format) {
        case PYALSOFT_SAMPLE_UINT8:
            return 1;
        case PYALSOFT_SAMPLE_INT16:
            return 2;
        case PYALSOFT_SAMPLE_FLOAT32:
            return 4;
        default:
            return 0;
    }
}

static ma_format pyalsoft_ma_format(int32_t sample_format) {
    switch (sample_format) {
        case PYALSOFT_SAMPLE_UINT8:
            return ma_format_u8;
        case PYALSOFT_SAMPLE_INT16:
            return ma_format_s16;
        case PYALSOFT_SAMPLE_FLOAT32:
            return ma_format_f32;
        default:
            return ma_format_unknown;
    }
}

static ma_encoding_format pyalsoft_ma_encoding(int32_t codec) {
    switch (codec) {
        case PYALSOFT_CODEC_WAV:
            return ma_encoding_format_wav;
        case PYALSOFT_CODEC_FLAC:
            return ma_encoding_format_flac;
        case PYALSOFT_CODEC_MP3:
            return ma_encoding_format_mp3;
        default:
            return ma_encoding_format_unknown;
    }
}

static int32_t pyalsoft_validate_info(
    struct pyalsoft_decoder_info *info,
    int32_t codec,
    int32_t sample_format,
    struct pyalsoft_decoder_error *error
) {
    size_t sample_size = pyalsoft_sample_size(sample_format);
    uint64_t frame_size;

    if (info->channels == 0 || info->sample_rate == 0) {
        return pyalsoft_fail(
            error, PYALSOFT_DECODER_INVALID_DATA, "invalid audio metadata"
        );
    }
    if (codec != PYALSOFT_CODEC_WAV && info->channels > 2) {
        return pyalsoft_fail(
            error,
            PYALSOFT_DECODER_UNSUPPORTED_CHANNELS,
            "compressed audio must be mono or stereo"
        );
    }
    if (info->frame_count == 0) {
        return pyalsoft_fail(
            error,
            PYALSOFT_DECODER_INVALID_FRAME_COUNT,
            "audio contains no complete sample frames"
        );
    }
    frame_size = (uint64_t)info->channels * (uint64_t)sample_size;
    if (sample_size == 0 || info->frame_count > (uint64_t)SIZE_MAX / frame_size) {
        return pyalsoft_fail(
            error,
            PYALSOFT_DECODER_INVALID_FRAME_COUNT,
            "decoded audio size is not representable"
        );
    }
    info->sample_format = (uint32_t)sample_format;
    return PYALSOFT_DECODER_SUCCESS;
}

static int32_t pyalsoft_probe_miniaudio(
    const void *data,
    size_t data_size,
    int32_t codec,
    int32_t sample_format,
    struct pyalsoft_decoder_info *info,
    struct pyalsoft_decoder_error *error
) {
    ma_decoder decoder;
    ma_decoder_config config;
    ma_result result;
    ma_format output_format;

    config = ma_decoder_config_init(pyalsoft_ma_format(sample_format), 0, 0);
    config.encodingFormat = pyalsoft_ma_encoding(codec);
    result = ma_decoder_init_memory(data, data_size, &config, &decoder);
    if (result != MA_SUCCESS) {
        return pyalsoft_fail(
            error, PYALSOFT_DECODER_INVALID_DATA, ma_result_description(result)
        );
    }
    result = ma_decoder_get_data_format(
        &decoder,
        &output_format,
        &info->channels,
        &info->sample_rate,
        NULL,
        0
    );
    if (result == MA_SUCCESS) {
        result = ma_decoder_get_length_in_pcm_frames(&decoder, &info->frame_count);
    }
    ma_decoder_uninit(&decoder);
    if (result != MA_SUCCESS) {
        return pyalsoft_fail(
            error, PYALSOFT_DECODER_INVALID_DATA, ma_result_description(result)
        );
    }
    if (output_format != pyalsoft_ma_format(sample_format)) {
        return pyalsoft_fail(
            error,
            PYALSOFT_DECODER_INTERNAL_ERROR,
            "decoder did not produce the requested sample format"
        );
    }
    return pyalsoft_validate_info(info, codec, sample_format, error);
}

static int32_t pyalsoft_probe_vorbis(
    const void *data,
    size_t data_size,
    struct pyalsoft_decoder_info *info,
    struct pyalsoft_decoder_error *error
) {
    stb_vorbis *decoder;
    stb_vorbis_info vorbis_info;
    int vorbis_error = VORBIS__no_error;

    if (data_size > INT_MAX) {
        return pyalsoft_fail(
            error,
            PYALSOFT_DECODER_INVALID_ARGUMENT,
            "Vorbis input exceeds the decoder size limit"
        );
    }
    decoder = stb_vorbis_open_memory(
        (const unsigned char *)data, (int)data_size, &vorbis_error, NULL
    );
    if (decoder == NULL) {
        return pyalsoft_fail(
            error, PYALSOFT_DECODER_INVALID_DATA, "invalid Ogg Vorbis data"
        );
    }
    vorbis_info = stb_vorbis_get_info(decoder);
    info->channels = (uint32_t)vorbis_info.channels;
    info->sample_rate = vorbis_info.sample_rate;
    info->frame_count = stb_vorbis_stream_length_in_samples(decoder);
    stb_vorbis_close(decoder);
    return pyalsoft_validate_info(
        info, PYALSOFT_CODEC_VORBIS, PYALSOFT_SAMPLE_FLOAT32, error
    );
}

int32_t pyalsoft_decoder_probe(
    const void *data,
    size_t data_size,
    int32_t codec,
    int32_t sample_format,
    struct pyalsoft_decoder_info *info,
    struct pyalsoft_decoder_error *error
) {
    pyalsoft_clear_error(error);
    if (data == NULL || data_size == 0 || info == NULL) {
        return pyalsoft_fail(
            error, PYALSOFT_DECODER_INVALID_ARGUMENT, "audio input is empty"
        );
    }
    (void)memset(info, 0, sizeof(*info));
    if (codec == PYALSOFT_CODEC_VORBIS) {
        if (sample_format != PYALSOFT_SAMPLE_FLOAT32) {
            return pyalsoft_fail(
                error,
                PYALSOFT_DECODER_UNSUPPORTED_FORMAT,
                "Vorbis output must use 32-bit floating point"
            );
        }
        return pyalsoft_probe_vorbis(data, data_size, info, error);
    }
    if (pyalsoft_ma_encoding(codec) == ma_encoding_format_unknown ||
        pyalsoft_ma_format(sample_format) == ma_format_unknown) {
        return pyalsoft_fail(
            error,
            PYALSOFT_DECODER_UNSUPPORTED_FORMAT,
            "unsupported codec or sample format"
        );
    }
    return pyalsoft_probe_miniaudio(
        data, data_size, codec, sample_format, info, error
    );
}

static int32_t pyalsoft_decode_miniaudio(
    const void *data,
    size_t data_size,
    int32_t codec,
    int32_t sample_format,
    const struct pyalsoft_decoder_info *expected,
    void *pcm,
    struct pyalsoft_decoder_error *error
) {
    ma_decoder decoder;
    ma_decoder_config config;
    ma_result result;
    ma_uint64 frames_read = 0;

    config = ma_decoder_config_init(pyalsoft_ma_format(sample_format), 0, 0);
    config.encodingFormat = pyalsoft_ma_encoding(codec);
    result = ma_decoder_init_memory(data, data_size, &config, &decoder);
    if (result != MA_SUCCESS) {
        return pyalsoft_fail(
            error, PYALSOFT_DECODER_INVALID_DATA, ma_result_description(result)
        );
    }
    result = ma_decoder_read_pcm_frames(
        &decoder, pcm, expected->frame_count, &frames_read
    );
    ma_decoder_uninit(&decoder);
    if (frames_read != expected->frame_count) {
        return pyalsoft_fail(
            error,
            PYALSOFT_DECODER_TRUNCATED,
            "decoder returned fewer frames than declared"
        );
    }
    if (result != MA_SUCCESS && result != MA_AT_END) {
        return pyalsoft_fail(
            error, PYALSOFT_DECODER_INVALID_DATA, ma_result_description(result)
        );
    }
    return PYALSOFT_DECODER_SUCCESS;
}

static int32_t pyalsoft_decode_vorbis(
    const void *data,
    size_t data_size,
    const struct pyalsoft_decoder_info *expected,
    float *pcm,
    struct pyalsoft_decoder_error *error
) {
    stb_vorbis *decoder;
    uint64_t frames_read = 0;
    int vorbis_error = VORBIS__no_error;

    decoder = stb_vorbis_open_memory(
        (const unsigned char *)data, (int)data_size, &vorbis_error, NULL
    );
    if (decoder == NULL) {
        return pyalsoft_fail(
            error, PYALSOFT_DECODER_INVALID_DATA, "invalid Ogg Vorbis data"
        );
    }
    while (frames_read < expected->frame_count) {
        uint64_t remaining = expected->frame_count - frames_read;
        int frame_limit = INT_MAX / (int)expected->channels;
        int requested = remaining > (uint64_t)frame_limit
            ? frame_limit
            : (int)remaining;
        int sample_capacity = requested * (int)expected->channels;
        int decoded = stb_vorbis_get_samples_float_interleaved(
            decoder,
            (int)expected->channels,
            pcm + frames_read * expected->channels,
            sample_capacity
        );
        if (decoded <= 0) {
            break;
        }
        frames_read += (uint64_t)decoded;
    }
    vorbis_error = stb_vorbis_get_error(decoder);
    stb_vorbis_close(decoder);
    if (frames_read != expected->frame_count) {
        return pyalsoft_fail(
            error,
            PYALSOFT_DECODER_TRUNCATED,
            "Vorbis decoder returned fewer frames than declared"
        );
    }
    if (vorbis_error != VORBIS__no_error) {
        return pyalsoft_fail(
            error, PYALSOFT_DECODER_INVALID_DATA, "Vorbis decoding failed"
        );
    }
    return PYALSOFT_DECODER_SUCCESS;
}

int32_t pyalsoft_decoder_decode(
    const void *data,
    size_t data_size,
    int32_t codec,
    int32_t sample_format,
    struct pyalsoft_decoder_info *info,
    void **pcm,
    size_t *pcm_size,
    struct pyalsoft_decoder_error *error
) {
    int32_t result;
    size_t size;
    void *allocation;

    if (pcm == NULL || pcm_size == NULL) {
        return pyalsoft_fail(
            error, PYALSOFT_DECODER_INVALID_ARGUMENT, "output pointers are null"
        );
    }
    *pcm = NULL;
    *pcm_size = 0;
    result = pyalsoft_decoder_probe(
        data, data_size, codec, sample_format, info, error
    );
    if (result != PYALSOFT_DECODER_SUCCESS) {
        return result;
    }
    size = (size_t)(
        info->frame_count * info->channels * pyalsoft_sample_size(sample_format)
    );
    allocation = malloc(size);
    if (allocation == NULL) {
        return pyalsoft_fail(
            error,
            PYALSOFT_DECODER_ALLOCATION_FAILED,
            "could not allocate decoded PCM memory"
        );
    }
    if (codec == PYALSOFT_CODEC_VORBIS) {
        result = pyalsoft_decode_vorbis(
            data, data_size, info, (float *)allocation, error
        );
    } else {
        result = pyalsoft_decode_miniaudio(
            data, data_size, codec, sample_format, info, allocation, error
        );
    }
    if (result != PYALSOFT_DECODER_SUCCESS) {
        free(allocation);
        return result;
    }
    *pcm = allocation;
    *pcm_size = size;
    return PYALSOFT_DECODER_SUCCESS;
}

void pyalsoft_decoder_free(void *pcm) {
    free(pcm);
}
