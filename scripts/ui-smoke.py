#!/usr/bin/env python3
"""Exercise the v1 UI on real hardware; requires an empty development queue."""
import json
from pathlib import Path
import time
import urllib.request
import uuid

import serial
from serial.tools import list_ports
from screenshot import save_capture

API = 'http://127.0.0.1:8788'
OUT = Path('.tools/v1')


def api(path, data=None):
    request = urllib.request.Request(API + path,
        None if data is None else json.dumps(data).encode(), {'Content-Type': 'application/json'})
    return json.load(urllib.request.urlopen(request, timeout=5))


def send(stream, data):
    stream.write(json.dumps(data).encode() + b'\n')
    stream.flush()


def inspect(stream):
    stream.reset_input_buffer()
    send(stream, {'type': 'inspect'})
    deadline = time.monotonic() + 5
    retry = time.monotonic() + 0.75
    while time.monotonic() < deadline:
        if time.monotonic() >= retry:
            send(stream, {'type': 'inspect'})
            retry = time.monotonic() + 0.75
        line = stream.readline()
        if line.startswith(b'PIPEVENT '):
            value = json.loads(line[9:])
            if value.get('type') == 'ui':
                return value
    raise TimeoutError('No UI inspection response')


def wait_state(stream, name):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        value = inspect(stream)
        if value['state'] == name:
            return value
        time.sleep(0.1)
    raise AssertionError((name, value))


def tap(stream, x, y=180):
    send(stream, {'type': 'tap', 'x': x, 'y': y})
    time.sleep(0.1)


def main():
    health = api('/health')
    assert health['connected'] and health['transport'] == 'tcp', health
    assert health['pending'] == 0, 'Dismiss the existing cards first; this test never clears user cards'
    devices = [p.device for p in list_ports.comports() if p.vid == 0x303a and p.pid == 0x1001]
    assert len(devices) == 1, devices
    OUT.mkdir(parents=True, exist_ok=True)
    with serial.Serial(devices[0], 115200, timeout=0.2, dsrdtr=True, rtscts=True) as stream:
        for state in ('sleeping', 'idle', 'listening', 'thinking', 'attention'):
            api('/state', {'state': state})
            wait_state(stream, state)
            time.sleep(0.15)
            save_capture(stream, OUT / (state + '.png'))
        api('/state', {'state': 'sleeping'})
        wait_state(stream, 'sleeping')
        tap(stream, 224)
        wait_state(stream, 'listening')
        tap(stream, 224)
        wait_state(stream, 'idle')
        run = 'ui-smoke-' + uuid.uuid4().hex[:8]
        api('/cards', {'id': run, 'title': 'Message', 'body':
            'here which can wrap multiple lines, and when it does wrap multiple lines, it paginates until the last page.'})
        initial = wait_state(stream, 'message')
        assert initial['page'] == 0 and initial['pages'] >= 2, initial
        for x in (20, 224):
            tap(stream, x)
            assert inspect(stream)['page'] == 0
        for page in range(initial['pages']):
            assert inspect(stream)['page'] == page
            save_capture(stream, OUT / f'message-{page + 1}.png')
            tap(stream, 400)
        assert inspect(stream)['page'] == initial['pages']
        save_capture(stream, OUT / 'dismissal.png')
        tap(stream, 400)
        assert inspect(stream)['page'] == initial['pages']
        tap(stream, 20)
        assert inspect(stream)['page'] == initial['pages'] - 1
        tap(stream, 400)
        # Queue advancement and error styling share the acknowledged reply path.
        api('/cards', {'id': run + '-error', 'kind': 'error', 'body':
            'Error: description starts here and may span multiple pages and ends with dismissal'})
        tap(stream, 224)
        error = wait_state(stream, 'message')
        save_capture(stream, OUT / 'error.png')
        for _ in range(error['pages']):
            tap(stream, 400)
        save_capture(stream, OUT / 'error-dismissal.png')
        tap(stream, 224)
        wait_state(stream, 'idle')
        events = api('/events')
        choices = [e for e in events if e.get('card_id', '').startswith(run)]
        assert len(choices) == 2 and all(e['option_id'] == 'dismiss' for e in choices), choices
        assert api('/health')['pending'] == 0
        save_capture(stream, OUT / 'idle-final.png')
    print('PASS: face states, listen taps, page boundaries, back navigation, queue advancement, error dismissal, one reply per card')


if __name__ == '__main__':
    main()
