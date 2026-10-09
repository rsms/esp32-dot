"""Temporary local whisper.cpp server for offline evaluations, with no prompt/context."""
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

    def __enter__(self):
        binary = ROOT / '.tools/whisper-build/bin/whisper-server'
        if not binary.exists() or not self.model.is_file():
            raise RuntimeError('Run scripts/setup-whisper.py first')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        self.url = f'http://127.0.0.1:{port}'
        self.log = tempfile.TemporaryFile()
        try:
            self.process = subprocess.Popen([str(binary), '-m', str(self.model),
                '--host', '127.0.0.1', '--port', str(port), '-l', 'en', '-t', '4',
                '-bs', '5', '-bo', '1'], stdout=self.log, stderr=self.log)
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise RuntimeError('whisper-server exited during startup')
                try:
                    with self.http.open(self.url + '/health', timeout=1) as response:
                        if json.load(response).get('status') == 'ok':
                            return self
                except (OSError, urllib.error.URLError):
                    pass
                time.sleep(0.2)
            raise TimeoutError('Whisper startup timed out')
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def transcribe(self, path):
        boundary = uuid.uuid4().hex
        parts = []
        for key, value in [('temperature', '0'), ('response_format', 'json'), ('language', 'en')]:
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="sample.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode()
            + Path(path).read_bytes() + b'\r\n')
        parts.append(f'--{boundary}--\r\n'.encode())
        request = urllib.request.Request(self.url + '/inference', data=b''.join(parts),
            headers={'Content-Type': 'multipart/form-data; boundary=' + boundary})
        start = time.perf_counter()
        with self.http.open(request, timeout=120) as response:
            result = json.load(response)
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
