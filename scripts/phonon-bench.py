#!/usr/bin/env python3
"""Measure the resident Phonon worker using a supplied speech fixture."""
import json
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parent.parent
worker = subprocess.Popen([str(ROOT / '.tools/phonon-build/release/DotTranscriber'),
    str(ROOT / '.tools/Phonon-2-CoreML')], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
try:
    print('worker:', worker.stdout.readline().strip(), flush=True)
    results = []
    for run in range(3):
        began = time.monotonic()
        worker.stdin.write(json.dumps({'id': str(run), 'path': str(ROOT /
            '.tools/Phonon-2-CoreML/ci/clips/1089-134686-0000.flac')}) + '\n')
        worker.stdin.flush()
        result = json.loads(worker.stdout.readline())
        result['roundtrip_seconds'] = time.monotonic() - began
        assert result.get('text') and 'error' not in result, result
        results.append(result)
        print({key: value for key, value in result.items() if key != 'words'}, flush=True)
    assert len({r['text'] for r in results}) == 1
    (ROOT / '.tools/phonon-benchmark.json').write_text(json.dumps(results, indent=4) + '\n')
finally:
    worker.stdin.close()
    worker.wait(timeout=30)
