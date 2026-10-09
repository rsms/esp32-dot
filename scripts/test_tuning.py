import asyncio
from array import array
import base64
import copy
import json
import math
from pathlib import Path
import struct
import tempfile
import unittest
import wave

from bridge import Bridge
from pip_audio import AudioService, Recording
from pip_tuning import MODES, PHRASES, VoiceTuning, preprocess, recommendation, word_error, recording_content, list_recordings
from test_audio import FakeWorker, tone
from test_mcp import Peer


class AnalysisTests(unittest.TestCase):
    def test_word_errors_and_filter(self):
        self.assertEqual(word_error('Hello, Dot!', 'hello dot')['wer'], 0)
        self.assertEqual(word_error('one two three', 'one four')['errors'], 2)
        self.assertEqual(word_error('one', 'one two three')['wer'], 2)
        pcm = struct.pack('<16000h', *([4000]*16000))
        self.assertEqual(preprocess(pcm, 'raw'), pcm)
        high = struct.unpack('<16000h', preprocess(pcm, 'highpass'))
        self.assertLess(max(abs(s) for s in high[8000:]), 2)
        for mode in MODES:
            self.assertLessEqual(len(preprocess(pcm, mode)), len(pcm))

    def test_validation_is_separate_and_rejects_regression(self):
        records = [{'reference': f'hello dot number {i}', 'split': 'practice' if i < 3 else 'validation',
            'results': {m: {'text': f'hello dot number {i}' if m == 'highpass' else 'hello not'} for m in MODES}}
            for i in range(5)]
        self.assertTrue(recommendation(records)['eligible'])
        self.assertFalse(recommendation(records[:4])['eligible'])
        bad = copy.deepcopy(records)
        bad[-1]['results']['highpass']['text'] = 'one two three four five six seven'
        self.assertFalse(recommendation(bad)['eligible'])
        # Validation success cannot select a candidate that lost on practice.
        for r in records[:3]:
            r['results']['highpass']['text'] = 'incorrect'
        self.assertFalse(recommendation(records)['eligible'])


class TuningTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.bridge = Bridge('test-token', self.root / 'bridge.json')
        self.worker = FakeWorker()
        self.bridge.audio = AudioService(self.bridge, self.worker)
        self.tuning = VoiceTuning(self.bridge, self.root / 'tuning')
        self.bridge.audio.tuning = self.tuning
        self.peer = Peer()
        await self.bridge.attach(self.peer)

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_whisper_does_not_apply_a_filter_profile(self):
        self.worker.name = 'whisper-large-v3-turbo-q8_0'
        with self.assertRaisesRegex(ValueError, 'uses raw PCM'):
            await self.tuning.command({'action': 'apply'})

    async def capture(self):
        await self.tuning.command({'action': 'start'})
        context = self.tuning.capture_context(self.tuning.prompt['id'])
        recording = Recording()
        for _ in range(150):
            recording.append(tone())
        await self.tuning.capture(context, recording, dict(recording.summary(), id='test-recording'), self.worker)
        return recording

    async def test_saved_wav_results_and_no_cloud_events(self):
        recording = await self.capture()
        record = self.tuning.records[0]
        with wave.open(record['wav'], 'rb') as audio:
            self.assertEqual(audio.getparams()[:3], (1, 2, 16000))
            self.assertEqual(audio.readframes(48000), bytes(recording.pcm))
        self.assertEqual(Path(record['wav']).stat().st_mode & 0o777, 0o600)
        self.assertEqual(set(record['results']), set(MODES))
        self.assertFalse(self.bridge.state['events'])
        old = self.tuning.prompt['id']
        await self.tuning.device_action(old, 'submit')
        self.assertEqual(self.tuning.records[0]['decision'], 'accepted')
        self.assertNotEqual(self.tuning.prompt['id'], old)
        with self.assertRaises(ValueError):
            self.tuning.capture_context(old)
        await self.tuning.stop()
        self.assertFalse(self.tuning.active)
        self.assertEqual(self.peer.messages[-1]['id'], '')

    async def test_incoming_card_and_reconnect_stop_tuning(self):
        await self.tuning.command({'action': 'start'})
        await self.bridge.add({'id': 'new', 'body': 'An incoming message'})
        self.assertFalse(self.tuning.active)
        self.assertEqual(self.peer.messages[-1]['type'], 'card')
        await self.bridge.cancel('new')
        await self.tuning.command({'action': 'start'})
        await self.bridge.attach(Peer())
        self.assertFalse(self.tuning.active)

    async def test_audio_content_is_bounded_and_has_no_reference(self):
        await self.capture()
        entries = list_recordings(self.tuning.root)
        self.assertEqual(len(entries), 1)
        self.assertNotIn('reference', entries[0])
        content = await recording_content(self.tuning.root, entries[0]['recording_id'], encoding='wav')
        self.assertEqual(content['type'], 'audio')
        self.assertEqual(content['mimeType'], 'audio/wav')
        self.assertTrue(base64.b64decode(content['data']).startswith(b'RIFF'))
        for rid in ('../../secret', 'session-aaaaaaaaaaaa:9999'):
            with self.assertRaises(ValueError):
                await recording_content(self.tuning.root, rid)
        with self.assertRaises(ValueError):
            await self.tuning.command({'action': 'apply'})

    async def test_stream_is_bound_to_prompt_and_not_published(self):
        await self.tuning.command({'action': 'start'})
        server = await asyncio.start_server(self.bridge.audio.client, '127.0.0.1', 0)
        async with server:
            reader, writer = await asyncio.open_connection('127.0.0.1', server.sockets[0].getsockname()[1])
            writer.write(json.dumps({'type':'audio', 'version':1, 'token':'test-token', 'id':'stream-test',
                'tuning_id': self.tuning.prompt['id'], 'sample_rate':16000, 'channels':1, 'format':'s16le'}).encode()+b'\n')
            await writer.drain()
            self.assertTrue(json.loads(await reader.readline())['ready'])
            for _ in range(150):
                writer.write(struct.pack('!H', 640)+tone())
            writer.write(b'\x00\x00')
            await writer.drain()
            self.assertEqual(json.loads(await reader.readline())['status'], 'tuning')
            writer.close()
            await writer.wait_closed()
            await asyncio.sleep(.02)
        self.assertEqual(len(self.tuning.records), 1)
        self.assertFalse(self.bridge.state['events'])
        self.assertFalse(self.bridge.audio.active)

    async def test_retry_rejects_take_and_duplicate_submit_does_not_advance(self):
        await self.capture()
        old = self.tuning.prompt['id']
        phrase = self.tuning.prompt['reference']
        await self.tuning.device_action(old, 'retry')
        self.assertEqual(self.tuning.records[-1]['decision'], 'rejected')
        self.assertEqual(self.tuning.prompt['reference'], phrase)
        self.assertNotIn('record', self.peer.messages[-1])
        retried_id = self.tuning.prompt['id']
        await self.tuning.device_action(old, 'retry')
        self.assertEqual(self.tuning.prompt['id'], retried_id)
        self.assertFalse(recommendation(self.tuning.records)['scores']['practice']['recordings'])
        recording = Recording()
        for _ in range(150):
            recording.append(tone())
        await self.tuning.capture(self.tuning.capture_context(retried_id), recording,
            dict(recording.summary(), id='second'), self.worker)
        await self.tuning.device_action(retried_id, 'submit')
        next_id = self.tuning.prompt['id']
        self.assertNotEqual(self.tuning.prompt['reference'], phrase)
        await self.tuning.device_action(retried_id, 'submit')
        self.assertEqual(self.tuning.prompt['id'], next_id)
        self.assertEqual(self.tuning.records[-1]['decision'], 'accepted')

    async def test_short_sample_is_saved_without_hallucinated_text(self):
        await self.tuning.command({'action': 'start'})
        recording = Recording()
        recording.append(tone())
        await self.tuning.capture(self.tuning.capture_context(self.tuning.prompt['id']), recording,
            dict(recording.summary(), id='short'), self.worker)
        self.assertEqual(self.tuning.records[0]['status'], 'too_short_or_quiet')
        self.assertFalse(self.tuning.records[0]['results'])
        self.assertTrue(Path(self.tuning.records[0]['wav']).exists())


if __name__ == '__main__':
    unittest.main()
