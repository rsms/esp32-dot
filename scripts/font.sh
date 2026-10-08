#!/bin/sh
set -eu
PIP_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PIP_ROOT"
.tools/python/bin/fonttools varLib.instancer assets/fonts/InterVariable.ttf \
    wght=450 opsz=32 --output .tools/Inter-Regular.ttf
for size in 20 28 40; do
    .tools/font-converter/node_modules/.bin/lv_font_conv \
        --font .tools/Inter-Regular.ttf --size "$size" --bpp 4 --range 0x20-0x7e \
        --format lvgl --no-compress --lv-font-name "inter_$size" \
        -o "components/pip_board/inter_$size.c"
done
