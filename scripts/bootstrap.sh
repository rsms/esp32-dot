#!/bin/sh
set -eu
PIP_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
. "$PIP_ROOT/scripts/env.sh"
cd "$PIP_ROOT"
mkdir -p .tools
cargo +stable install ldproxy --version 0.3.5 --root .tools --locked
uv venv --python 3.11 .tools/python
UV_CACHE_DIR="$PIP_ROOT/.tools/uv-cache" uv pip install --python .tools/python/bin/python \
    esptool==5.5.0 fonttools==4.66.1
npm install --prefix .tools/font-converter --save-exact lv_font_conv@1.5.3

