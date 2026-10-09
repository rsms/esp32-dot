#!/usr/bin/env python3
"""Capture deterministic sleep poses and measure the live repeat-tumble renderer."""
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
spec = importlib.util.spec_from_file_location('render_bench', ROOT / 'scripts/render-bench.py')
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)
ui = bench.ui


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / '.tools/sleep')
    parser.add_argument('--seconds', type=float, default=12)
    parser.add_argument('--leave-running', action='store_true')
    args = parser.parse_args()
    assert args.seconds >= 3
    health = ui.api('/health')
    assert health['pending'] == 0, health
    ports = [p.device for p in list_ports.comports() if p.vid == 0x303a and p.pid == 0x1001]
    assert len(ports) == 1, ports
    args.output.mkdir(parents=True, exist_ok=True)
    with serial.Serial(ports[0], 115200, timeout=0.1, dsrdtr=True, rtscts=True) as stream:
        try:
            ui.send(stream, {'type': 'state', 'state': 'sleeping'})
            ui.wait_state(stream, 'sleeping')
            for repeat, poses in ((False, (0, 3000, 5500, 6500, 7500, 8800)),
                                  (True, (0, 750, 1500, 2250, 3000))):
                for ms in poses:
                    ui.send(stream, {'type': 'sleep_animation', 'repeat': repeat, 'seek_ms': ms})
                    time.sleep(0.15)
                    status = ui.inspect(stream)['sleep']
                    assert status['frozen'] and status['ms'] == ms, status
                    save_capture(stream, args.output / f'{"tumble" if repeat else "sleep"}-{ms}.png')
            ui.send(stream, {'type': 'sleep_animation', 'repeat': True})
            time.sleep(0.2)
            start = bench.stats(stream)
            began = time.monotonic()
            samples = []
            while time.monotonic() - began < args.seconds:
                time.sleep(0.2)
                samples.append(bench.stats(stream))
            elapsed = time.monotonic() - began
            fps = (samples[-1]['frames'] - start['frames']) / elapsed
            status = ui.inspect(stream)
            assert status['sleep']['phase'] == 'tumble'
            assert samples[-1]['frames'] > start['frames'] + args.seconds * 15
            report = {'seconds': elapsed, 'refreshes_per_second': fps,
                'median_refresh_us': statistics.median(s['refresh_us'] for s in samples),
                'median_pixels': statistics.median(s['pixels'] for s in samples),
                'renderer': status['sleep'], 'samples': samples}
            (args.output / 'timings.json').write_text(json.dumps(report, indent=4) + '\n')
            print(json.dumps({k: v for k, v in report.items() if k != 'samples'}, indent=4))
            # Host state changes must stop the animation, even without Wi-Fi.
            ui.send(stream, {'type': 'state', 'state': 'idle'})
            ui.wait_state(stream, 'idle')
            stopped = bench.stats(stream)
            time.sleep(.2)
            assert bench.stats(stream)['frames'] == stopped['frames']
        finally:
            ui.send(stream, {'type': 'state', 'state': 'sleeping'})
            ui.send(stream, {'type': 'sleep_animation', 'repeat': args.leave_running})


if __name__ == '__main__':
    main()
