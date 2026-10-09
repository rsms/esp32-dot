"""Local, labelled voice calibration. References are never given to the recognizer."""
import asyncio
import base64
from array import array
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import time
import uuid
import wave

RATE = 16000
MODES = ('raw', 'highpass', 'trim_highpass')
PHRASES = (
    'Hello Dot. Remind me to water the plants tomorrow morning.',
    'Please show me the next message on my desk display.',
    'Set a timer for twenty minutes while I make dinner.',
    'Move the meeting from Tuesday to Thursday afternoon.',
    'The quick brown fox jumps over the lazy dog.',
    'I would like to leave at quarter past three.',
    'Turn down the brightness when the screen is sleeping.',
    'Please add milk, coffee, and apples to my shopping list.',
    'There are seven small birds sitting outside the window.',
    'Cancel the reminder and keep the original appointment.',
    'The train leaves at ten thirty from platform five.',
    'Tell me whether I need an umbrella tomorrow morning.',
)


def words(text):
    return re.findall(r"[a-z0-9]+(?:'[a-z0-9]+)?", text.lower().replace("’", "'"))


def word_error(reference, hypothesis):
    a, b = words(reference), words(hypothesis)
    row = list(range(len(b) + 1))
    for i, word in enumerate(a, 1):
        nxt = [i]
        for j, other in enumerate(b, 1):
            nxt.append(min(row[j] + 1, nxt[-1] + 1, row[j - 1] + (word != other)))
        row = nxt
    return {'errors': row[-1], 'words': len(a), 'wer': row[-1] / max(1, len(a))}


def preprocess(pcm, mode):
    if mode not in MODES:
        raise ValueError('Unknown audio preprocessing mode')
    if mode == 'raw':
        return bytes(pcm)
    samples = array('h', pcm)
    if sys.byteorder != 'little':
        samples.byteswap()
    # First-order 80 Hz high-pass removes DC and low-frequency rumble. No gain
    # normalization or noise gate: neither should amplify noise or hide words.
    alpha = math.exp(-2 * math.pi * 80 / RATE)
    filtered, previous_x, previous_y = [], 0, 0
    for x in samples:
        y = alpha * (previous_y + x - previous_x)
        filtered.append(max(-32768, min(32767, round(y))))
        previous_x, previous_y = x, y
    if mode == 'trim_highpass':
        # Use recording-relative energy, preserve 250 ms context on both ends.
        # Trimming is only a candidate, never assumed to improve recognition.
        levels = [math.sqrt(sum(x*x for x in filtered[i:i+320]) / max(1, len(filtered[i:i+320])))
            for i in range(0, len(filtered), 320)]
        threshold = max(100, max(levels, default=0) * .08)
        active = [i for i, level in enumerate(levels) if level >= threshold]
        if active:
            filtered = filtered[max(0, active[0]*320-4000):min(len(filtered), (active[-1]+1)*320+4000)]
    output = array('h', filtered)
    if sys.byteorder != 'little':
        output.byteswap()
    return output.tobytes()


def save_wav(path, pcm):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'wb') as file:
        with wave.open(file, 'wb') as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(RATE)
            output.writeframes(pcm)


def private_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix('.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as file:
        json.dump(value, file, indent=2)
        file.write('\n')
    tmp.replace(path)


def recommendation(records):
    usable = [r for r in records if r.get('decision', 'accepted') == 'accepted' and all(m in r.get('results', {}) for m in MODES)]
    scores = {}
    for split in ('practice', 'validation'):
        rows = [r for r in usable if r['split'] == split]
        scores[split] = {'phrases': len({r['reference'] for r in rows}), 'recordings': len(rows)}
        for mode in MODES:
            counts = [word_error(r['reference'], r['results'][mode]['text']) for r in rows]
            scores[split][mode] = sum(c['errors'] for c in counts) / max(1, sum(c['words'] for c in counts))
    best = min(MODES, key=lambda m: scores['practice'][m])
    enough = scores['practice']['phrases'] >= 3 and scores['validation']['phrases'] >= 2
    validation = [r for r in usable if r['split'] == 'validation']
    improves = (best != 'raw' and scores['practice'][best] < scores['practice']['raw']
        and scores['validation'][best] < scores['validation']['raw']
        and all(word_error(r['reference'], r['results'][best]['text'])['errors'] <=
            word_error(r['reference'], r['results']['raw']['text'])['errors'] for r in validation))
    return {'candidate': best, 'eligible': enough and improves, 'scores': scores,
        'reason': 'Validated improvement' if enough and improves else
            'Need at least 3 practice and 2 distinct validation phrases' if not enough else
            'No consistent improvement over original audio'}


class VoiceTuning:
    def __init__(self, bridge, root):
        self.bridge, self.root = bridge, root
        self.session = None
        self.active = False
        self.prompt = None
        self.records = []
        self.index = 0
        self.phrases = PHRASES
        self.last_command = {"type":"voice_tune", "id":"", "phrase":""}
        self.profile = 'raw'
        profile = root / 'profile.json'
        if profile.exists():
            try:
                mode = json.loads(profile.read_text())['mode']
                if mode in MODES:
                    self.profile = mode
            except (ValueError, KeyError, OSError):
                pass

    def status(self):
        return {'active': self.active, 'session': self.session, 'prompt': self.prompt,
            'recordings': len(self.records), 'last': self.records[-1] if self.records else None,
            'profile': self.profile, 'recommendation': recommendation(self.records)}

    async def command(self, data):
        action = data.get('action')
        if action == 'start':
            if self.active or self.bridge.audio.active:
                raise ValueError('Stop the current recording or tuning session first')
            if not self.bridge.peer or self.bridge.current() or not self.bridge.audio.worker.ready:
                raise ValueError('Need a connected device, empty message queue, and ready audio model')
            phrases = data.get('phrases', PHRASES)
            if (not isinstance(phrases, (list, tuple)) or not 8 <= len(phrases) <= 100 or
                any(not isinstance(p, str) or not 1 <= len(p.encode()) <= 160 or
                    not all(32 <= ord(c) <= 126 or c == '’' for c in p) for p in phrases) or len(set(phrases)) != len(phrases)):
                raise ValueError('Provide 8-100 distinct phrases using printable ASCII or ’, each at most 160 UTF-8 bytes')
            self.phrases = phrases
            self.session = 'session-' + uuid.uuid4().hex[:12]
            directory = self.root / self.session
            directory.mkdir(parents=True, mode=0o700)
            self.records, self.index = [], 0
            self.active = True
            self.write()
            try:
                await self.advance()
            except BaseException:
                self.active = False
                self.write()
                raise
        elif action == 'stop':
            await self.stop()
        elif action == 'apply':
            report = recommendation(self.records)
            if not report['eligible']:
                raise ValueError(report['reason'])
            self.profile = report['candidate']
            private_json(self.root / 'profile.json', {'mode': self.profile, 'session': self.session, 'report': report})
        elif action == 'reset':
            self.profile = 'raw'
            private_json(self.root / 'profile.json', {'mode': 'raw'})
        else:
            raise ValueError('Expected start, stop, apply, or reset')
        return self.status()

    async def stop(self, notify=True):
        was_active = self.active
        self.active = False
        self.prompt = None
        if self.records and self.records[-1].get('decision') == 'pending':
            self.records[-1]['decision'] = 'abandoned'
        self.last_command = {"type":"voice_tune", "id":"", "phrase":""}
        if self.session:
            self.write()
        if was_active and notify and self.bridge.peer:
            await self.bridge.peer.send(self.last_command)

    def write(self):
        if self.session:
            private_json(self.root / self.session / 'session.json', {
                'session': self.session, 'active': self.active, 'phrases': self.phrases,
                'records': self.records, 'recommendation': recommendation(self.records)})

    async def advance(self):
        if not self.active:
            return
        if not self.bridge.peer or self.bridge.current():
            await self.stop()
            return
        phrase_index = self.index % len(self.phrases)
        self.prompt = {'id': uuid.uuid4().hex, 'reference': self.phrases[phrase_index],
            'split': 'validation' if phrase_index % 4 == 3 else 'practice'}
        self.index += 1
        await self.send_prompt()

    async def send_prompt(self):
        self.last_command = {'type':'voice_tune', 'id':self.prompt['id'],
            'phrase':self.prompt['reference']}
        await self.bridge.peer.send(self.last_command)

    async def device_action(self, prompt_id, action):
        if action not in ('retry', 'exit', 'submit'):
            raise ValueError('Unknown tuning action')
        if not self.active or not self.prompt or prompt_id != self.prompt['id']:
            # Repeated requests resend the authoritative state, without accepting
            # twice or starting another recording on the device.
            if self.bridge.peer:
                await self.bridge.peer.send(self.last_command)
            return
        if action == 'exit':
            await self.stop()
            return
        record = self.records[-1] if self.records else None
        if (self.bridge.audio.active or not record or record.get('prompt_id') != prompt_id
            or record.get('decision') != 'pending'):
            return
        record['decision'] = 'accepted' if action == 'submit' else 'rejected'
        self.write()
        if action == 'submit':
            await self.advance()
        else:
            self.prompt = dict(self.prompt, id=uuid.uuid4().hex)
            await self.send_prompt()

    def capture_context(self, tuning_id):
        if not self.active or not self.prompt or tuning_id != self.prompt['id']:
            raise ValueError('Stale or missing voice tuning prompt')
        return dict(self.prompt, session=self.session)

    async def capture(self, context, recording, summary, worker):
        if not self.active or context['session'] != self.session or context['id'] != self.prompt['id']:
            return
        directory = self.root / self.session
        stem = f'{len(self.records)+1:04d}'
        original = directory / (stem + '-raw.wav')
        save_wav(original, recording.pcm)
        record = dict(context, **summary, prompt_id=context['id'], decision='pending',
            wav=str(original), results={}, timestamp=time.time())
        self.records.append(record)
        # Save the source before trying inference, so a worker failure cannot
        # destroy the labelled recording. No tuning transcript enters MCP events.
        self.write()
        if not recording.accepted():
            record['status'] = 'too_short_or_quiet'
            self.write()
            return
        try:
            for mode in MODES:
                if not self.active or context['session'] != self.session:
                    break
                pcm = preprocess(recording.pcm, mode)
                path = directory / (stem + '-' + mode + '.wav')
                if mode != 'raw':
                    save_wav(path, pcm)
                result = await worker.transcribe(stem + '-' + mode, pcm)
                record['results'][mode] = dict(text=result['text'], wav=str(path),
                    transcribe_seconds=result['transcribe_seconds'], **word_error(context['reference'], result['text']))
                self.write()
        finally:
            self.write()


def list_recordings(root):
    output = []
    for path in sorted(root.glob('session-*/session.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:20]:
        session = json.loads(path.read_text())
        for index in range(len(session['records']), max(0, len(session['records']) - 200), -1):
            record = session['records'][index - 1]
            output.append({'recording_id': f"{path.parent.name}:{index:04d}",
                'audio_seconds': record['seconds'], 'created_at': record['timestamp']})
    return output[:200]


async def recording_content(root, recording_id, mode='raw', encoding='mp3'):
    if not re.fullmatch(r'session-[0-9a-f]{12}:[0-9]{4,6}', recording_id) or mode not in MODES or encoding not in ('mp3', 'wav'):
        raise ValueError('Invalid recording selection')
    session, index = recording_id.split(':')
    path = root / session / f'{index}-{mode}.wav'
    if not path.is_file() or path.is_symlink() or path.parent.is_symlink() or path.stat().st_size > 960044:
        raise ValueError('Recording unavailable')
    with wave.open(str(path), 'rb') as audio:
        if audio.getparams()[:3] != (1, 2, RATE) or audio.getnframes() > RATE * 30:
            raise ValueError('Unexpected recording format')
    if encoding == 'mp3':
        encoder = shutil.which('ffmpeg') or '/opt/homebrew/bin/ffmpeg'
        process = await asyncio.create_subprocess_exec(encoder, '-v', 'error', '-nostdin',
            '-i', str(path), '-ac', '1', '-ar', '16000', '-codec:a', 'libmp3lame',
            '-b:a', '32k', '-f', 'mp3', 'pipe:1',
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            data, _ = await asyncio.wait_for(process.communicate(), 10)
        except BaseException:
            process.kill()
            await process.wait()
            raise
        if process.returncode or not 1 <= len(data) <= 256000:
            raise ValueError('MP3 encoding failed; request WAV instead')
        mime = 'audio/mpeg'
    else:
        data, mime = path.read_bytes(), 'audio/wav'
    return {'type': 'audio', 'mimeType': mime, 'data': base64.b64encode(data).decode()}
