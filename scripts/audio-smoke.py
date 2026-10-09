#!/usr/bin/env python3
"""Record a short microphone sample and check transport/gate diagnostics."""
import argparse
import importlib.util
import json
from pathlib import Path
import time
import subprocess
import serial
from serial.tools import list_ports

spec = importlib.util.spec_from_file_location('ui', Path(__file__).with_name('ui-smoke.py'))
ui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ui)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=float, default=3)
    parser.add_argument('--play-file', type=Path, help='Optional speech fixture to play through the Mac output during capture')
    args = parser.parse_args()
    assert 0.2 <= args.seconds <= 20
    deadline = time.monotonic() + 30
    health = ui.api('/health')
    while not health['connected'] and time.monotonic() < deadline:
        time.sleep(.5)
        health = ui.api('/health')
    assert health['connected'] and health['pending'] == 0
    assert ui.api('/audio')['ready'], 'Audio service still warming up'
    ports = [p.device for p in list_ports.comports() if p.vid == 0x303a and p.pid == 0x1001]
    assert len(ports) == 1
    with serial.Serial(ports[0], 115200, timeout=.1, dsrdtr=True, rtscts=True) as stream:
        try:
            ui.send(stream, {'type': 'state', 'state': 'sleeping'})
            ui.wait_state(stream, 'sleeping')
            ui.pointer(stream, 1)
            ui.wait_state(stream, 'listening')
            if args.play_file:
                time.sleep(.2)
                subprocess.run(['afplay', str(args.play_file)], check=True)
                time.sleep(.3)
            else:
                time.sleep(args.seconds)
            capture = ui.inspect(stream)
            print('device:', json.dumps(capture), flush=True)
            assert capture['state'] == 'listening' and capture['audio']['samples'] > args.seconds * 8000
            ui.pointer(stream, 0)
            deadline = time.monotonic() + 50
            while time.monotonic() < deadline:
                state = ui.inspect(stream)
                if state['state'] == 'idle':
                    break
                time.sleep(.1)
            assert state['state'] == 'idle', state
            result = ui.api('/audio')
            print('host:', json.dumps(result), flush=True)
            assert result['last']['status'] in ('discarded', 'empty', 'transcribed'), result
            Path('.tools/audio-smoke.json').write_text(json.dumps({'device': capture, 'host': result}, indent=4) + '\n')
        finally:
            ui.pointer(stream, 2)
            ui.send(stream, {'type': 'state', 'state': 'sleeping'})


if __name__ == '__main__':
    main()
