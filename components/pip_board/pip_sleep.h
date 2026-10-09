#pragma once
#include "lvgl.h"
#include <stdbool.h>

// Called with the LVGL lock held; stop before deleting the parent children.
void pip_sleep_start(lv_obj_t *parent);
void pip_sleep_stop(void);
void pip_sleep_debug(bool repeat, int32_t seek_ms);
void pip_sleep_inspect(void);
