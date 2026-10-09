#include "pip_board.h"
#include "bsp/esp-bsp.h"
#include "esp_codec_dev.h"
#include "esp_err.h"
#include "esp_timer.h"
#include <stdatomic.h>

// Only the Rust audio task calls these functions. The display task never waits
// for codec I/O or the network. DAC/speaker output is not opened.
static esp_codec_dev_handle_t microphone;
static atomic_uint samples_read, peak_sample;

int32_t pip_audio_open(void)
{
    if (!microphone) microphone = bsp_audio_codec_microphone_init();
    if (!microphone) return ESP_FAIL;
    esp_codec_dev_sample_info_t format = {
        .sample_rate = 16000, .channel = 1, .bits_per_sample = 16,
    };
    int result = esp_codec_dev_open(microphone, &format);
    if (result != ESP_OK) return result;
    result = esp_codec_dev_set_in_gain(microphone, 30.0f);
    if (result != ESP_OK) esp_codec_dev_close(microphone);
    atomic_store(&samples_read, 0);
    atomic_store(&peak_sample, 0);
    return result;
}

int32_t pip_audio_read(int16_t *samples, uint32_t count)
{
    if (!microphone || count != 320) return ESP_ERR_INVALID_ARG;
    int result = esp_codec_dev_read(microphone, samples, count * sizeof(int16_t));
    if (result == ESP_OK) {
        unsigned peak = atomic_load(&peak_sample);
        for (unsigned i = 0; i < count; i++) {
            unsigned value = samples[i] < 0 ? -(int)samples[i] : samples[i];
            if (value > peak) peak = value;
        }
        atomic_store(&peak_sample, peak);
        atomic_fetch_add(&samples_read, count);
    }
    return result;
}

void pip_audio_close(void)
{
    if (microphone) esp_codec_dev_close(microphone);
}

uint32_t pip_audio_samples(void) { return atomic_load(&samples_read); }
uint32_t pip_audio_peak(void) { return atomic_load(&peak_sample); }
