#!/bin/sh
# Install the pinned native worker and local model. Everything large is ignored.
set -eu
cd "$(dirname "$0")/.."
uv pip install --python .tools/python/bin/python huggingface_hub
.tools/python/bin/hf download FermionResearch/Phonon-2-CoreML --revision 1143812fd4236522232a8c33e3115f0577e3cd7d --local-dir .tools/Phonon-2-CoreML
swift build --package-path host/phonon --scratch-path .tools/phonon-build -c release
printf '%s\n' 'Audio installed. Restart the bridge to load Phonon-2.'
