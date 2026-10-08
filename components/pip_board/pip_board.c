#include "pip_board.h"

#include <stdatomic.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "esp_lcd_touch.h"
#include "bsp/esp-bsp.h"
#include "bsp/display.h"
#include "bsp/touch.h"
#include "esp_check.h"
#include "esp_heap_caps.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lvgl_port.h"
#include "esp_psram.h"
#include "esp_timer.h"
#include "esp_rom_crc.h"
#include "driver/usb_serial_jtag.h"
#include "driver/usb_serial_jtag_vfs.h"
#include "lvgl.h"

static const char *TAG = "pip";
static lv_obj_t *circle;
static lv_obj_t *label;
static atomic_uint taps;
static lv_display_t *ui_display;
static uint16_t *screen_pixels;
static uint32_t screen_flushes;
static lv_obj_t *choice_buttons[3];
static lv_obj_t *card_status;
static atomic_uint choice;
enum { UI_WIDTH = BSP_LCD_V_RES, UI_HEIGHT = BSP_LCD_H_RES };
LV_FONT_DECLARE(inter_40);
LV_FONT_DECLARE(inter_28);
LV_FONT_DECLARE(inter_20);

static void capture_flush(lv_event_t *event)
{
    // LVGL 9.2 PARTIAL mode: active buffer is the exact upcoming flush, before
    // the port rotates and byte-swaps it for the wire. Preserve logical RGB565.
    const lv_area_t *area = lv_event_get_param(event);
    const lv_draw_buf_t *buffer = lv_display_get_buf_active(ui_display);
    const size_t row_bytes = lv_area_get_width(area) * sizeof(uint16_t);
    for (int y = area->y1; y <= area->y2; y++) {
        memcpy(screen_pixels + y * UI_WIDTH + area->x1,
            buffer->data + (y - area->y1) * buffer->header.stride, row_bytes);
    }
    screen_flushes++;
}

static void round_area(lv_event_t *event)
{
    // Both panel variants require even starting coordinates and pixel counts.
    lv_area_t *area = lv_event_get_param(event);
    area->x1 &= ~1;
    area->y1 &= ~1;
    area->x2 |= 1;
    area->y2 |= 1;
}

static void tapped(lv_event_t *event)
{
    (void)event;
    atomic_fetch_add_explicit(&taps, 1, memory_order_relaxed);
}

int32_t pip_board_init(void)
{
    ESP_RETURN_ON_ERROR(bsp_i2c_init(), TAG, "I2C initialization");
    // Probe touch BEFORE creating the panel: BSP selects the revision's X gap.
    // The BSP returns an error when neither known controller responds.
    esp_lcd_touch_handle_t touch = NULL;
    ESP_RETURN_ON_ERROR(bsp_touch_new(NULL, &touch), TAG, "touch/revision detection");

    esp_lcd_panel_handle_t panel = NULL;
    esp_lcd_panel_io_handle_t io = NULL;
    const bsp_display_config_t panel_config = {0};
    ESP_RETURN_ON_ERROR(bsp_display_new(&panel_config, &panel, &io), TAG, "panel initialization");
    // The BSP waits only 100 ms after sleep-out and none after display-on.
    // Complete the panel's settling interval before starting asynchronous draws.
    vTaskDelay(pdMS_TO_TICKS(30));
    ESP_RETURN_ON_ERROR(esp_lcd_panel_disp_on_off(panel, true), TAG, "display on after sleep-out");
    vTaskDelay(pdMS_TO_TICKS(10));
    // Software rotation skips the port's normal panel orientation setup.
    // Explicitly restore native addressing after the panel has woken up.
    ESP_RETURN_ON_ERROR(esp_lcd_panel_swap_xy(panel, false), TAG, "native panel axes");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_mirror(panel, false, false), TAG, "native panel orientation");
    ESP_RETURN_ON_ERROR(bsp_display_brightness_set(35), TAG, "brightness");

    lvgl_port_cfg_t port_config = ESP_LVGL_PORT_INIT_CONFIG();
    port_config.task_stack = 6144;
    ESP_RETURN_ON_ERROR(lvgl_port_init(&port_config), TAG, "LVGL initialization");
    // Create the display, configure rotation, and build the first scene atomically.
    ESP_RETURN_ON_FALSE(lvgl_port_lock(1000), ESP_ERR_TIMEOUT, TAG, "LVGL initialization lock");
    const lvgl_port_display_cfg_t display_config = {
        .io_handle = io,
        .panel_handle = panel,
        .buffer_size = BSP_LCD_H_RES * 32,
        .double_buffer = true,
        .hres = BSP_LCD_H_RES,
        .vres = BSP_LCD_V_RES,
        .color_format = LV_COLOR_FORMAT_RGB565,
        .flags = {
            .buff_dma = true,
            .swap_bytes = true,
            .sw_rotate = true,
        },
    };
    // QSPI panel: use the SPI display port, not the BSP's RGB-display wrapper.
    lv_display_t *display = lvgl_port_add_disp(&display_config);
    ESP_RETURN_ON_FALSE(display, ESP_ERR_NO_MEM, TAG, "display buffers");
    ui_display = display;
    screen_pixels = heap_caps_calloc(UI_WIDTH * UI_HEIGHT, sizeof(uint16_t),
        MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    ESP_RETURN_ON_FALSE(screen_pixels, ESP_ERR_NO_MEM, TAG, "screenshot buffer");
    lv_display_add_event_cb(display, capture_flush, LV_EVENT_FLUSH_START, NULL);
    const lvgl_port_touch_cfg_t touch_config = {
        .disp = display,
        .handle = touch,
    };
    ESP_RETURN_ON_FALSE(lvgl_port_add_touch(&touch_config), ESP_FAIL, TAG, "touch input");
    lv_display_add_event_cb(display, round_area, LV_EVENT_INVALIDATE_AREA, NULL);
    // The enclosure mounts the native portrait panel clockwise. The LVGL port
    // maps this rotation counterclockwise into panel memory (448 x 368 UI).
    // LVGL applies the inverse transform to the associated touch input.
    lv_display_set_rotation(display, LV_DISPLAY_ROTATION_90);
    lv_display_set_antialiasing(display, true);
    lv_obj_t *screen = lv_display_get_screen_active(display);
    lv_obj_set_style_bg_color(screen, lv_color_black(), 0);
    lv_obj_remove_flag(screen, LV_OBJ_FLAG_SCROLLABLE);

    circle = lv_obj_create(screen);
    lv_obj_remove_style_all(circle);
    lv_obj_set_size(circle, 120, 120);
    lv_obj_align(circle, LV_ALIGN_CENTER, 0, -34);
    lv_obj_set_style_radius(circle, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_opa(circle, LV_OPA_COVER, 0);
    lv_obj_set_style_bg_color(circle, lv_color_hex(0xf5f5f0), 0);
    lv_obj_add_flag(circle, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_remove_flag(circle, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_event_cb(circle, tapped, LV_EVENT_CLICKED, NULL);

    label = lv_label_create(screen);
    lv_obj_set_style_text_font(label, &inter_40, 0);
    lv_obj_set_style_text_color(label, lv_color_hex(0xf5f5f0), 0);
    lv_label_set_text(label, "hello");
    lv_obj_align_to(label, circle, LV_ALIGN_OUT_BOTTOM_MID, 0, 32);
    ESP_LOGI(TAG, "UI=%ldx%ld; landscape, software rotation",
        (long)lv_display_get_horizontal_resolution(display),
        (long)lv_display_get_vertical_resolution(display));
    lvgl_port_unlock();
    const usb_serial_jtag_driver_config_t usb_config = {
        .rx_buffer_size = 4096,
        .tx_buffer_size = 2048,
    };
    ESP_RETURN_ON_ERROR(usb_serial_jtag_driver_install(&usb_config), TAG, "USB debug driver");
    usb_serial_jtag_vfs_use_driver();
    fcntl(STDIN_FILENO, F_SETFL, O_NONBLOCK);
    return ESP_OK;
}

static bool screenshot_write(const char *data, size_t size)
{
    // Queue whole lines with backpressure. Console printf writes one byte at a
    // time and may silently drop bytes after its short TX timeout.
    return usb_serial_jtag_write_bytes(data, size, pdMS_TO_TICKS(1000)) == size;
}

static void send_screenshot(void)
{
    const size_t size = UI_WIDTH * UI_HEIGHT * sizeof(uint16_t);
    uint8_t *snapshot = heap_caps_malloc(size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!snapshot || !lvgl_port_lock(1000)) {
        free(snapshot);
        puts("PIPERROR screenshot unavailable");
        return;
    }
    // Freeze only the copy, not the USB transfer. Animations can keep running.
    memcpy(snapshot, screen_pixels, size);
    uint32_t flushes = screen_flushes;
    int64_t timestamp = esp_timer_get_time() / 1000;
    lvgl_port_unlock();
    uint32_t crc = esp_rom_crc32_le(0, snapshot, size);
    char line[300];
    int length = snprintf(line, sizeof(line), "\nPIPSHOT 1 %u %u RGB565LE %u %08lx %lu %lld\n",
        UI_WIDTH, UI_HEIGHT, (unsigned)size, (unsigned long)crc,
        (unsigned long)flushes, (long long)timestamp);
    // Keep ordinary console logs between captures. LVGL rendering continues.
    flockfile(stdout);
    fflush(stdout);
    if (!screenshot_write(line, length)) {
        goto done;
    }
    // ASCII framing survives console newline conversion. CRC and row offsets
    // let the host reject truncated/corrupted captures instead of saving them.
    static const char hex[] = "0123456789abcdef";
    for (size_t offset = 0; offset < size; offset += 128) {
        size_t count = size - offset < 128 ? size - offset : 128;
        length = snprintf(line, sizeof(line), "PIPDATA %u ", (unsigned)offset);
        for (size_t i = 0; i < count; i++) {
            line[length++] = hex[snapshot[offset + i] >> 4];
            line[length++] = hex[snapshot[offset + i] & 15];
        }
        line[length++] = '\n';
        if (!screenshot_write(line, length)) {
            goto done;
        }
        // USB may stay writable continuously. Give IDLE0 CPU time so a large
        // capture cannot starve the task watchdog while the LVGL task runs.
        if ((offset / 128) % 8 == 7) {
            vTaskDelay(1);
        }
    }
    screenshot_write("PIPEND\n", 7);
done:
    funlockfile(stdout);
    free(snapshot);
}

const char *pip_debug_poll(void)
{
    static char command[4096];
    static size_t length;
    static bool overflow;
    char byte;
    while (read(STDIN_FILENO, &byte, 1) == 1) {
        if (byte == '\n' || byte == '\r') {
            command[length] = '\0';
            if (!overflow && strcmp(command, "screenshot") == 0) {
                send_screenshot();
            } else if (!overflow && length != 0) {
                length = 0;
                return command;
            }
            length = 0;
            overflow = false;
        } else if (length < sizeof(command) - 1) {
            command[length++] = byte;
        } else {
            overflow = true;
        }
    }
    return NULL;
}

static void option_clicked(lv_event_t *event)
{
    uint32_t index = (uintptr_t)lv_event_get_user_data(event);
    unsigned expected = 0;
    if (atomic_compare_exchange_strong(&choice, &expected, index + 1)) {
        for (unsigned i = 0; i < 3; i++) {
            if (choice_buttons[i]) {
                lv_obj_remove_flag(choice_buttons[i], LV_OBJ_FLAG_CLICKABLE);
                lv_obj_set_style_bg_color(choice_buttons[i], lv_color_hex(i == index ? 0xb6caff : 0x242426), 0);
            }
        }
    }
}

int32_t pip_ui_card(const char *title, const char *body, const char *const *options, uint32_t count)
{
    if (!title || !body || !options || count < 1 || count > 3) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!lvgl_port_lock(1000)) {
        return ESP_ERR_TIMEOUT;
    }
    lv_obj_t *screen = lv_display_get_screen_active(ui_display);
    lv_obj_clean(screen);
    circle = label = card_status = NULL;
    memset(choice_buttons, 0, sizeof(choice_buttons));
    atomic_store(&choice, 0);

    lv_obj_t *heading = lv_label_create(screen);
    lv_obj_set_style_text_font(heading, &inter_28, 0);
    lv_obj_set_style_text_color(heading, lv_color_hex(0xf5f5f0), 0);
    lv_obj_set_pos(heading, 24, 20);
    lv_obj_set_size(heading, UI_WIDTH - 48, 70);
    lv_label_set_long_mode(heading, LV_LABEL_LONG_DOT);
    lv_label_set_text(heading, title);

    lv_obj_t *content = lv_obj_create(screen);
    lv_obj_remove_style_all(content);
    lv_obj_set_pos(content, 24, 100);
    lv_obj_set_size(content, UI_WIDTH - 48, 160);
    lv_obj_set_scroll_dir(content, LV_DIR_VER);
    lv_obj_set_scrollbar_mode(content, LV_SCROLLBAR_MODE_AUTO);
    lv_obj_t *text = lv_label_create(content);
    lv_obj_set_width(text, UI_WIDTH - 56);
    lv_obj_set_style_text_font(text, &inter_20, 0);
    lv_obj_set_style_text_color(text, lv_color_hex(0xc8c8cc), 0);
    lv_label_set_text(text, body);

    int width = (UI_WIDTH - 48 - (count - 1) * 8) / count;
    for (uint32_t i = 0; i < count; i++) {
        lv_obj_t *button = lv_button_create(screen);
        choice_buttons[i] = button;
        lv_obj_remove_style_all(button);
        lv_obj_set_pos(button, 24 + i * (width + 8), UI_HEIGHT - 84);
        lv_obj_set_size(button, width, 60);
        lv_obj_set_style_radius(button, 14, 0);
        lv_obj_set_style_bg_opa(button, LV_OPA_COVER, 0);
        lv_obj_set_style_bg_color(button, lv_color_hex(0x343438), 0);
        lv_obj_remove_flag(button, LV_OBJ_FLAG_SCROLLABLE);
        lv_obj_add_event_cb(button, option_clicked, LV_EVENT_CLICKED, (void *)(uintptr_t)i);
        lv_obj_t *text = lv_label_create(button);
        lv_obj_set_width(text, width - 12);
        lv_obj_set_style_text_font(text, &inter_20, 0);
        lv_obj_set_style_text_color(text, lv_color_hex(0xf5f5f0), 0);
        lv_obj_set_style_text_align(text, LV_TEXT_ALIGN_CENTER, 0);
        lv_label_set_text(text, options[i]);
        lv_obj_center(text);
    }
    lvgl_port_unlock();
    return ESP_OK;
}

uint32_t pip_ui_choice(void)
{
    return atomic_exchange(&choice, 0);
}

int32_t pip_ui_status(const char *text)
{
    if (!text || !lvgl_port_lock(1000)) {
        return ESP_ERR_INVALID_STATE;
    }
    for (unsigned i = 0; i < 3; i++) {
        if (choice_buttons[i]) {
            lv_obj_delete(choice_buttons[i]);
            choice_buttons[i] = NULL;
        }
    }
    if (!card_status) {
        card_status = lv_label_create(lv_display_get_screen_active(ui_display));
        lv_obj_set_style_text_font(card_status, &inter_20, 0);
        lv_obj_set_style_text_color(card_status, lv_color_hex(0xb6caff), 0);
        lv_obj_set_style_text_align(card_status, LV_TEXT_ALIGN_CENTER, 0);
        lv_obj_set_size(card_status, UI_WIDTH - 48, 60);
        lv_obj_set_pos(card_status, 24, UI_HEIGHT - 76);
    }
    lv_label_set_text(card_status, text);
    lvgl_port_unlock();
    return ESP_OK;
}

int32_t pip_ui_show(const char *text, uint32_t circle_rgb)
{
    if (!text || !circle || !label) {
        return ESP_ERR_INVALID_STATE;
    }
    if (!lvgl_port_lock(1000)) {
        return ESP_ERR_TIMEOUT;
    }
    lv_label_set_text(label, text);
    lv_obj_set_style_bg_color(circle, lv_color_hex(circle_rgb), 0);
    lv_obj_align_to(label, circle, LV_ALIGN_OUT_BOTTOM_MID, 0, 32);
    lvgl_port_unlock();
    return ESP_OK;
}

uint32_t pip_touch_count(void)
{
    return atomic_load_explicit(&taps, memory_order_relaxed);
}

void pip_print_diagnostics(void)
{
    ESP_LOGI(TAG, "uptime=%lld ms; PSRAM=%u bytes; internal free=%u; PSRAM free=%u",
        (long long)(esp_timer_get_time() / 1000), (unsigned)esp_psram_get_size(),
        (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT),
        (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
}
