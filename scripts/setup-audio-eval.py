#!/usr/bin/env python3
"""Install pinned, local-only speech enhancement evaluation binaries (Apple Silicon)."""
import hashlib
from pathlib import Path
import platform
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / '.tools/denoise-bin'
ASSETS = (
    ('bd.rnnn', 'https://raw.githubusercontent.com/GregorR/rnnoise-models/3eee541a283fd3b8f81b85b1748e3b9ccbefa04d/beguiling-drafter-2018-08-30/bd.rnnn',
        'ae3f7411e1e6a884f839a4a145c394408398f09854dbc1216ee02faafc98a17b'),
    ('deep-filter', 'https://github.com/Rikorose/DeepFilterNet/releases/download/v0.5.6/deep-filter-0.5.6-aarch64-apple-darwin',
        '4601e7f4e4c03e59a4c5b5000216ef3add3e808799cfccd95e14e83ea4611081'),
)


def main():
    if (platform.system(), platform.machine()) != ('Darwin', 'arm64'):
        raise SystemExit('The pinned DeepFilterNet binary currently targets Apple Silicon macOS')
    DEST.mkdir(parents=True, exist_ok=True)
    for name, url, checksum in ASSETS:
        path = DEST / name
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == checksum:
            continue
        with urllib.request.urlopen(url, timeout=60) as response:
            data = response.read(40 * 1024 * 1024)
        if hashlib.sha256(data).hexdigest() != checksum:
            raise SystemExit(f'Checksum mismatch for {name}')
        temporary = path.with_suffix('.download')
        temporary.write_bytes(data)
        temporary.chmod(0o755 if name == 'deep-filter' else 0o644)
        temporary.replace(path)
        print('Installed', name)
    print('Ready. FFmpeg must also be available on PATH.')


if __name__ == '__main__':
    main()
