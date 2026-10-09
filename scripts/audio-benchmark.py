#!/usr/bin/env python3
"""Compare a resident Whisper model with recorded baseline results. All inference is local."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
from pip_enhance import read_wav
from pip_tuning import word_error
from pip_whisper import WhisperServer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('session', type=Path, help='Tuning session.json')
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--enhanced', type=Path, help='Optional audio-enhance manifest.json')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    session = json.loads(args.session.read_text())
    records = [r for r in session['records'] if r.get('decision') == 'accepted']
    if not records:
        parser.error('No accepted recordings')
    enhanced = {}
    if args.enhanced:
        enhanced = {r['original']: r['variants'] for r in json.loads(args.enhanced.read_text())['records']}
    os.umask(0o077)
    with args.model.open('rb') as model_file:
        model_hash = hashlib.file_digest(model_file, 'sha256').hexdigest()
    # Exclusive creation protects previous comparisons, including interrupted runs.
    with args.output.open('x') as output:
        report = {'model': str(args.model.resolve()),
            'model_sha256': model_hash,
            'scoring': 'Strict word edit distance; number spellings can count as errors.',
            'latency': 'Warm resident model; excludes loading, capture, and network transport to host.',
            'references_sent_to_model': False, 'records': []}
        with WhisperServer(args.model) as worker:
            worker.transcribe(records[0]['wav'])  # Warm Metal kernels; server has no context across requests.
            for record in records:
                original = str(Path(record['wav']).resolve())
                paths = {'raw': original}
                paths.update({name: v['path'] for name, v in enhanced.get(original, {}).items()})
                row = {'id': record['id'], 'reference': record['reference'], 'split': record['split'],
                    'baseline': record['results']['raw'],
                    'baseline_model': record.get('recognizer', 'phonon-2'), 'whisper': {}}
                for name, path in paths.items():
                    read_wav(path)  # Require the same mono 16 kHz PCM16 input format.
                    result = worker.transcribe(path)
                    result.update(word_error(row['reference'], result['text']))
                    result['path'] = path
                    row['whisper'][name] = result
                report['records'].append(row)
                output.seek(0)
                json.dump(report, output, indent=2)
                output.write('\n')
                output.truncate()
                output.flush()
                print(record['id'], row['whisper']['raw']['text'], flush=True)
    for variant in report['records'][0]['whisper']:
        results = [r['whisper'][variant] for r in report['records']]
        print(variant, 'strict errors:', sum(r['errors'] for r in results),
            'median seconds:', round(statistics.median(r['seconds'] for r in results), 3))


if __name__ == '__main__':
    main()
