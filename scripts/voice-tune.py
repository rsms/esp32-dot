#!/usr/bin/env python3
"""Start the device's local phrase/record/compare loop; Ctrl-C stops it."""
import argparse
import json
from pathlib import Path
import time
import urllib.error
import urllib.request


def api(data=None):
    request = urllib.request.Request('http://127.0.0.1:8788/voice-tune',
        data=None if data is None else json.dumps(data).encode(),
        headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise SystemExit(json.load(error).get('error', str(error)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('start', 'status', 'stop', 'apply', 'reset'), nargs='?', default='start')
    parser.add_argument('--phrases', type=Path, help='UTF-8 file with 8-100 distinct phrases (ASCII or ’), one per line')
    parser.add_argument('--detach', action='store_true', help='Keep tuning without monitoring this terminal')
    args = parser.parse_args()
    data = {'action': args.action}
    if args.phrases:
        data['phrases'] = [line.strip() for line in args.phrases.read_text().splitlines() if line.strip()]
    status = api(None if args.action == 'status' else data)
    if args.action != 'start' or args.detach:
        print(json.dumps(status, indent=2))
        return
    print('WAV files and labelled results stay under .tools/voice-tuning/' + status['session'])
    print('Read the phrase. Tap to record, wait for the red frame, speak, then tap to stop. Choose Retry, Exit, or Submit. Ctrl-C also stops.\n')
    previous_prompt, previous_record = None, None
    try:
        while status['active']:
            prompt, record = status['prompt'], status['last']
            if record and record['id'] != previous_record:
                previous_record = record['id']
                print('Original audio:', record['wav'], flush=True)
            if prompt and prompt['id'] != previous_prompt:
                previous_prompt = prompt['id']
                if record:
                    for mode, result in record['results'].items():
                        print(f"  {mode}: {result['wer']:.0%} word errors | {result['text']}")
                    print(status['recommendation']['reason'])
                print('\nSay:', prompt['reference'], flush=True)
            time.sleep(.5)
            status = api()
    except KeyboardInterrupt:
        pass
    finally:
        api({'action': 'stop'})
    print('Tuning stopped. Recordings kept locally. Use status to see the comparison.')


if __name__ == '__main__':
    main()
