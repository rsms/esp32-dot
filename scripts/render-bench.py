#!/usr/bin/env python3
"""Measure LVGL refresh work on hardware, excluding command/USB latency."""
import argparse
import importlib.util
import json
from pathlib import Path
import statistics
import time

import serial
from serial.tools import list_ports
from screenshot import save_capture

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('ui_smoke', ROOT / 'scripts/ui-smoke.py')
ui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ui)


def stats(stream):
    stream.reset_input_buffer()
    deadline = time.monotonic() + 5
    retry = 0
    while time.monotonic() < deadline:
        if time.monotonic() >= retry:
            stream.write(b'render-stats\n')
            stream.flush()
            retry = time.monotonic() + 0.5
        line = stream.readline()
        if b'[Warn]' in line or b'[Error]' in line:
            raise AssertionError(line.decode(errors='replace').strip())
        if line.startswith(b'PIPRENDER '):
            return json.loads(line[10:])
    raise TimeoutError('No render statistics')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    health = ui.api('/health')
    deadline = time.monotonic() + 30
    while not health['connected'] and time.monotonic() < deadline:
        time.sleep(0.5)
        health = ui.api('/health')
    assert health['connected'] and health['pending'] == 0, health
    ports = [p.device for p in list_ports.comports() if p.vid == 0x303a and p.pid == 0x1001]
    assert len(ports) == 1, ports
    args.output.mkdir(parents=True, exist_ok=True)
    samples = []
    with serial.Serial(ports[0], 115200, timeout=0.1, dsrdtr=True, rtscts=True) as stream:
        previous = stats(stream)
        for run in range(3):
            for state in ('idle', 'listening', 'thinking', 'attention', 'sleeping'):
                ui.send(stream, {'type': 'state', 'state': state})
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    sample = stats(stream)
                    if sample['frames'] > previous['frames']:
                        break
                assert sample['frames'] > previous['frames'], sample
                previous = sample
                samples.append(dict(sample, state=state))
                if run == 2:
                    save_capture(stream, args.output / (state + '.png'))
    (args.output / 'timings.json').write_text(json.dumps(samples, indent=4) + '\n')
    for state in dict.fromkeys(s['state'] for s in samples):
        group = [s for s in samples if s['state'] == state]
        print(state, {key: round(statistics.median(s[key] for s in group), 1)
            for key in ('refresh_us', 'wait_us', 'flush_us', 'strips', 'pixels')}, flush=True)


if __name__ == '__main__':
    main()
