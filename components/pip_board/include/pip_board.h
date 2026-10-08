#pragma once
#include <stdint.h>

// Called once from the Rust app task. Returns an ESP-IDF error code.
int32_t pip_board_init(void);
void pip_print_diagnostics(void);
// Process bounded USB debug commands; call from the app loop.
const char *pip_debug_poll(void);
int32_t pip_ui_card(const char *title, const char *body, const char *const *options, uint32_t count, uint32_t kind);
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

// Renderer entry points; init is called while holding the LVGL lock.
struct lv_display_t;
void pip_ui_init(struct lv_display_t *display);
int32_t pip_ui_state(const char *name);
uint32_t pip_ui_interaction(void);
void pip_ui_tap(int32_t x, int32_t y);
void pip_ui_drag(int32_t x0, int32_t y0, int32_t x1, int32_t y1);
int32_t pip_legacy_card(const char *title, const char *body, const char *const *options, uint32_t count);
uint32_t pip_legacy_choice(void);
void pip_ui_inspect(void);
