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

# Figma v1 message type: Medium, 27.5 design px at 2x.
.tools/python/bin/fonttools varLib.instancer assets/fonts/InterVariable.ttf \
    wght=500 opsz=27.5 --output .tools/Inter-Message.ttf
.tools/font-converter/node_modules/.bin/lv_font_conv \
    --font .tools/Inter-Message.ttf --size 55 --bpp 4 --range 0x20-0x7e \
    --format lvgl --no-compress --lv-font-name inter_55 -o components/pip_board/inter_55.c
.tools/python/bin/fonttools varLib.instancer assets/fonts/InterVariable.ttf \
    wght=500 opsz=32 --output .tools/Inter-Medium.ttf
.tools/font-converter/node_modules/.bin/lv_font_conv \
    --font .tools/Inter-Medium.ttf --size 88 --bpp 4 --symbols '✓H' \
    --format lvgl --no-compress --lv-font-name inter_check -o components/pip_board/inter_check.c

# Reply-choice markers and confirmation type, at 2x the Figma dimensions.
.tools/python/bin/fonttools varLib.instancer assets/fonts/InterVariable.ttf \
    wght=700 opsz=16.5 --output .tools/Inter-Choice.ttf
.tools/font-converter/node_modules/.bin/lv_font_conv \
    --font .tools/Inter-Choice.ttf --size 33 --bpp 4 --symbols ABCH \
    --format lvgl --no-compress --lv-font-name inter_choice -o components/pip_board/inter_choice.c
.tools/python/bin/fonttools varLib.instancer assets/fonts/InterVariable.ttf \
    wght=600 opsz=14 --output .tools/Inter-Confirm.ttf
.tools/font-converter/node_modules/.bin/lv_font_conv \
    --font .tools/Inter-Confirm.ttf --size 22 --bpp 4 --symbols ConfirmH \
    --format lvgl --no-compress --lv-font-name inter_confirm -o components/pip_board/inter_confirm.c
.tools/font-converter/node_modules/.bin/lv_font_conv \
    --font .tools/Inter-Message.ttf --size 55 --bpp 4 --symbols '←→H' \
    --format lvgl --no-compress --lv-font-name inter_arrows -o components/pip_board/inter_arrows.c

.tools/python/bin/python - <<'PY'
from pathlib import Path
for name in ('inter_55', 'inter_check', 'inter_choice', 'inter_confirm', 'inter_arrows'):
    path = Path('components/pip_board') / (name + '.c')
    path.write_text(path.read_text().rstrip() + '\n')
PY
