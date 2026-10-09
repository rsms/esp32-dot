#!/usr/bin/env python3
"""Build a pinned whisper.cpp checkout and download a verified local ASR model."""
import argparse
import hashlib
from pathlib import Path
import platform
import shutil
import subprocess
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
REVISION = 'd1be6fde11ac6e0407606b4e42fe72d34add8037'
MODELS = {
    'base.en': 'a03779c86df3323075f5e796cb2ce5029f00ec8869eee3fdfb897afe36c6d002',
    'small.en': 'c6138d6d58ecc8322097e0f987c32f1be8bb0a18532a3f88f734d1bbf9c41e5d',
    'large-v3-turbo-q8_0': '317eb69c11673c9de1e1f0d459b253999804ec71ac4c23c17ecf5fbe24e259a1',
}


def sha(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', choices=MODELS, default='large-v3-turbo-q8_0')
    args = parser.parse_args()
    source = ROOT / '.tools/whisper.cpp'
    build = ROOT / '.tools/whisper-build'
    source.parent.mkdir(exist_ok=True)
    if not source.exists():
        subprocess.run(['git', 'clone', 'https://github.com/ggml-org/whisper.cpp', str(source)], check=True)
        subprocess.run(['git', '-C', str(source), 'checkout', '--detach', REVISION], check=True)
    revision = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    dirty = subprocess.check_output(['git', '-C', str(source), 'status', '--porcelain'], text=True).strip()
    if revision != REVISION or dirty:
        raise SystemExit('Existing whisper.cpp checkout differs from pinned revision; refusing to reset it')
    command = ['cmake', '-S', str(source), '-B', str(build), '-DCMAKE_BUILD_TYPE=Release',
        '-DWHISPER_BUILD_TESTS=OFF']
    if platform.system() == 'Darwin':
        gmake = shutil.which('gmake')
        if not gmake:
            raise SystemExit('Install GNU make (gmake) first')
        command.append('-DCMAKE_MAKE_PROGRAM=' + gmake)
    subprocess.run(command, check=True)
    subprocess.run(['cmake', '--build', str(build), '--parallel', '6'], check=True)
    model = ROOT / '.tools/whisper-models' / f'ggml-{args.model}.bin'
    model.parent.mkdir(exist_ok=True)
    if not model.exists() or sha(model) != MODELS[args.model]:
        temporary = model.with_suffix('.download')
        url = 'https://huggingface.co/ggerganov/whisper.cpp/resolve/main/' + model.name
        with urllib.request.urlopen(url, timeout=60) as response, temporary.open('wb') as output:
            shutil.copyfileobj(response, output)
        if sha(temporary) != MODELS[args.model]:
            temporary.unlink()
            raise SystemExit('Model checksum mismatch')
        temporary.replace(model)
    print(model)


if __name__ == '__main__':
    main()
