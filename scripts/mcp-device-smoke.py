#!/usr/bin/env python3
"""Test MCP -> TCP -> real screen -> simulated touch -> MCP reply; empty queue only."""
import importlib.util
import json
from pathlib import Path
import time
import urllib.request
import uuid

import serial
from serial.tools import list_ports
from pip_mcp import load_mcp_token
from screenshot import save_capture

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('ui_smoke', ROOT / 'scripts/ui-smoke.py')
ui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ui)


def call(name, args):
    payload = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': name, 'arguments': args}}
    request = urllib.request.Request('http://127.0.0.1:8789/mcp', json.dumps(payload).encode(),
        {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + load_mcp_token()})
    with urllib.request.urlopen(request, timeout=5) as response:
        value = json.load(response)
    assert 'error' not in value, value
    assert not value['result']['isError'], value
    return value['result']['structuredContent']


def main():
    health = call('get_device_status', {})
    deadline = time.monotonic() + 40
    while not health['connected'] and time.monotonic() < deadline:
        time.sleep(0.5)
        health = call('get_device_status', {})
    assert health['connected'] and health['pending'] == 0, health
    ports = [p.device for p in list_ports.comports() if p.vid == 0x303a and p.pid == 0x1001]
    assert len(ports) == 1, ports
    prefix = 'mcp-smoke-' + uuid.uuid4().hex[:8]
    output = ROOT / '.tools/mcp-smoke'
    output.mkdir(parents=True, exist_ok=True)
    with serial.Serial(ports[0], 115200, timeout=0.2, dsrdtr=True, rtscts=True) as stream:
        try:
            call('ask_question', {'request_id': prefix + '-cancel', 'text': 'Temporary MCP test',
                'options': [{'id': 'ok', 'label': 'OK'}]})
            ui.wait_state(stream, 'message')
            call('cancel_request', {'request_id': prefix + '-cancel'})
            ui.wait_state(stream, 'idle')
            call('send_message', {'request_id': prefix, 'text': 'Hello from pip MCP.'})
            state = ui.wait_state(stream, 'message')
            save_capture(stream, output / 'message.png')
            for _ in range(state['pages']):
                ui.tap(stream, 400)
            save_capture(stream, output / 'dismiss.png')
            ui.tap(stream, 224)
            ui.wait_state(stream, 'idle')
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                result = call('get_request', {'request_id': prefix})
                if result['status'] == 'answered':
                    break
                time.sleep(0.1)
            assert result['selected_option']['id'] == 'dismiss', result
            save_capture(stream, output / 'idle.png')
            print('PASS: MCP tools -> TCP -> real display, cancellation, simulated touch dismissal -> durable MCP reply')
        finally:
            for rid in (prefix + '-cancel', prefix):
                try:
                    call('cancel_request', {'request_id': rid})
                except (AssertionError, OSError):
                    pass


if __name__ == '__main__':
    main()
