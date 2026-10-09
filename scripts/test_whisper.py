import asyncio
import io
from unittest.mock import patch
import unittest
import wave
from pip_whisper import WhisperServer, WhisperWorker


class MemoryTests(unittest.TestCase):
    def test_wav_transport_preserves_raw_pcm(self):
        server = WhisperServer('unused-model')
        captured = []
        server.transcribe_wav = lambda data: captured.append(data)
        pcm = bytes(range(256)) * 8
        server.transcribe_pcm(pcm)
        with wave.open(io.BytesIO(captured[0])) as wav:
            self.assertEqual((wav.getnchannels(), wav.getsampwidth(), wav.getframerate()), (1, 2, 16000))
            self.assertEqual(wav.readframes(wav.getnframes()), pcm)
        for bad in (b'', b'\0', bytes(960002)):
            with self.assertRaises(ValueError):
                server.transcribe_pcm(bad)


class Process:
    returncode = None

    def __init__(self):
        self.done = asyncio.Event()

    async def wait(self):
        await self.done.wait()
        return self.returncode

    def terminate(self):
        self.returncode = -15
        self.done.set()

    def kill(self):
        self.terminate()


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_warmup_inference_failure_and_shutdown(self):
        worker = WhisperWorker('unused-model')
        worker.server.command = lambda: ['unused-command']
        worker.server.healthy = lambda: True
        calls = []

        def recognize(pcm):
            calls.append(pcm)
            return {'text': 'Hello Dot.', 'seconds': 0.1}

        worker.server.transcribe_pcm = recognize
        process = Process()
        with patch('pip_whisper.asyncio.create_subprocess_exec', return_value=process):
            await worker.start()
            self.assertTrue(worker.ready)
            self.assertEqual(calls, [bytes(32000)])
            result = await worker.transcribe('sample', b'\x01\x00' * 32000)
            self.assertEqual(result, {'id': 'sample', 'text': 'Hello Dot.', 'transcribe_seconds': 0.1})
            self.assertEqual(calls[-1], b'\x01\x00' * 32000)
            worker.server.transcribe_pcm = lambda _: (_ for _ in ()).throw(OSError('lost connection'))
            with self.assertRaises(RuntimeError):
                await worker.transcribe('failed', b'\0\0')
            self.assertFalse(worker.ready)
            await worker.stop()
            self.assertIsNotNone(process.returncode)
            self.assertIsNone(worker.task)

    async def test_stop_while_starting_terminates_child(self):
        worker = WhisperWorker('unused-model')
        worker.server.command = lambda: ['unused-command']
        worker.server.healthy = lambda: False
        process = Process()
        with patch('pip_whisper.asyncio.create_subprocess_exec', return_value=process):
            starting = asyncio.create_task(worker.start())
            while worker.process is None:
                await asyncio.sleep(0)
            await worker.stop()
            await asyncio.wait_for(starting, 1)
            self.assertFalse(worker.ready)
            self.assertIsNotNone(process.returncode)


if __name__ == '__main__':
    unittest.main()
