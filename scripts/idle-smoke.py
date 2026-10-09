#!/usr/bin/env python3
"""Verify idle timeout and interruption behavior on the connected device."""
import importlib.util
from pathlib import Path
import time
import uuid

import serial
from serial.tools import list_ports
from screenshot import save_capture

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('ui_smoke', ROOT / 'scripts/ui-smoke.py')
ui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ui)


def expect(stream, state):
    value = ui.inspect(stream)
    assert value['state'] == state, value


def await_sleep(stream, started):
    deadline = started + 12
    while time.monotonic() < deadline:
        value = ui.inspect(stream)
        if value['state'] == 'sleeping':
            elapsed = time.monotonic() - started
            assert 9.5 <= elapsed <= 12, elapsed
            print(f'Sleep after {elapsed:.2f}s', flush=True)
            return
        assert value['state'] == 'idle', value
        time.sleep(0.1)
    raise AssertionError('Idle did not transition to sleeping')


def main():
    health = ui.api('/health')
    deadline = time.monotonic() + 30
    while not health['connected'] and time.monotonic() < deadline:
        time.sleep(0.5)
        health = ui.api('/health')
    assert health['connected'] and health['pending'] == 0, health
    ports = [p.device for p in list_ports.comports() if p.vid == 0x303a and p.pid == 0x1001]
    assert len(ports) == 1, ports
    output = ROOT / '.tools/idle'
    output.mkdir(parents=True, exist_ok=True)
    with serial.Serial(ports[0], 115200, timeout=0.2, dsrdtr=True, rtscts=True) as stream:
        started = time.monotonic()
        ui.api('/state', {'state': 'idle'})
        ui.wait_state(stream, 'idle')
        time.sleep(max(0, started + 9 - time.monotonic()))
        expect(stream, 'idle')
        await_sleep(stream, started)
        save_capture(stream, output / 'sleeping.png')

        # Holding sleeping starts listening; idle's old timer cannot fire.
        ui.pointer(stream, 1)
        ui.wait_state(stream, 'listening')
        time.sleep(10.5)
        expect(stream, 'listening')
        ui.pointer(stream, 0)
        ui.wait_state(stream, 'idle')

        # A drag that leaves the face idle still counts as activity.
        time.sleep(6)
        started = time.monotonic()
        ui.send(stream, {'type': 'drag', 'x0': 224, 'y0': 80, 'x1': 224, 'y1': 240})
        time.sleep(5)
        expect(stream, 'idle')
        await_sleep(stream, started)
        print('PASS: wake tap, listening excluded, drag resets inactivity', flush=True)

        ui.api('/state', {'state': 'idle'})
        ui.wait_state(stream, 'idle')
        time.sleep(2)
        rid = 'idle-smoke-' + uuid.uuid4().hex[:8]
        ui.api('/cards', {'id': rid, 'body': 'Incoming message prevents idle sleep.'})
        ui.wait_state(stream, 'attention')
        ui.tap(stream, 224)
        message = ui.wait_state(stream, 'message')
        time.sleep(10.5)
        expect(stream, 'message')
        for _ in range(message['pages']):
            ui.tap(stream, 400)
        started = time.monotonic()
        ui.tap(stream, 224)
        ui.wait_state(stream, 'idle')
        await_sleep(stream, started)
        assert ui.api('/health')['pending'] == 0
        save_capture(stream, output / 'sleeping-after-message.png')
    print('PASS: 10-second idle timeout, activity reset, incoming message cancellation, fresh timeout after dismissal', flush=True)


if __name__ == '__main__':
    main()
