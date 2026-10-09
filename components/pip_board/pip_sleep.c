#include "pip_sleep.h"
#include "esp_random.h"
#include "esp_timer.h"
#include <math.h>
#include <stdio.h>
#include <string.h>

// A small opaque RGB565 tile keeps invalidation and rotation local to the blob.
// No bitmap assets: evaluate the capsule and eye arcs in its rotating coordinates.
enum { TILE_W = 128, TILE_H = 104, FLOOR = 360, MIN_X = 93, MAX_X = 355 };
static uint16_t pixels[TILE_W * TILE_H];
static uint16_t letters_small[18 * 34], letters_big[30 * 34];
static lv_obj_t *blob, *zz, *big_z;
static lv_timer_t *timer;
static uint32_t epoch, frames, draw_us;
static float x = 225, from_x, to_x;
static bool tumbling, repeat_tumble, frozen;
static unsigned phase_ms;
static float last_width, last_height, last_angle;
static const float PI = 3.14159265358979323846f;

static float clamp(float v, float a, float b) { return fminf(b, fmaxf(a, v)); }
static float smooth(float t) { return t * t * (3 - 2 * t); }
static uint16_t gray(float coverage)
{
    unsigned c = (unsigned)(255 * clamp(coverage, 0, 1) + 0.5f);
    return ((c >> 3) << 11) | ((c >> 2) << 5) | (c >> 3);
}

static float eye_distance(float u, float v)
{
    // Lower 120 degrees of an 11px circle, including round stroke caps.
    float a = fabsf(u);
    if (v >= a * 0.577350269f) return fabsf(sqrtf(u * u + v * v) - 11);
    float dx = a - 9.52627944f, dy = v - 5.5f;
    return sqrtf(dx * dx + dy * dy);
}

static void raster_blob(float width, float height, float angle)
{
    int64_t started = esp_timer_get_time();
    float c = cosf(angle), s = sinf(angle), radius = height * 0.5f;
    float half_line = (width - height) * 0.5f;
    // Exact capsule support function: keep its bottom on the same baseline.
    float center_y = TILE_H - radius - fabsf(s) * half_line;
    float eye_scale = width / 122;
    for (int row = 0; row < TILE_H; row++) {
        float dy = row + 0.5f - center_y;
        for (int col = 0; col < TILE_W; col++) {
            float dx = col + 0.5f - TILE_W * 0.5f;
            float u = c * dx + s * dy, v = -s * dx + c * dy;
            float q = fmaxf(fabsf(u) - half_line, 0);
            float d2 = q * q + v * v;
            float coverage = d2 < (radius - 1) * (radius - 1) ? 1 :
                d2 > (radius + 1) * (radius + 1) ? 0 : radius + 0.5f - sqrtf(d2);
            if (coverage > 0) {
                float eu = (fabsf(u) - 18 * eye_scale), ev = v + 6;
                if (fabsf(eu) < 13 && ev > 3 && ev < 14) {
                    coverage = fminf(coverage, eye_distance(eu, ev) - 1.5f);
                }
            }
            pixels[row * TILE_W + col] = gray(coverage);
        }
    }
    lv_obj_invalidate(blob);
    draw_us = (uint32_t)(esp_timer_get_time() - started);
    frames++;
}

static float segment_distance(float x, float y, float ax, float ay, float bx, float by)
{
    float dx = bx - ax, dy = by - ay;
    float t = clamp(((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy), 0, 1);
    dx = x - ax - t * dx;
    dy = y - ay - t * dy;
    return sqrtf(dx * dx + dy * dy);
}

static void raster_letters(void)
{
    // Rounded monoline zZ, matching the supplied 448x368 reference.
    const float lines[][4] = {{3,17,14,17}, {14,17,3,29}, {3,29,14,29},
        {24,3,43,3}, {43,3,24,24}, {24,24,43,24}};
    for (int row = 0; row < 34; row++) for (int col = 0; col < 48; col++) {
        float d = 100;
        for (unsigned i = 0; i < 6; i++) d = fminf(d, segment_distance(
            col + 0.5f, row + 0.5f, lines[i][0], lines[i][1], lines[i][2], lines[i][3]));
        if (col < 18) letters_small[row * 18 + col] = gray(2.5f - d);
        else letters_big[row * 30 + col - 18] = gray(2.5f - d);
    }
}

// Monotone cubic angular interpolation through the storyboard's resting,
// quarter, upright, returning, and resting poses. The second half completes
// the roll faster, bringing the eyes upright again without a discontinuity.
static float turn(float t)
{
    const float angle[] = {0, 45, 90, 300, 360};
    const float tangent[] = {45, 45, 74.117647f, 93.333333f, 60};
    unsigned i = (unsigned)fminf(t * 4, 3);
    float u = t * 4 - i, u2 = u * u, u3 = u2 * u;
    return ((2 * u3 - 3 * u2 + 1) * angle[i] + (u3 - 2 * u2 + u) * tangent[i] +
        (-2 * u3 + 3 * u2) * angle[i + 1] + (u3 - u2) * tangent[i + 1]) * PI / 180;
}

static void draw(unsigned ms)
{
    float width = 122, height = 46, angle = 0;
    phase_ms = ms;
    if (tumbling) {
        float t = clamp(ms / 3000.0f, 0, 1), p = smooth(t);
        float roundness = sinf(PI * t);
        width -= 42 * roundness;
        height += 28 * roundness;
        angle = (to_x < from_x ? -1 : 1) * turn(t);
        x = from_x + (to_x - from_x) * p;
    } else if (ms >= 1000 && ms < 5000) {
        float breath = sinf(PI * (ms - 1000) / 4000.0f);
        breath *= breath;
        width -= 20 * breath;
        height += 12 * breath;
    }
    lv_obj_set_pos(blob, (int)lroundf(x) - TILE_W / 2, FLOOR - TILE_H);
    if (width != last_width || height != last_height || angle != last_angle) {
        raster_blob(width, height, angle);
        last_width = width;
        last_height = height;
        last_angle = angle;
    }
    float small_opacity = 0, big_opacity = 0;
    if (!tumbling && ms >= 6000 && ms < 9000) {
        float seconds = (ms - 6000) / 1000.0f;
        small_opacity = smooth(clamp(seconds * 2, 0, 1)) * smooth(clamp((2.3f - seconds) * 2, 0, 1));
        big_opacity = smooth(clamp((seconds - .5f) * 2, 0, 1)) * smooth(clamp((3 - seconds) * 2, 0, 1));
    }
    int zx = (int)fminf(x + 52, 392);
    lv_obj_set_pos(zz, zx, 272);
    lv_obj_set_pos(big_z, zx + 18, 272);
    lv_obj_set_style_opa(zz, (lv_opa_t)lroundf(small_opacity * 255), 0);
    lv_obj_set_style_opa(big_z, (lv_opa_t)lroundf(big_opacity * 255), 0);
}

static void begin_tumble(void)
{
    from_x = x;
    if (repeat_tumble) to_x = x < 224 ? MAX_X : MIN_X;
    else {
        // Uniformly choose from the allowed positions on either side, at
        // least 64px away. Near an edge this naturally favors moving inward.
        float left = fmaxf(x - 64 - MIN_X, 0);
        float right = fmaxf(MAX_X - x - 64, 0);
        float pick = (left + right) * (esp_random() / 4294967295.0f);
        to_x = (pick < left || right == 0) ? MIN_X + pick : x + 64 + pick - left;
    }
    tumbling = true;
}

static void tick(lv_timer_t *unused)
{
    (void)unused;
    if (frozen) return;
    unsigned ms = lv_tick_elaps(epoch);
    unsigned duration = tumbling ? 3000 : 9000;
    if (ms >= duration) {
        bool finished_cycle = !tumbling;
        if (tumbling) x = to_x;
        tumbling = false;
        if (repeat_tumble || (finished_cycle && esp_random() % 10 == 0)) begin_tumble();
        epoch = lv_tick_get();
        ms = 0;
    }
    // Still phases generate no refreshes after their entry frame.
    bool still = !tumbling && (ms < 1000 || (ms >= 5000 && ms < 6000));
    bool was_still = !tumbling && (phase_ms < 1000 || (phase_ms >= 5000 && phase_ms < 6000));
    if (still && was_still && ms >= phase_ms && ms / 1000 == phase_ms / 1000) {
        phase_ms = ms;
        return;
    }
    draw(ms);
}

void pip_sleep_stop(void)
{
    if (timer) lv_timer_delete(timer);
    timer = NULL;
    blob = zz = big_z = NULL;
    repeat_tumble = frozen = tumbling = false;
}

void pip_sleep_start(lv_obj_t *parent)
{
    x = 225;
    last_width = last_height = last_angle = -1;
    frames = 0;
    blob = lv_canvas_create(parent);
    lv_canvas_set_buffer(blob, pixels, TILE_W, TILE_H, LV_COLOR_FORMAT_RGB565);
    zz = lv_canvas_create(parent);
    lv_canvas_set_buffer(zz, letters_small, 18, 34, LV_COLOR_FORMAT_RGB565);
    big_z = lv_canvas_create(parent);
    lv_canvas_set_buffer(big_z, letters_big, 30, 34, LV_COLOR_FORMAT_RGB565);
    lv_obj_remove_flag(big_z, LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_remove_flag(blob, LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_remove_flag(zz, LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_SCROLLABLE);
    raster_letters();
    epoch = lv_tick_get();
    draw(0);
    timer = lv_timer_create(tick, 33, NULL);
}

void pip_sleep_debug(bool repeat, int32_t seek_ms)
{
    repeat_tumble = repeat;
    frozen = seek_ms >= 0;
    tumbling = false;
    x = 225;
    if (repeat) begin_tumble();
    epoch = lv_tick_get();
    draw(frozen ? (unsigned)seek_ms % (repeat ? 3001 : 9000) : 0);
}

void pip_sleep_inspect(void)
{
    printf(",\"sleep\":{\"phase\":\"%s\",\"ms\":%u,\"x\":%.1f,\"repeat\":%s,"
        "\"frozen\":%s,\"draws\":%u,\"raster_us\":%u}",
        tumbling ? "tumble" : phase_ms < 1000 ? "still" : phase_ms < 5000 ? "breathing" :
        phase_ms < 6000 ? "still" : "zz", phase_ms, (double)x,
        repeat_tumble ? "true" : "false", frozen ? "true" : "false", frames, draw_us);
}
