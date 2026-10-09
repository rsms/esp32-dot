import asyncio
from array import array
import json
import math
from pathlib import Path
import struct
import tempfile
import unittest
from bridge import Bridge
from pip_audio import AudioService, Recording, BLOCK, RATE


def tone(amplitude=4000):
    return struct.pack('<320h', *(int(amplitude * math.sin(i * 2 * math.pi / 40)) for i in range(BLOCK)))


class GateTests(unittest.TestCase):
    def test_short_quiet_dc_and_tap_are_discarded(self):
        for blocks, packet in ((49, tone()), (150, bytes(640)),
                               (150, struct.pack('<320h', *([8000] * 320)))):
            recording = Recording()
            for _ in range(blocks):
                recording.append(packet)
            self.assertFalse(recording.accepted())
        recording = Recording()
        recording.append(tone(16000))
        for _ in range(149):
            recording.append(bytes(640))
        self.assertFalse(recording.accepted())

    def test_sustained_signal_and_limits(self):
        recording = Recording()
        for _ in range(49):
            recording.append(tone())
        self.assertFalse(recording.accepted())  # 0.98 seconds
        recording.append(tone())
        self.assertTrue(recording.accepted())  # Exactly 1.00 second
        recording.append(tone())
        self.assertTrue(recording.accepted())
        with self.assertRaises(ValueError):
            recording.append(b'\0\0')
        recording.pcm = bytearray(RATE * 30 * 2)
        with self.assertRaises(ValueError):
            recording.append(tone())


class FakeWorker:
    ready = True
    error = None
    calls = 0

    async def transcribe(self, request_id, pcm):
        self.calls += 1
        self.pcm = bytes(pcm)
        return {'text': 'A local test.', 'transcribe_seconds': 0.01}


class StreamTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.bridge = Bridge('test-token', Path(self.tmp.name) / 'state.json')
        self.worker = FakeWorker()
        self.service = AudioService(self.bridge, self.worker)
        self.server = await asyncio.start_server(self.service.client, '127.0.0.1', 0, limit=4096)
        self.port = self.server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()
        self.tmp.cleanup()

    async def recording(self, blocks=101, end=0, token='test-token', malformed=False, quiet=False):
        reader, writer = await asyncio.open_connection('127.0.0.1', self.port)
        writer.write(json.dumps({'type': 'audio', 'version': 1, 'token': token,
            'id': 'test-recording', 'sample_rate': 16000, 'channels': 1, 'format': 's16le'}).encode() + b'\n')
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), 1)
        if not line:
            writer.close()
            await writer.wait_closed()
            return None
        self.assertTrue(json.loads(line)['ready'])
        if malformed:
            writer.write(struct.pack('!H', 639))
        else:
            packet = bytes(640) if quiet else tone()
            for _ in range(blocks):
                writer.write(struct.pack('!H', 640) + packet)
            writer.write(struct.pack('!H', end))
        await writer.drain()
        result = json.loads(await asyncio.wait_for(reader.readline(), 2))
        writer.close()
        await writer.wait_closed()
        await asyncio.sleep(0.01)
        return result

    async def test_stream_authentication_and_cancel(self):
        self.assertIsNone(await self.recording(token='wrong'))
        self.assertEqual((await self.recording(end=65535))['status'], 'cancelled')
        self.assertEqual(self.worker.calls, 0)
        self.assertFalse(self.bridge.state['events'])

    async def test_gate_before_transcription_and_one_persisted_event(self):
        self.assertEqual((await self.recording(blocks=49))['status'], 'discarded')
        self.assertEqual((await self.recording(quiet=True))['status'], 'discarded')
        self.assertEqual(self.worker.calls, 0)
        self.assertEqual((await self.recording(blocks=50))['status'], 'transcribed')
        self.assertEqual((await self.recording())['status'], 'transcribed')
        self.assertEqual(len(self.bridge.state['events']), 1)
        self.assertEqual(self.bridge.state['events'][0]['text'], 'A local test.')
        self.assertFalse(self.service.active)

    async def test_live_pcm_is_raw_even_with_an_old_filter_profile(self):
        self.service.tuning.profile = 'trim_highpass'
        self.assertEqual((await self.recording())['status'], 'transcribed')
        self.assertEqual(self.worker.pcm, tone() * 101)
        self.assertEqual(self.service.status()['preprocessing'], 'raw')

    async def test_malformed_packet_does_not_poison_next_session(self):
        self.assertIn('error', await self.recording(malformed=True))
        self.assertEqual((await self.recording())['status'], 'transcribed')


if __name__ == '__main__':
    unittest.main()
