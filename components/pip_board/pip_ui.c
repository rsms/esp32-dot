#include "pip_board.h"
#include "pip_assets.h"
#include "pip_sleep.h"
#include "esp_lvgl_port.h"
#include "esp_err.h"
#include "lvgl.h"
#include "src/misc/lv_text_private.h"
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

LV_FONT_DECLARE(inter_55);
LV_FONT_DECLARE(inter_28);
LV_FONT_DECLARE(inter_40);
LV_FONT_DECLARE(inter_20);
LV_FONT_DECLARE(inter_choice);
LV_FONT_DECLARE(inter_confirm);
LV_FONT_DECLARE(inter_arrows);
LV_FONT_DECLARE(inter_check);
enum { WIDTH = 448, HEIGHT = 368, BLUE = 0x0028b9, RED = 0xa44200 };
static lv_obj_t *scene;
static unsigned option_count;
static char option_labels[3][65];
static bool confirming;
static lv_point_t pressed;
static bool pointer_down;
static lv_timer_t *attention_timer;
static lv_timer_t *idle_timer;
static atomic_uint choice;
static atomic_uint interaction;
static atomic_uint audio_state;
static char state[16] = "sleeping";
static char content[664];
static uint16_t offsets[168];
static unsigned pages, page;
static bool error_card, submitted;
static void render_page(void);
static void face(const char *name);

static void idle_activity(void)
{
    if (idle_timer && !strcmp(state, "idle")) lv_timer_reset(idle_timer);
}

static void idle_elapsed(lv_timer_t *timer)
{
    if (strcmp(state, "idle")) return;
    if (pointer_down) lv_timer_reset(timer);
    else face("sleeping");
}

static lv_obj_t *box(lv_obj_t *parent, int x, int y, int w, int h, uint32_t color, int radius)
{
    lv_obj_t *obj = lv_obj_create(parent);
    lv_obj_remove_style_all(obj);
    lv_obj_remove_flag(obj, LV_OBJ_FLAG_SCROLLABLE | LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_pos(obj, x, y);
    lv_obj_set_size(obj, w, h);
    lv_obj_set_style_bg_color(obj, lv_color_hex(color), 0);
    lv_obj_set_style_bg_opa(obj, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(obj, radius, 0);
    return obj;
}

static lv_obj_t *mask(const lv_image_dsc_t *image, int x, int y, uint32_t color)
{
    lv_obj_t *obj = lv_image_create(scene);
    lv_image_set_src(obj, image);
    lv_obj_set_pos(obj, x, y);
    lv_obj_set_style_image_recolor(obj, lv_color_hex(color), 0);
    lv_obj_set_style_image_recolor_opa(obj, LV_OPA_COVER, 0);
    return obj;
}

static void clear(uint32_t color)
{
    if (idle_timer) lv_timer_pause(idle_timer);
    pip_sleep_stop();
    lv_obj_clean(scene);
    lv_obj_set_style_bg_color(scene, lv_color_hex(color), 0);
}

static void dot(int x, int y, bool active, uint32_t background)
{
    lv_obj_t *obj = box(scene, x, y, 24, 24, active ? 0xffffff : background, LV_RADIUS_CIRCLE);
    lv_obj_set_style_border_width(obj, active ? 0 : 4, 0);
    lv_obj_set_style_border_color(obj, lv_color_white(), 0);
}

// Figma trims text to cap height. LVGL positions the complete font line box.
static lv_obj_t *text_at(lv_obj_t *parent, const char *text, int x, int cap_y, int width,
    const lv_font_t *font, uint32_t color)
{
    lv_font_glyph_dsc_t glyph;
    lv_font_get_glyph_dsc(font, &glyph, 'H', 0);
    int inset = font->line_height - font->base_line - glyph.box_h - glyph.ofs_y;
    lv_obj_t *label = lv_label_create(parent);
    lv_obj_set_style_text_font(label, font, 0);
    lv_obj_set_style_text_color(label, lv_color_hex(color), 0);
    lv_obj_set_style_text_line_space(label, 64 - font->line_height, 0);
    lv_obj_set_pos(label, x, cap_y - inset);
    lv_obj_set_width(label, width);
    lv_label_set_text(label, text);
    return label;
}

static void face(const char *name)
{
    uint32_t color = !strcmp(name, "listening") ? 0xff472a : !strcmp(name, "attention") ? 0xffd900 : 0;
    clear(color);
    unsigned audio = atomic_load(&audio_state);
    atomic_store(&audio_state, !strcmp(name, "listening") ? ((audio & ~3u) + 4) | 1 : audio & ~3u);
    snprintf(state, sizeof(state), "%s", name);
    if (!strcmp(name, "idle")) {
        lv_timer_reset(idle_timer);
        lv_timer_resume(idle_timer);
    }
    if (!strcmp(name, "attention")) {
        mask(&pip_face_attention, 104, 64, 0);
    } else if (!strcmp(name, "thinking")) {
        mask(&pip_face_thinking, 104, 64, 0xffffff);
        for (int i = 0; i < 3; i++) dot(312 + i * 36, 40, true, 0);
    } else if (!strcmp(name, "listening")) {
        mask(&pip_face_listening, 104, 64, 0xffffff);
        mask(&pip_ears, 331, 98, 0xffffff);
        mask(&pip_ears_left, 56, 98, 0xffffff);
    } else if (!strcmp(name, "sleeping")) {
        pip_sleep_start(scene);
    } else {
        mask(&pip_face_outline, 104, 64, 0xffffff);
        mask(&pip_eye_happy, 160, 140, 0xffffff);
        mask(&pip_eye_happy, 236, 140, 0xffffff);
        mask(&pip_smile, 178, 212, 0xffffff);
    }
}

static int cap_height(const lv_font_t *font)
{
    lv_font_glyph_dsc_t glyph;
    lv_font_get_glyph_dsc(font, &glyph, 'H', 0);
    return glyph.box_h;
}

static void indicators(uint32_t background)
{
    unsigned capacity = 9 - option_count;
    unsigned visible = pages < capacity ? pages : capacity;
    unsigned first = page > capacity / 2 ? page - capacity / 2 : 0;
    if (first + visible > pages) first = pages - visible;
    int x = (WIDTH - (int)(visible + option_count) * 36) / 2 - (option_count ? 2 : 0);
    for (unsigned i = first; i < first + visible; i++, x += 36) {
        if (option_count) mask(i == page ? &pip_page_full : &pip_page_empty, x + 6, 312, 0xffffff);
        else dot(x + 6, 312, i == page, background);
    }
    for (unsigned i = 0; i < option_count; i++, x += 36) {
        bool active = page == pages + i;
        if (active) box(scene, x, 306, 36, 36, 0xffffff, LV_RADIUS_CIRCLE);
        char letter[] = {(char)('A' + i), 0};
        lv_obj_t *label = text_at(scene, letter, x, 324 - cap_height(&inter_choice) / 2,
            36, &inter_choice, active ? background : 0xffffff);
        lv_obj_set_style_text_align(label, LV_TEXT_ALIGN_CENTER, 0);
    }
}

static void option_text(const char *text)
{
    const lv_font_t *fonts[] = {&inter_55, &inter_40, &inter_28, &inter_20};
    const lv_font_t *font = fonts[0];
    unsigned lines = 1;
    int line_height = 64;
    for (unsigned i = 0; i < sizeof(fonts) / sizeof(fonts[0]); i++) {
        font = fonts[i];
        line_height = i ? font->line_height : 64;
        unsigned offset = 0;
        lines = 0;
        while (text[offset]) {
            uint32_t length = lv_text_get_next_line(text + offset, font, 0, 352, NULL, LV_TEXT_FLAG_NONE);
            if (!length) break;
            offset += length;
            lines++;
        }
        if (!lines) lines = 1;
        if ((int)(lines - 1) * line_height + cap_height(font) <= 208) break;
    }
    int height = (int)(lines - 1) * line_height + cap_height(font);
    if (height > 208 && strchr(text, '\n')) {
        // Excess explicit line breaks must not hide part of an option.
        char compact[65];
        snprintf(compact, sizeof(compact), "%s", text);
        for (char *p = compact; *p; p++) if (*p == '\n') *p = ' ';
        option_text(compact);
        return;
    }
    lv_obj_t *label = text_at(scene, text, 48, 24 + (256 - height) / 2, 352, font, 0);
    lv_obj_set_style_text_line_space(label, line_height - font->line_height, 0);
}

static void render_page(void)
{
    uint32_t color = error_card ? RED : BLUE;
    clear(color);
    snprintf(state, sizeof(state), "message");
    if (page < pages) {
        char text[664];
        size_t size = offsets[page + 1] - offsets[page];
        memcpy(text, content + offsets[page], size);
        while (size && (text[size - 1] == ' ' || text[size - 1] == '\n')) size--;
        text[size] = 0;
        text_at(scene, text, 40, 40, 368, &inter_55, 0xffffff);
    } else if (option_count && !confirming) {
        snprintf(state, sizeof(state), "choice");
        box(scene, 24, 24, 400, 256, 0xffffff, page == pages ? 32 : 34);
        option_text(option_labels[page - pages]);
    } else if (option_count) {
        snprintf(state, sizeof(state), "confirm");
        box(scene, 128, 56, 192, 192, 0xffffff, LV_RADIUS_CIRCLE);
        lv_obj_t *check = text_at(scene, "✓", 192, 110, 64, &inter_check, 0);
        lv_obj_set_style_text_align(check, LV_TEXT_ALIGN_CENTER, 0);
        lv_obj_set_style_text_line_space(check, 0, 0);
        lv_label_set_long_mode(check, LV_LABEL_LONG_CLIP);
        text_at(scene, "Confirm", 182, 186, 100, &inter_confirm, 0);
        lv_obj_t *left = text_at(scene, "←", 42, 132, 60, &inter_arrows, 0xffffff);
        lv_obj_t *right = text_at(scene, "→", 356, 132, 60, &inter_arrows, 0xffffff);
        lv_obj_set_style_text_opa(left, LV_OPA_50, 0);
        lv_obj_set_style_text_opa(right, LV_OPA_50, 0);
    } else {
        lv_obj_t *button = box(scene, 161, 104, 128, 128, 0xffffff, LV_RADIUS_CIRCLE);
        lv_obj_t *label = lv_label_create(button);
        lv_obj_set_style_text_font(label, &inter_check, 0);
        lv_obj_set_style_text_color(label, lv_color_hex(color), 0);
        lv_label_set_text(label, "✓");
        lv_obj_center(label);
    }
    indicators(color);
}

static void attention_done(lv_timer_t *timer)
{
    (void)timer;
    attention_timer = NULL;
    render_page();
}

static void cancel_attention(void)
{
    if (attention_timer) {
        lv_timer_delete(attention_timer);
        attention_timer = NULL;
    }
}

static bool showing_card(void)
{
    return !strcmp(state, "message") || !strcmp(state, "choice") || !strcmp(state, "confirm");
}

static void navigate(int direction)
{
    if (!showing_card() || submitted) return;
    unsigned last = pages + (option_count ? option_count - 1 : 0);
    if (direction < 0 && page) page--;
    else if (direction > 0 && page < last) page++;
    confirming = false;
    render_page();
}

static void submit(unsigned index)
{
    submitted = true;
    atomic_store(&choice, index + 1);
    face("thinking");
}

static void tap(int x, int y)
{
    idle_activity();
    if (!strcmp(state, "attention") && pages) {
        cancel_attention();
        render_page();
    } else if (showing_card() && !submitted) {
        if (option_count && page >= pages) {
            if (confirming) {
                int dx = x - 224, dy = y - 152;
                if (dx * dx + dy * dy <= 96 * 96) submit(page - pages);
                else if (x < 128) navigate(-1);
                else if (x >= 320) navigate(1);
            } else if (x >= 24 && x < 424 && y >= 24 && y < 280) {
                confirming = true;
                render_page();
            } else if (x < 128) navigate(-1);
            else if (x >= 320) navigate(1);
        } else if (x < 128 && page) navigate(-1);
        else if (x >= 320) navigate(1);
        else if (!option_count && page == pages && x >= 130 && x < 320) submit(0);
    } else if (!strcmp(state, "sleeping") || !strcmp(state, "idle")) {
        face("listening");
        atomic_store(&interaction, 1);
    } else if (!strcmp(state, "listening")) {
        unsigned audio = atomic_load(&audio_state);
        face("thinking");
        atomic_store(&audio_state, (audio & ~3u) | 2);
        atomic_store(&interaction, 2);
    }
}

static void release(int x0, int y0, int x1, int y1)
{
    idle_activity();
    int dx = x1 - x0, dy = y1 - y0;
    if (abs(dx) >= 48 && abs(dx) > abs(dy)) navigate(dx < 0 ? 1 : -1);
    else if (abs(dx) < 24 && abs(dy) < 24) tap(x1, y1);
}

static void pointer_event(lv_event_t *event)
{
    lv_indev_t *input = lv_event_get_indev(event);
    if (!input) return;
    lv_point_t point;
    lv_indev_get_point(input, &point);
    if (lv_event_get_code(event) == LV_EVENT_PRESSED) {
        idle_activity();
        pressed = point;
        pointer_down = true;
    } else if (lv_event_get_code(event) == LV_EVENT_RELEASED && pointer_down) {
        pointer_down = false;
        release(pressed.x, pressed.y, point.x, point.y);
    } else if (lv_event_get_code(event) == LV_EVENT_PRESS_LOST) {
        pointer_down = false;
        idle_activity();
    }
}

void pip_ui_init(lv_display_t *display)
{
    lv_obj_t *screen = lv_display_get_screen_active(display);
    lv_obj_set_style_bg_color(screen, lv_color_black(), 0);
    lv_obj_remove_flag(screen, LV_OBJ_FLAG_SCROLLABLE);
    scene = box(screen, 0, 0, WIDTH, HEIGHT, 0, 56);
    // All children are inset within the rounded background. Clipping the
    // children into the rounded corners adds unnecessary offscreen compositing.
    lv_obj_add_flag(scene, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(scene, pointer_event, LV_EVENT_PRESSED, NULL);
    lv_obj_add_event_cb(scene, pointer_event, LV_EVENT_RELEASED, NULL);
    lv_obj_add_event_cb(scene, pointer_event, LV_EVENT_PRESS_LOST, NULL);
    idle_timer = lv_timer_create(idle_elapsed, 10000, NULL);
    face("sleeping");
}

int32_t pip_ui_state(const char *name)
{
    if (strcmp(name, "sleeping") && strcmp(name, "idle") && strcmp(name, "thinking") &&
        strcmp(name, "listening") && strcmp(name, "attention")) return ESP_ERR_INVALID_ARG;
    if (!lvgl_port_lock(1000)) return ESP_ERR_TIMEOUT;
    cancel_attention();
    face(name);
    lvgl_port_unlock();
    return ESP_OK;
}

int32_t pip_ui_card(const char *title, const char *body, const char *const *labels,
    uint32_t count, uint32_t kind)
{
    if (!title || !body || !labels || count < 1 || count > 3) return ESP_ERR_INVALID_ARG;
    if (!lvgl_port_lock(1000)) return ESP_ERR_TIMEOUT;
    cancel_attention();
    snprintf(content, sizeof(content), "%s%s%s", title, *title ? "\n" : "", body);
    option_count = kind == 1 ? count : 0;
    for (unsigned i = 0; i < option_count; i++) snprintf(option_labels[i], sizeof(option_labels[i]), "%s", labels[i]);
    confirming = false;
    pages = page = 0;
    unsigned offset = 0;
    offsets[0] = 0;
    while (content[offset] && pages < 166) {
        for (int line = 0; line < 4 && content[offset]; line++) {
            offset += lv_text_get_next_line(content + offset, &inter_55, 0, 368, NULL, LV_TEXT_FLAG_NONE);
        }
        offsets[++pages] = offset;
    }
    error_card = kind == 2;
    submitted = false;
    atomic_store(&choice, 0);
    face("attention");
    attention_timer = lv_timer_create(attention_done, 900, NULL);
    lv_timer_set_repeat_count(attention_timer, 1);
    lvgl_port_unlock();
    return ESP_OK;
}

uint32_t pip_ui_choice(void) { return atomic_exchange(&choice, 0); }
uint32_t pip_ui_interaction(void) { return atomic_exchange(&interaction, 0); }

void pip_ui_tap(int32_t x, int32_t y)
{
    if (x < 0 || x >= WIDTH || y < 0 || y >= HEIGHT || !lvgl_port_lock(1000)) return;
    tap(x, y);
    lvgl_port_unlock();
}

void pip_ui_drag(int32_t x0, int32_t y0, int32_t x1, int32_t y1)
{
    if (x0 < 0 || x0 >= WIDTH || x1 < 0 || x1 >= WIDTH ||
        y0 < 0 || y0 >= HEIGHT || y1 < 0 || y1 >= HEIGHT || !lvgl_port_lock(1000)) return;
    release(x0, y0, x1, y1);
    lvgl_port_unlock();
}

void pip_ui_inspect(void)
{
    if (!lvgl_port_lock(1000)) return;
    printf("PIPEVENT {\"type\":\"ui\",\"state\":\"%s\",\"page\":%u,\"pages\":%u",
        state, page, pages);
    if (!strcmp(state, "sleeping")) pip_sleep_inspect();
    printf(",\"audio\":{\"state\":%u,\"samples\":%u,\"peak\":%u}}\n",
        atomic_load(&audio_state), (unsigned)pip_audio_samples(), (unsigned)pip_audio_peak());
    lvgl_port_unlock();
}

void pip_ui_sleep_debug(uint32_t repeat, int32_t seek_ms)
{
    if (!lvgl_port_lock(1000)) return;
    if (!strcmp(state, "sleeping")) pip_sleep_debug(repeat != 0, seek_ms);
    lvgl_port_unlock();
}

uint32_t pip_ui_audio_state(void) { return atomic_load(&audio_state); }

void pip_ui_audio_complete(uint32_t generation)
{
    if (!lvgl_port_lock(1000)) return;
    unsigned audio = atomic_load(&audio_state);
    if ((audio & ~3u) == generation && (audio & 3) != 0 &&
        (!strcmp(state, "thinking") || !strcmp(state, "listening"))) face("idle");
    lvgl_port_unlock();
}

void pip_ui_audio_finish(uint32_t generation)
{
    if (!lvgl_port_lock(1000)) return;
    if (atomic_load(&audio_state) == (generation | 1) && !strcmp(state, "listening")) {
        face("thinking");
        atomic_store(&audio_state, generation | 2);
    }
    lvgl_port_unlock();
}
