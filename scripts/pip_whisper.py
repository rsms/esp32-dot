"""Resident local whisper.cpp workers with isolated utterances and in-memory WAVs."""
import asyncio
import io
import wave
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parent.parent


class WhisperServer:
    def __init__(self, model):
        self.model = Path(model).resolve()
        self.process = None
        self.log = None
        # Ignore proxy environment variables: audio must stay on loopback.
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def command(self):
        binary = ROOT / '.tools/whisper-build/bin/whisper-server'
        if not binary.exists() or not self.model.is_file():
            raise RuntimeError('Run scripts/setup-whisper.py first')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        self.url = f'http://127.0.0.1:{port}'
        return [str(binary), '-m', str(self.model), '--host', '127.0.0.1',
            '--port', str(port), '-l', 'en', '-t', '4', '-bs', '5', '-bo', '1']

    def healthy(self):
        with self.http.open(self.url + '/health', timeout=1) as response:
            return json.load(response).get('status') == 'ok'

    def __enter__(self):
        command = self.command()
        self.log = tempfile.TemporaryFile()
        try:
            self.process = subprocess.Popen(command, stdout=self.log, stderr=self.log)
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise RuntimeError('whisper-server exited during startup')
                try:
                    if self.healthy():
                        return self
                except (OSError, urllib.error.URLError):
                    pass
                time.sleep(0.2)
            raise TimeoutError('Whisper startup timed out')
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def transcribe(self, path):
        return self.transcribe_wav(Path(path).read_bytes())

    def transcribe_pcm(self, pcm):
        if len(pcm) % 2 or not 0 < len(pcm) <= 16000 * 2 * 30:
            raise ValueError('Expected at most 30 seconds of mono PCM16')
        output = io.BytesIO()
        with wave.open(output, 'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(pcm)
        return self.transcribe_wav(output.getvalue())

    def transcribe_wav(self, data):
        boundary = uuid.uuid4().hex
        parts = []
        for key, value in [('temperature', '0'), ('response_format', 'json'), ('language', 'en')]:
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="sample.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode()
            + data + b'\r\n')
        parts.append(f'--{boundary}--\r\n'.encode())
        request = urllib.request.Request(self.url + '/inference', data=b''.join(parts),
            headers={'Content-Type': 'multipart/form-data; boundary=' + boundary})
        start = time.perf_counter()
        with self.http.open(request, timeout=30) as response:
            result = json.load(response)
        if not isinstance(result, dict) or not isinstance(result.get('text'), str):
            raise ValueError('Invalid Whisper response')
        return {'text': result['text'].strip(), 'seconds': time.perf_counter() - start}

    def __exit__(self, *args):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        if self.log:
            self.log.close()


class WhisperWorker:
    name = 'whisper-large-v3-turbo-q8_0'

    def __init__(self, model=None):
        self.server = WhisperServer(model or ROOT / '.tools/whisper-models/ggml-large-v3-turbo-q8_0.bin')
        self.ready = False
        self.error = None
        self.process = None
        self.lock = asyncio.Lock()
        self.task = None
        self.initialized = asyncio.Event()

    async def start(self):
        if self.task is None:
            self.initialized.clear()
            self.task = asyncio.create_task(self._supervise())
        await self.initialized.wait()

    async def _supervise(self):
        try:
            while True:
                try:
                    command = self.server.command()
                    self.process = await asyncio.create_subprocess_exec(*command,
                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                    deadline = time.monotonic() + 120
                    while time.monotonic() < deadline:
                        if self.process.returncode is not None:
                            raise RuntimeError('Whisper exited during startup')
                        try:
                            if await asyncio.to_thread(self.server.healthy):
                                break
                        except (OSError, ValueError):
                            pass
                        await asyncio.sleep(0.2)
                    else:
                        raise TimeoutError('Whisper startup timed out')
                    # Initialize Metal kernels before accepting microphone input.
                    # This synthetic silence is never published as a transcript.
                    await asyncio.to_thread(self.server.transcribe_pcm, bytes(32000))
                    self.ready, self.error = True, None
                    self.initialized.set()
                    await self.process.wait()
                    self.error = 'Whisper exited; restarting'
                except (OSError, ValueError, KeyError, RuntimeError) as error:
                    self.error = str(error)[:200]
                finally:
                    self.ready = False
                    self.initialized.set()
                    await self._stop_process()
                await asyncio.sleep(2)
        finally:
            self.ready = False

    async def _stop_process(self):
        if self.process and self.process.returncode is None:
            try:
                self.process.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self.process.wait(), 5)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
        self.process = None

    async def stop(self):
        self.ready = False
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None

    async def transcribe(self, request_id, pcm):
        if not self.ready or self.lock.locked():
            raise RuntimeError('Transcriber unavailable or busy')
        async with self.lock:
            try:
                result = await asyncio.to_thread(self.server.transcribe_pcm, bytes(pcm))
                return {'id': request_id, 'text': result['text'],
                    'transcribe_seconds': result['seconds']}
            except (OSError, ValueError, KeyError) as error:
                self.ready = False
                self.error = 'Whisper request failed; restarting'
                if self.process and self.process.returncode is None:
                    try:
                        self.process.kill()
                    except ProcessLookupError:
                        pass
                raise RuntimeError(self.error) from error
