#!/bin/sh
# Source from a script after setting PIP_ROOT to the repository path.
export CARGO_HOME="$PIP_ROOT/.tools/cargo"
export PATH="$PIP_ROOT/.tools/bin:$PATH"
export LIBCLANG_PATH="${LIBCLANG_PATH:-$HOME/.espup/esp-clang}"
export IDF_TOOLS_PATH="$PIP_ROOT/.embuild/espressif"

