#!/usr/bin/env python3
"""Hardware checks for dimming and voice-tuning lifecycle (records 2.5 seconds)."""
import importlib.util
import json
from pathlib import Path
import time
import serial
from serial.tools import list_ports
from screenshot import save_capture

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('ui', ROOT / 'scripts/ui-smoke.py')
ui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ui)


def pointer(stream, phase, x=224, y=180):
    ui.send(stream, {'type': 'pointer', 'phase': phase, 'x': x, 'y': y})


def wait_microphone(stream):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        value = ui.inspect(stream)
        if value['audio']['started']:
            return
        assert value['state'] == 'listening', value
        time.sleep(.05)
    raise AssertionError('Microphone did not start')


def main():
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        health, audio = ui.api('/health'), ui.api('/audio')
        if health['connected'] and audio['ready']:
            break
        time.sleep(.5)
    assert health['connected'] and health['pending'] == 0 and audio['ready'], (health, audio)
    assert not ui.api('/voice-tune')['active'], 'Stop the current tuning session first'
    ports = [p.device for p in list_ports.comports() if p.vid == 0x303a and p.pid == 0x1001]
    assert len(ports) == 1
    output = ROOT / '.tools/tuning-smoke'
    output.mkdir(exist_ok=True)
    with serial.Serial(ports[0], 115200, timeout=.2, dsrdtr=True, rtscts=True) as stream:
        pointer(stream, 2)
        ui.api('/state', {'state': 'sleeping'})
        assert ui.wait_state(stream, 'sleeping')['brightness'] == 20
        ui.api('/state', {'state': 'idle'})
        assert ui.wait_state(stream, 'idle')['brightness'] == 80
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            current = ui.inspect(stream)
            if current['state'] == 'sleeping':
                break
            time.sleep(.3)
        assert current['state'] == 'sleeping' and current['brightness'] == 20, current
        # Main experience uses the same press/release path. Keep captures below
        # two seconds so this check cannot publish a normal voice transcript.
        for origin in ('sleeping', 'idle'):
            ui.api('/state', {'state': origin})
            ui.wait_state(stream, origin)
            pointer(stream, 1)
            assert ui.wait_state(stream, 'listening')['brightness'] == 80
            wait_microphone(stream)
            time.sleep(.3)
            pointer(stream, 0)
            ui.wait_state(stream, 'idle')
        # A quick main-screen tap cancels before microphone startup.
        ui.tap(stream, 224)
        ui.wait_state(stream, 'idle')
        # A state change during a hold consumes its later release, rather than
        # treating it as a fresh tap on the replacement screen.
        pointer(stream, 1)
        ui.wait_state(stream, 'listening')
        ui.api('/state', {'state': 'idle'})
        ui.wait_state(stream, 'idle')
        pointer(stream, 0)
        assert ui.inspect(stream)['state'] == 'idle'
        from pip_tuning import PHRASES
        sample = "Message here can span quite a lot of lines with this smaller font size. I’m going to add another line here"
        first = ui.api('/voice-tune', {'action':'start', 'phrases':[sample, *PHRASES[1:]]})
        try:
            assert ui.wait_state(stream, 'tuning')['brightness'] == 80
            save_capture(stream, output / 'ready.png')
            ui.tap(stream, 224)
            assert ui.wait_state(stream, 'listening')['tuning']
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not ui.inspect(stream)['audio']['started']:
                time.sleep(.1)
            assert ui.inspect(stream)['audio']['started']
            save_capture(stream, output / 'recording.png')
            time.sleep(2.1)
            ui.tap(stream, 224)
            ui.wait_state(stream, 'tune_review')
            save_capture(stream, output / 'review.png')
            status = ui.api('/voice-tune')
            assert status['recordings'] == 1 and status['last']['decision'] == 'pending', status
            assert status['prompt']['id'] == first['prompt']['id']
            assert Path(status['last']['wav']).exists()
            # Gaps do not select an action.
            ui.tap(stream, 224, 184)
            ui.tap(stream, 440, 356)
            assert ui.inspect(stream)['state'] == 'tune_review'
            ui.tap(stream, 118, 80)  # Retry returns to the same phrase, awaiting a tap.
            ui.wait_state(stream, 'tuning')
            status = ui.api('/voice-tune')
            assert status['prompt']['reference'] == sample
            assert status['last']['decision'] == 'rejected'
            ui.tap(stream, 224)
            ui.wait_state(stream, 'listening')
            wait_microphone(stream)
            # Losing a subsequent touch must not stop a tap-controlled take.
            pointer(stream, 1)
            pointer(stream, 2)
            assert ui.inspect(stream)['state'] == 'listening'
            time.sleep(2.5)
            ui.tap(stream, 224)
            ui.wait_state(stream, 'tune_review')
            ui.tap(stream, 330, 184)  # Submit accepts exactly this take.
            ui.wait_state(stream, 'tuning')
            status = ui.api('/voice-tune')
            assert status['prompt']['reference'] == PHRASES[1]
            assert status['last']['decision'] == 'accepted'
            assert status['recordings'] == 2
            save_capture(stream, output / 'next-phrase.png')
            ui.tap(stream, 224)
            ui.wait_state(stream, 'listening')
            wait_microphone(stream)
            time.sleep(.3)
            ui.tap(stream, 224)
            ui.wait_state(stream, 'tune_review')
            ui.tap(stream, 118, 270)  # Exit abandons the pending take.
            ui.wait_state(stream, 'idle')
            time.sleep(.3)
            status = ui.api('/voice-tune')
            assert not status['active'] and status['last']['decision'] == 'abandoned', status
            (output / 'result.json').write_text(json.dumps(status, indent=2))
        finally:
            pointer(stream, 2)
            ui.api('/voice-tune', {'action':'stop'})
        assert ui.wait_state(stream, 'idle')['brightness'] == 80
        ui.api('/state', {'state':'sleeping'})
        assert ui.wait_state(stream, 'sleeping')['brightness'] == 20
    print('PASS: sleep 20%, active 80%, idle timeout, prompt, main hold/release, tuning tap/start/stop, short press, retained WAV, review gaps, Retry, Submit, Exit')


if __name__ == '__main__':
    main()
