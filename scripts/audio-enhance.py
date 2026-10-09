#!/usr/bin/env python3
"""Export local neural-denoised WAVs for listening; does not change live recognition."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time
from pip_enhance import ENGINES, enhance, read_wav
from pip_tuning import save_wav


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path, help='Directory containing *-raw.wav')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--engines', nargs='+', choices=ENGINES, default=list(ENGINES))
    args = parser.parse_args()
    originals = sorted(args.directory.glob('*-raw.wav'))
    if not originals:
        parser.error('No original WAV files found')
    os.umask(0o077)
    args.output.mkdir(parents=True, exist_ok=False)
    report = {'originals_unchanged': True, 'live_profile_changed': False, 'records': []}
    for source in originals:
        original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        pcm = read_wav(source)
        row = {'original': str(source.resolve()), 'sha256': original_hash, 'variants': {}}
        for engine in args.engines:
            began = time.perf_counter()
            cleaned = enhance(pcm, engine)
            elapsed = time.perf_counter() - began
            dest = args.output / (source.stem.removesuffix('-raw') + '-' + engine + '.wav')
            save_wav(dest, cleaned)
            row['variants'][engine] = {'path': str(dest.resolve()), 'seconds': elapsed}
        if hashlib.sha256(source.read_bytes()).hexdigest() != original_hash:
            raise RuntimeError(f'Original changed during export: {source}')
        report['records'].append(row)
        (args.output / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
        print(source.name, flush=True)
    print(args.output.resolve())


if __name__ == '__main__':
    main()
