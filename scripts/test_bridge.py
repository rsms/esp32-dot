import asyncio
import json
from pathlib import Path
import tempfile
import unittest

from bridge import Bridge, validate_card


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.bridge = Bridge("test-token", Path(self.directory.name) / "state.json")
        self.server = await asyncio.start_server(self.bridge.tcp_client, "127.0.0.1", 0, limit=4096)
        self.port = self.server.sockets[0].getsockname()[1]
        self.connections = []

    async def asyncTearDown(self):
        for writer in self.connections:
            writer.close()
            await writer.wait_closed()
        self.server.close()
        await self.server.wait_closed()
        self.directory.cleanup()

    async def connect(self, token="test-token"):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        self.connections.append(writer)
        writer.write(json.dumps({"type": "hello", "version": 1, "device": "pip", "token": token}).encode() + b"\n")
        await writer.drain()
        return reader, writer

    async def message(self, reader):
        while True:
            message = json.loads(await asyncio.wait_for(reader.readline(), 1))
            if message["type"] not in ("sync", "voice_tune"):
                return message

    async def test_queue_reconnect_and_exactly_one_choice_event(self):
        card = {"id": "test", "kind": "decision", "title": "Choose", "body": "Pick one",
            "options": [{"id": "yes", "label": "Yes"}, {"id": "later", "label": "Later"}]}
        await self.bridge.add(card)
        reader, writer = await self.connect()
        self.assertEqual((await self.message(reader))["type"], "welcome")
        self.assertEqual((await self.message(reader))["id"], "test")
        writer.close()
        await writer.wait_closed()
        reader, writer = await self.connect()
        await self.message(reader)
        self.assertEqual((await self.message(reader))["id"], "test")
        reply = b'{"type":"choice","card_id":"test","option_id":"yes"}\n'
        writer.write(reply + reply)
        await writer.drain()
        self.assertEqual((await self.message(reader))["type"], "ack")
        self.assertEqual((await self.message(reader))["type"], "ack")
        self.assertEqual(len(self.bridge.state["events"]), 1)
        restored = Bridge("test-token", self.bridge.state_path)
        self.assertEqual(restored.state["events"][0]["option_id"], "yes")
        self.assertIsNone(restored.current())

    async def test_rejects_wrong_token(self):
        reader, _ = await self.connect("wrong")
        self.assertEqual(await asyncio.wait_for(reader.readline(), 1), b"")

    async def test_duplicate_id_requires_same_content(self):
        card = {"id": "notice", "title": "One", "body": "Text"}
        await self.bridge.add(card)
        await self.bridge.add(card)
        self.assertEqual(len(self.bridge.state["cards"]), 1)
        with self.assertRaises(ValueError):
            await self.bridge.add(dict(card, title="Changed"))

    async def test_interaction_events_and_error_cards(self):
        reader, writer = await self.connect()
        await self.message(reader)
        event = b'{"type":"interaction","id":"sample-1","action":"listen_start"}\n'
        writer.write(event + event + b'{"type":"ping"}\n')
        await writer.drain()
        self.assertEqual((await self.message(reader))["type"], "pong")
        self.assertEqual(len(self.bridge.state["events"]), 1)
        self.assertEqual(self.bridge.state["events"][0]["action"], "listen_start")
        card = await self.bridge.add({"id":"error", "kind":"error", "body":"Error: retry later"})
        self.assertEqual(card["message"]["title"], "")
        self.assertEqual((await self.message(reader))["kind"], "error")
        self.assertEqual(card["message"]["options"], [{"id":"dismiss", "label":"Dismiss"}])

    def test_validates_display_limits(self):
        for card in ({"title": "x" * 61, "body": "x"}, {"title": "x", "body": "snowman ☃"},
            {"kind": "decision", "title": "x", "body": "x", "options": []}):
            with self.subTest(card=card), self.assertRaises(ValueError):
                validate_card(card)


if __name__ == "__main__":
    unittest.main()
