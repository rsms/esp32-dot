"""Offline neural enhancement. Originals and the live ASR profile are never changed."""
from pathlib import Path
from array import array
import math
import shutil
import subprocess
import tempfile
import sys
import wave

ROOT = Path(__file__).resolve().parent.parent
RATE = 16000
ENGINES = ('rnnoise', 'deepfilter')


def run(command, data=None, cwd=None):
    result = subprocess.run(command, input=data, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, timeout=30, check=False, cwd=cwd)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors='replace')[-1200:])
    return result.stdout


def enhance(pcm, engine):
    if engine not in ENGINES or len(pcm) % 2 or len(pcm) > RATE * 2 * 30:
        raise ValueError('Expected a supported engine and at most 30 seconds of PCM16')
    if not pcm:
        return b''
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise RuntimeError('FFmpeg is required')
    prefix = [ffmpeg, '-nostdin', '-v', 'error', '-f', 's16le', '-ar', str(RATE),
        '-ac', '1', '-i', 'pipe:0']
    if engine == 'rnnoise':
        model = ROOT / '.tools/denoise-bin/bd.rnnn'
        if not model.exists():
            raise RuntimeError('Run scripts/setup-audio-eval.py first')
        samples = array('h', pcm)
        if sys.byteorder != 'little':
            samples.byteswap()
        peak = max(map(abs, samples)) / 32768
        gain = min(12.0, 20 * math.log10(0.95 / max(peak, 1e-9)))
        # Isolate the fixed model filename from FFmpeg filter-path escaping.
        # RNNoise introduces one 10 ms frame of delay; pad and remove that delay
        # so comparisons retain every original sample, including the final word.
        with tempfile.TemporaryDirectory(prefix='dot-denoise-') as directory:
            local = Path(directory) / 'model.rnnn'
            shutil.copyfile(model, local)
            chain = (f'apad=pad_dur=0.1,volume={gain}dB,aresample=48000,'
                f'arnndn=m=model.rnnn,aresample=16000,volume={-gain}dB,'
                f'atrim=start_sample=160:end_sample={160 + len(pcm)//2}')
            result = run(prefix + ['-af', chain, '-f', 's16le', 'pipe:1'], pcm, cwd=directory)
    else:
        binary = ROOT / '.tools/denoise-bin/deep-filter'
        if not binary.exists():
            raise RuntimeError('Run scripts/setup-audio-eval.py first')
        with tempfile.TemporaryDirectory(prefix='dot-denoise-') as directory:
            source = Path(directory) / 'sample.wav'
            # DeepFilterNet's delay compensation shortens the output by 30 ms.
            # Flush its final frames with silence, then retain the original duration.
            run(prefix + ['-af', 'apad=pad_dur=0.1', '-ar', '48000', str(source)], pcm)
            output = Path(directory) / 'output'
            run([str(binary), '-D', '-o', str(output), str(source)])
            result = run([ffmpeg, '-nostdin', '-v', 'error', '-i', str(output / source.name),
                '-af', f'aresample=16000,atrim=end_sample={len(pcm)//2}',
                '-ac', '1', '-f', 's16le', 'pipe:1'])
    if len(result) != len(pcm):
        raise RuntimeError(f'Enhancer changed sample count: {len(pcm)//2} -> {len(result)//2}')
    return result


def read_wav(path):
    with wave.open(str(path)) as source:
        if (source.getnchannels(), source.getsampwidth(), source.getframerate()) != (1, 2, RATE):
            raise ValueError('Expected mono 16 kHz PCM16 WAV')
        return source.readframes(source.getnframes())
