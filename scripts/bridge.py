#!/usr/bin/env python3
"""Pip's local card API and persistent device connection (TCP or USB)."""
import argparse
import asyncio
import hmac
import io
import json
import os
from pathlib import Path
import secrets
import time
import uuid
from urllib.parse import urlsplit, parse_qs

ROOT = Path(__file__).resolve().parent.parent
MAX_MESSAGE = 4096


def display_text(value, limit, name):
    if not isinstance(value, str) or not 1 <= len(value) <= limit:
        raise ValueError(f"{name} must contain 1–{limit} characters")
    if any(c != "\n" and not 32 <= ord(c) <= 126 for c in value):
        raise ValueError(f"{name}: the current font supports printable ASCII and newlines")
    return value


def validate_card(data):
    kind = data.get("kind", "notice")
    if kind not in ("notice", "decision"):
        raise ValueError("kind must be notice or decision")
    options = data.get("options", [{"id": "dismiss", "label": "Dismiss"}] if kind == "notice" else [])
    if not isinstance(options, list) or not 1 <= len(options) <= 3:
        raise ValueError("Provide between one and three options")
    options = [{"id": display_text(o.get("id"), 48, "option id"),
        "label": display_text(o.get("label"), 24, "option label")} for o in options]
    if len({o["id"] for o in options}) != len(options):
        raise ValueError("Option IDs must be unique")
    return {"type": "card", "id": display_text(data.get("id", str(uuid.uuid4())), 64, "id"),
        "kind": kind, "title": display_text(data.get("title"), 60, "title"),
        "body": display_text(data.get("body"), 600, "body"), "options": options}


def private_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as output:
        json.dump(value, output, indent=2)
        output.write("\n")
    temporary.replace(path)


class Bridge:
    def __init__(self, token, state_path):
        self.token, self.state_path = token, state_path
        self.state = json.loads(state_path.read_text()) if state_path.exists() else {"cards": [], "events": []}
        self.peer = None

    def save(self):
        private_json(self.state_path, self.state)

    def current(self):
        return next((c for c in self.state["cards"] if c["status"] == "pending"), None)

    async def deliver(self):
        card = self.current()
        if self.peer and card:
            await self.peer.send(card["message"])

    async def add(self, data):
        card = validate_card(data)
        existing = next((c for c in self.state["cards"] if c["message"]["id"] == card["id"]), None)
        if existing:
            if existing["message"] != card:
                raise ValueError("This card ID already has different content")
            return existing
        record = {"message": card, "status": "pending", "created_at": time.time()}
        self.state["cards"].append(record)
        self.save()
        await self.deliver()
        return record

    async def receive(self, peer, message):
        if peer is not self.peer:
            return
        kind = message.get("type")
        if kind == "ping":
            await peer.send({"type": "pong"})
        elif kind == "choice":
            card = next((c for c in self.state["cards"] if c["message"]["id"] == message.get("card_id")), None)
            if not card or message.get("option_id") not in {o["id"] for o in card["message"]["options"]}:
                await peer.send({"type": "error", "message": "Unknown card or option"})
                return
            if card["status"] == "pending":
                if card is not self.current():
                    await peer.send({"type": "error", "message": "Card is not current"})
                    return
                card.update(status="answered", option_id=message["option_id"], answered_at=time.time())
                self.state["events"].append({"seq": len(self.state["events"]) + 1,
                    "type": "choice", "card_id": message["card_id"], "option_id": message["option_id"],
                    "timestamp": time.time()})
                # Persist before ACK. Replayed choices never create a second event.
                self.save()
            await peer.send({"type": "ack", "card_id": card["message"]["id"], "option_id": card["option_id"]})
            await self.deliver()

    async def attach(self, peer):
        if self.peer:
            await self.peer.close()
        self.peer = peer
        await self.deliver()

    async def tcp_client(self, reader, writer):
        peer = TcpPeer(writer)
        try:
            line = await asyncio.wait_for(reader.readline(), 5)
            hello = json.loads(line)
            if hello.get("type") != "hello" or hello.get("version") != 1 or hello.get("device") != "pip" or not hmac.compare_digest(str(hello.get("token", "")), self.token):
                return
            await peer.send({"type": "welcome", "version": 1})
            await self.attach(peer)
            while line := await asyncio.wait_for(reader.readline(), 35):
                await self.receive(peer, json.loads(line))
        except (ValueError, KeyError, TypeError, AttributeError, ConnectionError, asyncio.TimeoutError):
            pass
        finally:
            if self.peer is peer:
                self.peer = None
            await peer.close()

    async def http_client(self, reader, writer):
        status, content_type, body = 200, "application/json", b""
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
            lines = head.decode("ascii").split("\r\n")
            method, target, _ = lines[0].split()
            headers = dict(line.split(":", 1) for line in lines[1:] if line)
            headers = {k.lower(): v.strip() for k, v in headers.items()}
            # This is a local tooling API, not a browser endpoint.
            if ("origin" in headers or "transfer-encoding" in headers
                or urlsplit("//" + headers.get("host", "")).hostname not in ("localhost", "127.0.0.1")):
                raise ValueError("Use a direct localhost tooling request")
            size = int(headers.get("content-length", "0"))
            if not 0 <= size <= MAX_MESSAGE:
                raise ValueError("Request is too large")
            data = await asyncio.wait_for(reader.readexactly(size), 5)
            url = urlsplit(target)
            if method == "GET" and url.path == "/health":
                result = {"device": "pip", "connected": self.peer is not None,
                    "transport": "usb" if isinstance(self.peer, UsbPeer) else "tcp" if self.peer else None,
                    "pending": sum(c["status"] == "pending" for c in self.state["cards"])}
            elif method == "POST" and url.path == "/cards":
                result = await self.add(json.loads(data))
            elif method == "GET" and url.path == "/cards":
                result = self.state["cards"]
            elif method == "GET" and url.path == "/events":
                after = int(parse_qs(url.query).get("after", ["0"])[0])
                result = [e for e in self.state["events"] if e["seq"] > after]
            elif method == "GET" and url.path == "/screenshot.png" and isinstance(self.peer, UsbPeer):
                body = await self.peer.capture()
                content_type, result = "image/png", None
            elif method == "POST" and url.path == "/configure" and isinstance(self.peer, UsbPeer):
                result = await self.peer.configure(json.loads(data))
            else:
                status, result = 404, {"error": "Unknown endpoint"}
            if result is not None:
                body = json.dumps(result).encode()
        except (ValueError, KeyError, TypeError, AttributeError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError) as error:
            status, body = 400, json.dumps({"error": str(error)}).encode()
        except ConnectionError:
            status, body = 503, b'{"error":"Device disconnected; card remains queued"}'
        writer.write(f"HTTP/1.1 {status} Response\r\nContent-Type: {content_type}\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body)
        try:
            await writer.drain()
        except ConnectionError:
            pass
        writer.close()


class TcpPeer:
    def __init__(self, writer):
        self.writer = writer

    async def send(self, message):
        self.writer.write(json.dumps(message, separators=(",", ":")).encode() + b"\n")
        await self.writer.drain()

    async def close(self):
        self.writer.close()


class UsbPeer:
    def __init__(self, port):
        import serial
        self.stream = serial.Serial(port, 115200, timeout=0.2, dsrdtr=True, rtscts=True)
        self.capture_future = None
        self.capture_data = None
        self.configure_future = None

    async def send(self, message):
        data = json.dumps(message, separators=(",", ":")).encode() + b"\n"
        await asyncio.to_thread(self.stream.write, data)

    async def close(self):
        self.stream.close()

    async def capture(self):
        if self.capture_future is not None:
            raise ValueError("A screenshot is already in progress")
        self.capture_future = asyncio.get_running_loop().create_future()
        try:
            await asyncio.to_thread(self.stream.write, b"screenshot\n")
            return await asyncio.wait_for(self.capture_future, 15)
        finally:
            self.capture_future = self.capture_data = None

    async def configure(self, message):
        if self.configure_future is not None:
            raise ValueError("Configuration is already in progress")
        self.configure_future = asyncio.get_running_loop().create_future()
        try:
            await self.send(dict(message, type="configure"))
            return await asyncio.wait_for(self.configure_future, 15)
        finally:
            self.configure_future = None

    async def run(self, bridge):
        await bridge.attach(self)
        pending = bytearray()
        try:
            while self.stream.is_open:
                pending.extend(await asyncio.to_thread(self.stream.read, 4096))
                while b"\n" in pending:
                    line, _, pending = pending.partition(b"\n")
                    if line.startswith(b"PIPEVENT "):
                        message = json.loads(line[9:])
                        if message.get("type") == "configured" and self.configure_future and not self.configure_future.done():
                            self.configure_future.set_result(message)
                        else:
                            await bridge.receive(self, message)
                    elif self.capture_future and not self.capture_future.done():
                        if line.startswith(b"PIPSHOT "):
                            self.capture_data = bytearray()
                        if self.capture_data is not None:
                            self.capture_data.extend(line + b"\n")
                            if len(self.capture_data) > 800000:
                                self.capture_future.set_exception(ValueError("Oversized screenshot"))
                            elif line.strip() == b"PIPEND":
                                from screenshot import read_capture, encode_png
                                try:
                                    metadata, pixels = read_capture(io.BytesIO(self.capture_data))
                                    self.capture_future.set_result(encode_png(metadata["width"], metadata["height"], pixels))
                                except ValueError as error:
                                    self.capture_future.set_exception(error)
                if len(pending) > MAX_MESSAGE:
                    pending.clear()
        except OSError:
            pass
        finally:
            if bridge.peer is self:
                bridge.peer = None
            await self.close()


async def serve(args):
    config_path = ROOT / ".tools/bridge-config.json"
    if not config_path.exists():
        private_json(config_path, {"token": secrets.token_hex(32)})
    bridge = Bridge(json.loads(config_path.read_text())["token"], ROOT / ".tools/bridge-state.json")
    tcp = await asyncio.start_server(bridge.tcp_client, args.bind, args.tcp_port, limit=MAX_MESSAGE)
    http = await asyncio.start_server(bridge.http_client, "127.0.0.1", args.http_port, limit=8192)
    print(f"Pip API: http://127.0.0.1:{args.http_port}; device TCP: {args.bind}:{args.tcp_port}", flush=True)
    tasks = [tcp.serve_forever(), http.serve_forever()]
    if args.serial:
        from serial.tools import list_ports
        ports = [p.device for p in list_ports.comports() if p.vid == 0x303a and p.pid == 0x1001]
        port = args.serial if args.serial != "auto" else ports[0] if len(ports) == 1 else None
        if not port:
            raise ValueError("Expected one USB device; specify --serial /dev/cu.usbmodem…")
        tasks.append(UsbPeer(port).run(bridge))
    async with tcp, http:
        await asyncio.gather(*tasks)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--tcp-port", type=int, default=8787)
    parser.add_argument("--http-port", type=int, default=8788)
    parser.add_argument("--serial", help="Optional USB transport: auto or a serial port")
    try:
        asyncio.run(serve(parser.parse_args()))
    except KeyboardInterrupt:
        pass
