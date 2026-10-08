#!/bin/sh
set -eu
PIP_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
. "$PIP_ROOT/scripts/env.sh"
cd "$PIP_ROOT"
# esp-idf-sys watches its binding header, but not all extra-component C files.
# Make CMake/Ninja check those files on every build, including font edits.
touch components/pip_board/include/pip_board.h
cargo build --release "$@"
