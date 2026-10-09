#!/usr/bin/env python3
"""Check attention gating over USB without publishing test replies or transcripts."""
import importlib.util
from pathlib import Path
import time

import serial
from serial.tools import list_ports

spec = importlib.util.spec_from_file_location('ui', Path(__file__).with_name('ui-smoke.py'))
ui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ui)


def main():
    health = ui.api('/health')
    assert health['connected'] and health['pending'] == 0, health
    audio = ui.api('/audio')
    assert not any(audio.get(key) for key in ('recording', 'busy', 'tuning')), audio
    ports = [p.device for p in list_ports.comports() if p.vid == 0x303a and p.pid == 0x1001]
    assert len(ports) == 1, ports
    with serial.Serial(ports[0], 115200, timeout=0.2, dsrdtr=True, rtscts=True) as stream:
        def card(suffix):
            assert ui.api('/health')['pending'] == 0, 'A real message arrived; stop the test'
            ui.send(stream, {'type': 'card', 'id': 'attention-smoke-' + suffix,
                'title': '', 'body': 'Attention must wait for a fresh tap.',
                'kind': 'notice', 'options': [{'id': 'dismiss', 'label': 'Dismiss'}]})
            ui.wait_state(stream, 'attention')

        try:
            ui.send(stream, {'type': 'state', 'state': 'thinking'})
            ui.wait_state(stream, 'thinking')
            ui.pointer(stream, 1)
            card('old-press')
            ui.pointer(stream, 0)
            assert ui.inspect(stream)['state'] == 'attention'
            time.sleep(11)
            assert ui.inspect(stream)['state'] == 'attention'
            ui.pointer(stream, 1)
            card('replacement')
            ui.pointer(stream, 0)
            assert ui.inspect(stream)['state'] == 'attention'
            ui.tap(stream, 400)
            value = ui.wait_state(stream, 'message')
            assert value['page'] == 0, value
            ui.tap(stream, 400)
            assert ui.inspect(stream)['page'] == 1
        finally:
            ui.pointer(stream, 2)
            if ui.api('/health')['pending'] == 0:
                ui.send(stream, {'type': 'sync', 'card_id': None})
                ui.wait_state(stream, 'idle')
    print('PASS: attention persists, old/replaced touches ignored, fresh tap opens page zero, navigation works')


if __name__ == '__main__':
    main()
