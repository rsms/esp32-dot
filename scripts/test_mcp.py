import asyncio
import base64
import hashlib
import hmac
import json
from pathlib import Path
import socket
import tempfile
import time
import unittest
from unittest.mock import patch

from bridge import Bridge
from pip_mcp import MCP, SKILL_URI, callback_url, canonical, public_addresses, signed_headers, signing_key


class Receiver:
    def __init__(self):
        self.messages = []
        self.status = 200
        self.verify = True

    def __call__(self, url, body, headers):
        payload = json.loads(body)
        self.messages.append((payload, body, headers))
        if payload.get('type') == 'verification':
            return 200, json.dumps({'challenge': payload['challenge'] if self.verify else 'wrong'}).encode()
        return self.status, b'{}'


class Peer:
    def __init__(self):
        self.messages = []

    async def send(self, message):
        self.messages.append(message)

    async def close(self):
        pass


class MCPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.bridge = Bridge('device-token', Path(self.temp.name) / 'state.json')
        self.receiver = Receiver()
        self.mcp = MCP(self.bridge, 'mcp-token', self.receiver)
        self.params = {'name': 'device.reply', 'arguments': {}, 'delivery': {'mode': 'webhook',
            'url': 'https://receiver.example/events', 'secret': 'whsec_' + base64.b64encode(b'K' * 32).decode()}}

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def call(self, name, arguments):
        reply = await self.mcp.rpc({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
            'params': {'name': name, 'arguments': arguments}})
        return reply['result']

    async def answer(self, rid='q1'):
        peer = Peer()
        await self.bridge.attach(peer)
        await self.bridge.receive(peer, {'type': 'choice', 'card_id': rid, 'option_id': 'yes'})
        return peer

    async def question(self, rid='q1'):
        return await self.call('ask_question', {'request_id': rid, 'text': 'Ready?',
            'options': [{'id': 'yes', 'label': 'Yes'}, {'id': 'later', 'label': 'Later'}]})

    async def test_question_reply_restart_delivery_and_deduplication(self):
        subscription = await self.mcp.subscribe(self.params)
        await self.question()
        peer = await self.answer()
        await self.bridge.receive(peer, {'type': 'choice', 'card_id': 'q1', 'option_id': 'yes'})
        # A crash after durable choice, before outbox creation, must not lose the reply.
        bridge = Bridge('device-token', self.bridge.state_path)
        mcp = MCP(bridge, 'mcp-token', self.receiver)
        await mcp.deliver_once()
        events = [m for m in self.receiver.messages if m[0].get('name') == 'device.reply']
        self.assertEqual(len(events), 1)
        event, raw, headers = events[0]
        self.assertEqual(event['data'], {'request_id': 'q1', 'kind': 'decision', 'option_id': 'yes', 'option_label': 'Yes'})
        signed = headers['webhook-id'].encode() + b'.' + headers['webhook-timestamp'].encode() + b'.' + raw
        signature = 'v1,' + base64.b64encode(hmac.digest(b'K' * 32, signed, 'sha256')).decode()
        self.assertEqual(headers['webhook-signature'], signature)
        self.assertEqual(headers['X-MCP-Subscription-Id'], subscription['id'])
        await mcp.deliver_once()
        self.assertEqual(len(self.receiver.messages), 2)  # verification + exactly one event
        self.assertEqual(bridge.get('q1')['status'], 'answered')

    async def test_retry_keeps_event_id_across_restart(self):
        await self.mcp.subscribe(self.params)
        await self.question()
        await self.answer()
        self.receiver.status = 503
        await self.mcp.deliver_once()
        outbox = self.mcp.state['outbox'][0]
        self.assertEqual(outbox['status'], 'pending')
        first_id = outbox['event']['eventId']
        outbox['next_attempt'] = 0
        self.bridge.save()
        restored = MCP(Bridge('device-token', self.bridge.state_path), 'mcp-token', self.receiver)
        self.receiver.status = 200
        await restored.deliver_once()
        self.assertEqual(restored.state['outbox'][0]['event']['eventId'], first_id)
        self.assertEqual(restored.state['outbox'][0]['status'], 'delivered')
        self.assertEqual(restored.state['outbox'][0]['attempts'], 2)

    async def test_filtered_subscription_refresh_unsubscribe_and_expiration(self):
        self.params['arguments'] = {'request_id': 'q1'}
        one = await self.mcp.subscribe(self.params)
        two = await self.mcp.subscribe(self.params)
        self.assertEqual(one['id'], two['id'])
        self.assertEqual(len(self.receiver.messages), 1)  # Verification cache.
        await self.question('q2')
        await self.answer('q2')
        await self.mcp.deliver_once()
        self.assertEqual(self.mcp.state['outbox'], [])
        await self.question()
        await self.answer()
        self.receiver.status = 503
        await self.mcp.deliver_once()
        params = json.loads(json.dumps(self.params))
        del params['delivery']['secret']
        await self.mcp.dispatch('events/unsubscribe', params)
        await self.mcp.dispatch('events/unsubscribe', params)
        self.assertEqual(self.mcp.state['outbox'][0]['status'], 'stopped')
        await self.mcp.subscribe(self.params)
        self.mcp.state['subscriptions'][one['id']]['expires_at'] = time.time() - 1
        self.assertFalse(self.mcp.active(self.mcp.state['subscriptions'][one['id']]))
        self.mcp.state['outbox'][0].update(status='pending', next_attempt=0)
        self.bridge.save()
        restored = MCP(Bridge('device-token', self.bridge.state_path), 'mcp-token', self.receiver)
        count = len(self.receiver.messages)
        await restored.deliver_once()
        self.assertEqual(len(self.receiver.messages), count)
        self.assertEqual(restored.state['outbox'][0]['status'], 'stopped')

    async def test_verification_failure_and_secret_rotation(self):
        self.receiver.verify = False
        reply = await self.mcp.rpc({'jsonrpc': '2.0', 'id': 4, 'method': 'events/subscribe', 'params': self.params})
        self.assertEqual(reply['error']['code'], -32015)
        self.assertEqual(self.mcp.state['subscriptions'], {})
        self.receiver.verify = True
        sub = await self.mcp.subscribe(self.params)
        self.params['delivery']['secret'] = 'whsec_' + base64.b64encode(b'J' * 32).decode()
        await self.mcp.subscribe(self.params)
        stored = self.mcp.state['subscriptions'][sub['id']]
        self.assertEqual(stored['old_secret'], 'whsec_' + base64.b64encode(b'K' * 32).decode())
        self.assertEqual(len(signed_headers(stored, {'eventId': 'e'}, b'{}')['webhook-signature'].split()), 2)
        revoked = MCP(self.bridge, 'rotated-auth-token', self.receiver)
        self.assertFalse(revoked.active(stored))

    async def test_cancellation_and_priority_do_not_interrupt_current(self):
        await self.question('current')
        await self.question('waiting')
        result = await self.call('send_message', {'request_id': 'urgent', 'text': 'Important', 'importance': 'urgent'})
        self.assertFalse(result['isError'])
        self.assertEqual(self.bridge.current()['message']['id'], 'current')
        peer = Peer()
        await self.bridge.attach(peer)
        await self.call('cancel_request', {'request_id': 'current'})
        self.assertEqual(self.bridge.current()['message']['id'], 'urgent')
        self.assertEqual(peer.messages[-2], {'type': 'sync', 'card_id': 'urgent'})
        self.assertEqual(peer.messages[-1]['id'], 'urgent')
        await self.bridge.receive(peer, {'type': 'choice', 'card_id': 'current', 'option_id': 'yes'})
        self.assertEqual(self.bridge.state['events'], [])
        # Reconnecting after offline cancellation receives a clearing sync.
        self.bridge.peer = None
        await self.bridge.cancel('urgent')
        await self.bridge.cancel('waiting')
        await self.bridge.attach(peer)
        self.assertEqual(peer.messages[-1], {'type': 'sync', 'card_id': None})

    async def test_tool_validation_idempotency_and_skill_digest(self):
        self.assertFalse((await self.question())['isError'])
        self.assertFalse((await self.question())['isError'])
        self.assertEqual(len(self.bridge.state['cards']), 1)
        label = 'W' * 64
        result = await self.call('ask_question', {'request_id': 'long-label', 'text': 'Choose',
            'options': [{'id': 'long', 'label': label}]})
        self.assertFalse(result['isError'])
        self.assertEqual(self.bridge.get('long-label')['message']['options'][0]['label'], label)
        self.assertTrue((await self.call('ask_question', {'request_id': 'too-long', 'text': 'Choose',
            'options': [{'id': 'long', 'label': label + 'W'}]}))['isError'])
        self.assertTrue((await self.call('send_message', {'request_id': 'q1', 'text': 'different'}))['isError'])
        self.assertTrue((await self.call('send_message', {'request_id': 'bad', 'text': '☃'}))['isError'])
        self.assertTrue((await self.call('get_device_status', {'unknown': True}))['isError'])
        notification = await self.mcp.rpc({'jsonrpc': '2.0', 'method': 'tools/call', 'params': {
            'name': 'cancel_request', 'arguments': {'request_id': 'q1'}}})
        self.assertIsNone(notification)
        self.assertEqual(self.bridge.get('q1')['status'], 'pending')
        skill = (await self.mcp.dispatch('skills/list', {}))['skills'][0]
        content = (await self.mcp.dispatch('resources/read', {'uri': SKILL_URI}))['contents'][0]['text']
        self.assertEqual(skill['resources'][0]['digest'], 'sha256:' + hashlib.sha256(content.encode()).hexdigest())
        self.assertEqual(skill['frontmatter']['name'], 'rsms-dot')

    async def test_http_auth_and_legacy_and_modern_discovery(self):
        server = await asyncio.start_server(self.mcp.http_client, '127.0.0.1', 0)
        port = server.sockets[0].getsockname()[1]
        async def request(payload, auth='mcp-token', extra=''):
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            body = json.dumps(payload).encode()
            writer.write((f'POST /mcp HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nAuthorization: Bearer {auth}\r\n'
                f'Content-Type: application/json\r\n{extra}Content-Length: {len(body)}\r\n\r\n').encode() + body)
            await writer.drain()
            response = await reader.read()
            writer.close()
            await writer.wait_closed()
            head, body = response.split(b'\r\n\r\n', 1)
            return int(head.split()[1]), json.loads(body) if body else None
        try:
            payload = {'jsonrpc': '2.0', 'id': 1, 'method': 'server/discover'}
            self.assertEqual((await request(payload, 'wrong'))[0], 401)
            self.assertEqual((await request(payload, extra='Origin: https://evil.example\r\n'))[0], 403)
            status, data = await request(payload)
            self.assertEqual(status, 200)
            self.assertIn('2026-07-28', data['result']['supportedVersions'])
            status, data = await request({'jsonrpc': '2.0', 'id': 2, 'method': 'initialize',
                'params': {'protocolVersion': '2025-03-26'}})
            self.assertEqual(data['result']['protocolVersion'], '2025-03-26')
            self.assertEqual((await request({'jsonrpc': '2.0', 'method': 'notifications/initialized'}))[0], 202)
        finally:
            server.close()
            await server.wait_closed()

    async def test_terminal_delivery_errors_are_not_retried(self):
        for status in (410, 413, 401):
            with self.subTest(status=status):
                self.mcp.state['subscriptions'].clear()
                self.mcp.state['outbox'].clear()
                await self.mcp.subscribe(self.params)
                await self.question(str(status))
                await self.answer(str(status))
                self.receiver.status = status
                await self.mcp.deliver_once()
                before = len(self.receiver.messages)
                await self.mcp.deliver_once()
                self.assertEqual(len(self.receiver.messages), before)
                self.assertNotEqual(self.mcp.state['outbox'][0]['status'], 'pending')


class CallbackSecurityTests(unittest.TestCase):
    def test_rejects_non_https_and_credentials(self):
        for url in ('http://example.com', 'https://u:p@example.com', 'https://example.com/#fragment', 'https://example.com:8080'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                callback_url(url)
        for secret in ('bad', 'whsec_bad', 'whsec_' + base64.b64encode(b'short').decode()):
            with self.assertRaises(ValueError):
                signing_key(secret)

    def test_blocks_private_loopback_linklocal_and_mixed_dns(self):
        for host in ('127.0.0.1', '10.0.0.1', '169.254.169.254', '::1', 'fe80::1', '::ffff:127.0.0.1', '224.0.0.1', 'ff02::1'):
            family = socket.AF_INET6 if ':' in host else socket.AF_INET
            addresses = [(family, socket.SOCK_STREAM, 6, '', (host, 443)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443))]
            with patch('pip_mcp.socket.getaddrinfo', return_value=addresses), self.assertRaises(ValueError):
                public_addresses('callback.example')


if __name__ == '__main__':
    unittest.main()
