#!/bin/sh
# Install the live local recognizer. Large downloads and build products are ignored.
set -eu
cd "$(dirname "$0")/.."
.tools/python/bin/python scripts/setup-whisper.py --model large-v3-turbo-q8_0
printf '%s\n' 'Audio installed. Restart the bridge to load Whisper turbo.'
