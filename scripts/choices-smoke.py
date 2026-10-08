#!/usr/bin/env python3
"""Exercise the Figma reply flow on hardware with simulated drags and taps."""
import importlib.util
from pathlib import Path
import time
import uuid

import serial
from serial.tools import list_ports
from screenshot import save_capture

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('mcp_smoke', ROOT / 'scripts/mcp-device-smoke.py')
mcp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mcp)
ui = mcp.ui


def drag(stream, direction):
    x0, x1 = (380, 60) if direction > 0 else (60, 380)
    ui.send(stream, {'type': 'drag', 'x0': x0, 'y0': 180, 'x1': x1, 'y1': 180})
    time.sleep(0.15)


def main():
    health = mcp.call('get_device_status', {})
    assert health['connected'] and health['pending'] == 0, health
    ports = [p.device for p in list_ports.comports() if p.vid == 0x303a and p.pid == 0x1001]
    assert len(ports) == 1, ports
    prefix = 'choices-smoke-' + uuid.uuid4().hex[:8]
    output = ROOT / '.tools/choices'
    output.mkdir(parents=True, exist_ok=True)
    labels = ['SFO, United at 13:45', 'OAK, American Airlines, 14:15', 'SFO, Virgin, 12:30']
    created = []
    with serial.Serial(ports[0], 115200, timeout=0.2, dsrdtr=True, rtscts=True) as stream:
        try:
            for count in (3, 1, 2):
                rid = prefix + '-' + str(count)
                mcp.call('ask_question', {'request_id': rid,
                    'text': 'Question\ncan also wrap multiple lines and always ends with a few choices',
                    'options': [{'id': str(i), 'label': label} for i, label in enumerate(labels[:count])]})
                created.append(rid)
                initial = ui.wait_state(stream, 'message')
                assert initial['pages'] == 2, initial
                drag(stream, -1)
                assert ui.inspect(stream)['page'] == 0
                for page in range(initial['pages']):
                    if count == 3:
                        save_capture(stream, output / f'question-{page}.png')
                    drag(stream, 1)
                for option in range(count):
                    value = ui.wait_state(stream, 'choice')
                    assert value['page'] == initial['pages'] + option, value
                    if count == 3:
                        save_capture(stream, output / f'choice-{option}.png')
                    ui.tap(stream, 224, 152)
                    ui.wait_state(stream, 'confirm')
                    assert mcp.call('get_request', {'request_id': rid})['status'] == 'pending'
                    if count == 3:
                        save_capture(stream, output / f'confirm-{option}.png')
                    # A vertical drag or a tap outside the circle must never submit.
                    ui.send(stream, {'type': 'drag', 'x0': 224, 'y0': 80, 'x1': 224, 'y1': 220})
                    ui.tap(stream, 224, 300)
                    ui.wait_state(stream, 'confirm')
                    assert mcp.call('get_request', {'request_id': rid})['status'] == 'pending'
                    # Back leaves confirmation and returns to the preceding page.
                    drag(stream, -1)
                    value = ui.inspect(stream)
                    assert value['page'] == initial['pages'] + option - 1, value
                    drag(stream, 1)
                    ui.wait_state(stream, 'choice')
                    ui.tap(stream, 224, 152)
                    ui.wait_state(stream, 'confirm')
                    # Forward leaves confirmation, including the last-choice boundary.
                    ui.tap(stream, 400, 152)
                    value = ui.wait_state(stream, 'choice')
                    assert value['page'] == initial['pages'] + min(option + 1, count - 1), value
                ui.tap(stream, 224, 152)
                ui.wait_state(stream, 'confirm')
                ui.tap(stream, 224, 152)
                ui.wait_state(stream, 'idle')
                result = mcp.call('get_request', {'request_id': rid})
                assert result['status'] == 'answered' and result['selected_option']['id'] == str(count - 1), result
                events = [e for e in ui.api('/events') if e.get('card_id') == rid]
                assert len(events) == 1, events
            save_capture(stream, output / 'idle.png')
        finally:
            for rid in created:
                mcp.call('cancel_request', {'request_id': rid})
    print('PASS: 1/2/3 choices, pagination, swipe boundaries, confirmation cancellation, no premature replies, exactly one confirmed reply per card')


if __name__ == '__main__':
    main()
