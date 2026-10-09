"""Bounded PCM streaming and resident local ASR worker for the local bridge."""
import asyncio
from array import array
import hmac
import json
import math
import os
from pathlib import Path
import struct
import sys
import tempfile
import time
import wave
from pip_tuning import VoiceTuning
from pip_whisper import WhisperWorker

ROOT = Path(__file__).resolve().parent.parent
RATE = 16000
MAX_SAMPLES = RATE * 30
BLOCK = 320


class Recording:
    def __init__(self, threshold=0.0126):
        self.pcm = bytearray()
        self.voiced = 0
        self.peak = 0
        self.max_rms = 0
        self.threshold = threshold
        self.levels = []
        self.clipped = 0
        self.dc_sum = 0

    def append(self, data):
        if len(data) != BLOCK * 2 or len(self.pcm) + len(data) > MAX_SAMPLES * 2:
            raise ValueError('Invalid or oversized audio packet')
        samples = array('h', data)
        if sys.byteorder != 'little':
            samples.byteswap()
        mean = sum(samples) / len(samples)
        rms = math.sqrt(sum((s - mean) ** 2 for s in samples) / len(samples)) / 32768
        self.peak = max(self.peak, max(abs(s) for s in samples) / 32768)
        self.max_rms = max(self.max_rms, rms)
        # Ignore initial codec/tap transient; require sustained significant levels.
        if len(self.pcm) >= RATE // 5:
            self.levels.append(rms)
            self.clipped += sum(abs(s) >= 32112 for s in samples)
            self.dc_sum += mean
            if rms >= self.threshold:
                self.voiced += BLOCK
        self.pcm.extend(data)

    def summary(self):
        levels = sorted(self.levels) or [0]
        return {'seconds': len(self.pcm) / (RATE * 2), 'voiced_seconds': self.voiced / RATE,
            'peak': self.peak, 'max_rms': self.max_rms,
            'clipped_fraction': self.clipped / max(1, len(self.levels) * BLOCK),
            'dc_offset': self.dc_sum / max(1, len(self.levels)) / 32768,
            'rms_p50': levels[len(levels) // 2], 'rms_p95': levels[int((len(levels) - 1) * .95)]}

    def accepted(self):
        return len(self.pcm) >= RATE * 4 and self.voiced >= RATE // 5


class PhononWorker:
    def __init__(self):
        self.process = None
        self.ready = False
        self.lock = asyncio.Lock()
        self.error = None

    async def start(self):
        binary = ROOT / '.tools/phonon-build/release/DotTranscriber'
        model = ROOT / '.tools/Phonon-2-CoreML'
        if not binary.exists() or not model.exists():
            self.error = 'Run scripts/setup-phonon.sh to install the comparison model and worker'
            return
        try:
            directory = ROOT / '.tools/audio'
            if directory.exists():
                for leftover in directory.glob('dot-recording-*.wav'):
                    if leftover.stat().st_mtime < time.time() - 120:
                        leftover.unlink(missing_ok=True)
            self.process = await asyncio.create_subprocess_exec(str(binary), str(model),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, limit=262144)
            line = await asyncio.wait_for(self.process.stdout.readline(), 600)
            value = json.loads(line)
            if not value.get('ready'):
                raise ValueError('Worker did not become ready')
            self.ready = True
            self.error = None
        except (OSError, ValueError, asyncio.TimeoutError) as error:
            self.error = type(error).__name__
            await self.stop()

    async def stop(self):
        self.ready = False
        if self.process and self.process.returncode is None:
            self.process.kill()
            await self.process.wait()

    async def transcribe(self, request_id, pcm):
        if not self.ready or self.lock.locked():
            raise RuntimeError('Transcriber unavailable or busy')
        async with self.lock:
            # Phonon accepts files or float arrays. A private temporary WAV keeps
            # the process protocol small; raw recordings are deleted in all cases.
            directory = ROOT / '.tools/audio'
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd, name = tempfile.mkstemp(prefix='dot-recording-', suffix='.wav', dir=directory)
            os.close(fd)
            try:
                with wave.open(name, 'wb') as output:
                    output.setnchannels(1)
                    output.setsampwidth(2)
                    output.setframerate(RATE)
                    output.writeframes(pcm)
                self.process.stdin.write(json.dumps({'id': request_id, 'path': name}).encode() + b'\n')
                await self.process.stdin.drain()
                line = await asyncio.wait_for(self.process.stdout.readline(), 45)
                result = json.loads(line)
                if result.get('id') != request_id or 'error' in result:
                    raise RuntimeError('Transcription failed')
                return result
            except (ValueError, OSError, asyncio.TimeoutError, RuntimeError):
                await self.stop()
                # A fresh process prevents a late response from being mistaken
                # for the next request's result.
                asyncio.create_task(self.start())
                raise
            finally:
                Path(name).unlink(missing_ok=True)


class AudioService:
    def __init__(self, bridge, worker=None, threshold=0.0126):
        self.bridge = bridge
        self.worker = worker or WhisperWorker()
        self.threshold = threshold
        self.active = False
        self.stage = "idle"
        self.last = None
        self.tuning = VoiceTuning(bridge, ROOT / ".tools/voice-tuning")

    def status(self):
        return {'ready': self.worker.ready, 'recording': self.stage == 'recording',
            'busy': self.active, 'stage': self.stage,
            'error': self.worker.error, 'last': self.last, 'preprocessing': 'raw',
            'model': getattr(self.worker, 'name', 'phonon-2'), 'tuning': self.tuning.active}

    async def client(self, reader, writer):
        owns_session = False
        recording = None
        tuning_context = None
        async def reply(value):
            writer.write(json.dumps(value, separators=(',', ':')).encode() + b'\n')
            await writer.drain()
        try:
            hello = json.loads(await asyncio.wait_for(reader.readline(), 3))
            if (hello.get('type') != 'audio' or hello.get('version') != 1 or
                not hmac.compare_digest(str(hello.get('token', '')), self.bridge.token)):
                return
            request_id = hello.get('id')
            if (not isinstance(request_id, str) or not 1 <= len(request_id) <= 64 or
                any(not (c.isascii() and (c.isalnum() or c in '-_')) for c in request_id) or
                hello.get('sample_rate') != RATE or hello.get('channels') != 1 or hello.get('format') != 's16le'):
                raise ValueError('Invalid audio format or ID')
            if self.active or not self.worker.ready:
                await reply({'error': 'Audio service unavailable or busy'})
                return
            tuning_id = hello.get('tuning_id', '')
            if tuning_id or self.tuning.active:
                tuning_context = self.tuning.capture_context(tuning_id)
            self.active = owns_session = True
            self.stage = "recording"
            recording = Recording(self.threshold)
            started = time.monotonic()
            first_packet = None
            await reply({'ready': True})
            while True:
                size = struct.unpack('!H', await asyncio.wait_for(reader.readexactly(2), 3))[0]
                if size in (0, 65535):
                    break
                if size != BLOCK * 2 or time.monotonic() - started > 35:
                    raise ValueError('Invalid audio stream')
                packet = await asyncio.wait_for(reader.readexactly(size), 3)
                if first_packet is None:
                    first_packet = time.monotonic() - started
                recording.append(packet)
            summary = dict(recording.summary(), id=request_id, first_packet_seconds=first_packet)
            if tuning_context and size != 65535:
                self.stage = "transcribing"
                summary['status'] = 'tuning'
                await self.tuning.capture(tuning_context, recording, summary, self.worker)
                self.last = summary
                await reply(summary)
                return
            if size == 65535 or not recording.accepted():
                summary['status'] = 'cancelled' if size == 65535 else 'discarded'
                self.last = summary
                await reply(summary)
                return
            self.stage = "transcribing"
            result = await self.worker.transcribe(request_id, bytes(recording.pcm))
            text = result['text'].strip()
            summary.update(status='transcribed' if text else 'empty',
                transcribe_seconds=result['transcribe_seconds'])
            if text and not reader.at_eof():
                # Persist only recognized text, never raw audio. A bounded event
                # payload is suitable for the existing signed MCP event outbox.
                events = self.bridge.state['events']
                if not any(e.get('type') == 'transcript' and e.get('id') == request_id for e in events):
                    events.append({'seq': len(events) + 1, 'type': 'transcript', 'id': request_id,
                        'text': text[:8192], 'timestamp': time.time(), 'audio_seconds': summary['seconds'],
                        'transcribe_seconds': result['transcribe_seconds']})
                    self.bridge.save()
            self.last = summary
            await reply(summary)
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError,
                asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError) as error:
            if owns_session:
                self.last = {'status':'failed', 'stage':self.stage, 'error':type(error).__name__,
                    'seconds':recording.summary()['seconds'] if recording else 0}
            try:
                await reply({'error': 'Audio stream interrupted or transcription unavailable'})
            except (ConnectionError, OSError):
                pass
        finally:
            if recording:
                recording.pcm.clear()
            if owns_session:
                self.active = False
                self.stage = "idle"
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
