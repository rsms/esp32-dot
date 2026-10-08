#pragma once
#include <stdint.h>

// Called once from the Rust app task. Returns an ESP-IDF error code.
int32_t pip_board_init(void);
// Copies the text under the LVGL lock; RGB colors use 0xRRGGBB.
int32_t pip_ui_show(const char *text, uint32_t circle_rgb);
uint32_t pip_touch_count(void);
void pip_print_diagnostics(void);
// Process bounded USB debug commands; call from the app loop.
const char *pip_debug_poll(void);
int32_t pip_ui_card(const char *title, const char *body, const char *const *options, uint32_t count);
int32_t pip_ui_status(const char *text);
uint32_t pip_ui_choice(void);

typedef struct {
    char host[128];
    char token[65];
    uint16_t port;
} pip_network_settings_t;
int32_t pip_network_init(void);
void pip_network_poll(void);
int32_t pip_network_ready(void);
uint32_t pip_network_generation(void);
int32_t pip_network_load(pip_network_settings_t *settings);
int32_t pip_network_save(const char *ssid, const char *password, const pip_network_settings_t *settings);
int32_t pip_network_set_host(const char *host, uint16_t port);
