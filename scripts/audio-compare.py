#!/usr/bin/env python3
"""Make offline listening candidates from retained WAVs; never changes the live profile."""
import argparse
from array import array
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import wave


def measure(path):
    with wave.open(str(path)) as source:
        if source.getnchannels() != 1 or source.getsampwidth() != 2 or source.getframerate() != 16000:
            raise ValueError('Expected mono 16 kHz PCM16 WAV')
        samples = array('h', source.readframes(source.getnframes()))
    if sys.byteorder != 'little':
        samples.byteswap()
    windows = {}
    for name, start, end in (('startup', 0, 320), ('after_100ms', 1600, len(samples)),
                             ('quiet_reference_250_500ms', 4000, 8000)):
        data = samples[start:end]
        rms = math.sqrt(sum(x*x for x in data) / max(1, len(data))) / 32768
        windows[name] = {'rms_dbfs': round(20 * math.log10(max(rms, 1e-10)), 2),
            'peak': max(map(abs, data), default=0), 'rail_samples': sum(abs(x) >= 32760 for x in data)}
    return {'seconds': len(samples)/16000, 'windows': windows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise SystemExit('FFmpeg is required')
    originals = sorted(args.directory.glob('*-raw.wav'))
    if not originals:
        raise SystemExit('No *-raw.wav recordings found')
    os.umask(0o077)
    args.output.mkdir(parents=True, exist_ok=True)
    # The 60 ms trim is for these known startup-transient recordings only, not
    # arbitrary audio. The extra variants are audition candidates, not ASR defaults.
    depop = 'atrim=start=0.06,asetpts=PTS-STARTPTS,afade=t=in:d=0.005'
    clean = depop + ',highpass=f=80:p=2,afftdn=nr=8:nf=-55:tn=0:gs=8'
    filters = {'depop': depop, 'denoise': clean,
        'presence': clean + ',equalizer=f=2500:t=q:w=0.7:g=3'}
    report = {'filters': filters, 'notes': [
        'Original WAVs are unchanged. Output candidates remove the first 60 ms.',
        'The 250-500 ms window is only a quiet reference if the speaker was silent then.',
        'No loudness normalization; denoise/EQ may harm recognition and require listening evaluation.',
        'These settings do not change the live preprocessing profile.'], 'recordings': []}
    for original in originals:
        row = {'original': str(original.resolve()), 'metrics': measure(original), 'variants': {}}
        for name, chain in filters.items():
            output = args.output / (original.stem.replace('-raw', '') + '-' + name + '.wav')
            if output.exists():
                raise SystemExit(f'Refusing to overwrite {output}')
            subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-i', str(original), '-af', chain,
                '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(output)], check=True)
            row['variants'][name] = {'path': str(output.resolve()), 'metrics': measure(output)}
        report['recordings'].append(row)
    (args.output / 'comparison.json').write_text(json.dumps(report, indent=2) + '\n')
    print(args.output.resolve())


if __name__ == '__main__':
    main()
