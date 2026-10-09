"""Pip MCP tools, skill discovery, and durable signed reply webhooks.

The bridge owns all application state. This adapter exposes no shell, USB, or
provisioning tools. HTTP is loopback-only and uses a separate bearer credential.
"""
import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import hmac
import http.client
import ipaddress
import json
import secrets
import socket
import ssl
import time
from urllib.parse import urlsplit

from bridge import ROOT, private_json

VERSIONS = ["2026-07-28", "2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]
VERSION_META = "io.modelcontextprotocol/protocolVersion"
CLIENT_META = "io.modelcontextprotocol/clientCapabilities"
SERVER_META = "io.modelcontextprotocol/serverInfo"
SKILL = ROOT / "skills/rsms-dot/SKILL.md"
SKILL_URI = "skill://rsms-dot/rsms-dot/SKILL.md"
INSTRUCTIONS = (
    "This is Dot's physical desk display. Use a stable request_id for retries. "
    "Queued does not mean read. Subscribe to device.reply before asking questions; "
    "get_request recovers answers. Dismissal acknowledges a notice, not approval. "
    "Subscribe to device.transcript for voice input; get_voice_input recovers recognized text. "
    "Current display text is printable ASCII, at most 600 characters; options at most 64."
)


def load_mcp_token():
    path = ROOT / ".tools/mcp-config.json"
    if not path.exists():
        private_json(path, {"token": secrets.token_urlsafe(32)})
    return json.loads(path.read_text())["token"]


def iso(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace("+00:00", "Z")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def obj(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


def string(limit, description=""):
    return {"type": "string", "minLength": 1, "maxLength": limit, "description": description}


REQUEST_ID = string(64, "Stable caller-generated ID. Reuse this ID with identical content on retries.")
TEXT = string(600, "Printable ASCII and newlines. Keep short; firmware paginates long text.")
IMPORTANCE = {"type": "string", "enum": ["normal", "urgent"],
    "description": "Urgent advances ahead of waiting normal requests, without interrupting the current card."}
OPTION = obj({"id": string(48), "label": string(64)}, ["id", "label"])


def tool(name, description, schema, read_only=False, destructive=False):
    return {"name": name, "description": description, "inputSchema": schema,
        "annotations": {"readOnlyHint": read_only, "destructiveHint": destructive,
            "idempotentHint": True, "openWorldHint": False}}


TOOLS = [
    tool("send_message", "Queue a notice on pip's desk display. Returns a request, not a read receipt.",
        obj({"request_id": REQUEST_ID, "text": TEXT, "importance": IMPORTANCE}, ["request_id", "text"])),
    tool("ask_question", "Ask Rasmus to choose an option on pip. Subscribe to device.reply first. Returns immediately with a request ID.",
        obj({"request_id": REQUEST_ID, "text": TEXT, "importance": IMPORTANCE,
            "options": {"type": "array", "items": OPTION, "minItems": 1, "maxItems": 3}},
            ["request_id", "text", "options"])),
    tool("get_request", "Read a request's text, pending/answered/cancelled status, and selected option. Use to recover a missed reply.",
        obj({"request_id": REQUEST_ID}, ["request_id"]), True),
    tool("get_voice_input", "Recover a locally transcribed voice input by its recording ID. Speech recognition can be inaccurate; confirm consequential actions.",
        obj({"recording_id": string(64)}, ["recording_id"]), True),
    tool("get_device_status", "Check device connectivity, queue, and reply subscription/delivery health.", obj({}), True),
    tool("cancel_request", "Cancel an obsolete pending request and remove it from the display. Answered requests retain their answer.",
        obj({"request_id": REQUEST_ID}, ["request_id"]), destructive=True),
]
REPLY_SCHEMA = obj({"request_id": REQUEST_ID, "kind": {"type": "string", "enum": ["notice", "decision", "error"]},
    "option_id": string(48), "option_label": string(64)}, ["request_id", "kind", "option_id", "option_label"])
EVENTS = [{"name": "device.reply", "description": "Rasmus selected a decision option or dismissed a message on pip. Use request_id to recover its original context. A notice dismissal is not approval.",
    "delivery": ["webhook"], "inputSchema": obj({"request_id": string(64, "Optional filter for a single request. Omit to monitor all replies.")}),
    "payloadSchema": REPLY_SCHEMA},
    {"name": "device.transcript", "description": "A tap-to-record voice input transcribed locally with Phonon-2. Deduplicate by recording_id. Recognition can be inaccurate.",
     "delivery": ["webhook"], "inputSchema": obj({}),
     "payloadSchema": obj({"recording_id": string(64), "text": string(8192),
         "audio_seconds": {"type": "number"}}, ["recording_id", "text", "audio_seconds"])}]


def validate(value, schema, path="arguments"):
    """Validate the bounded schema subset used by this server, including unknown keys."""
    kind = schema.get("type")
    if kind == "object":
        if not isinstance(value, dict):
            raise ValueError(f"{path} must be an object")
        if set(value) - set(schema["properties"]):
            raise ValueError(f"Unknown fields in {path}")
        if set(schema.get("required", [])) - set(value):
            raise ValueError(f"Missing required fields in {path}")
        for key, item in value.items():
            validate(item, schema["properties"][key], path + "." + key)
    elif kind == "string":
        if not isinstance(value, str) or not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 10000):
            raise ValueError(f"Invalid string length for {path}")
    elif kind == "array":
        if not isinstance(value, list) or not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 100):
            raise ValueError(f"Invalid array length for {path}")
        for item in value:
            validate(item, schema["items"], path)
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"Unsupported value for {path}")


class RpcError(Exception):
    def __init__(self, code, message, data=None):
        self.code, self.message, self.data = code, message, data


def callback_url(url):
    if not isinstance(url, str) or len(url) > 2048:
        raise ValueError("Invalid callback URL")
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
        or parsed.password is not None or parsed.fragment or parsed.port not in (None, 443)
        or any(ord(c) <= 32 or ord(c) > 126 for c in url)):
        raise ValueError("Callback must be a public HTTPS URL on port 443")
    return parsed


def signing_key(secret):
    if not isinstance(secret, str) or not secret.startswith("whsec_"):
        raise ValueError("Expected a whsec_ signing secret")
    key = base64.b64decode(secret[6:], validate=True)
    if not 24 <= len(key) <= 64:
        raise ValueError("Signing key must decode to 24–64 bytes")
    return key


def signed_headers(subscription, payload, body, now=None):
    timestamp = str(int(time.time() if now is None else now))
    event_id = payload.get("eventId") or "verification_" + secrets.token_hex(16)
    signed = event_id.encode() + b"." + timestamp.encode() + b"." + body
    keys = [subscription["secret"]]
    if subscription.get("old_secret_until", 0) > time.time():
        keys.append(subscription["old_secret"])
    signatures = ["v1," + base64.b64encode(hmac.digest(signing_key(key), signed, "sha256")).decode() for key in keys]
    return {"Content-Type": "application/json", "webhook-id": event_id,
        "webhook-timestamp": timestamp, "webhook-signature": " ".join(signatures),
        "X-MCP-Subscription-Id": subscription["id"]}


def public_addresses(host):
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    ips = [ipaddress.ip_address(a[4][0]) for a in addresses]
    if not ips or any(not ip.is_global or ip.is_multicast or ip.is_reserved for ip in ips):
        raise ValueError("Callback resolved to a non-public address")
    return addresses


def post_webhook(url, body, headers):
    """Resolve once and pin the socket; TLS still verifies the original hostname.

    No proxy environment, redirects, or second DNS lookup. Applies equally to
    verification and delivery. Errors never include the callback or signing key.
    """
    parsed = callback_url(url)
    addresses = public_addresses(parsed.hostname)
    context = ssl.create_default_context()
    for family, socktype, proto, _, address in addresses:
        connection = http.client.HTTPSConnection(parsed.hostname, timeout=10, context=context)
        raw = socket.socket(family, socktype, proto)
        raw.settimeout(10)
        try:
            raw.connect(address)
            connection.sock = context.wrap_socket(raw, server_hostname=parsed.hostname)
            connection.request("POST", parsed.path + ("?" + parsed.query if parsed.query else "") or "/", body, headers)
            response = connection.getresponse()
            data = response.read(65537)
            if len(data) > 65536:
                raise ValueError("Callback response is too large")
            return response.status, data
        except OSError:
            if address == addresses[-1][4]:
                raise
        finally:
            connection.close()
            raw.close()
    raise ConnectionError("Callback unavailable")


class MCP:
    def __init__(self, bridge, token, sender=post_webhook):
        self.bridge, self.token, self.sender = bridge, token, sender
        self.owner = hashlib.sha256(token.encode()).hexdigest()
        self.state = bridge.state.setdefault("mcp", {"subscriptions": {}, "outbox": []})
        self.verification_cache = {}
        self.subscription_lock = asyncio.Lock()

    def active(self, subscription):
        return subscription["owner"] == self.owner and subscription["expires_at"] > time.time()

    async def post(self, subscription, payload):
        body = canonical(payload).encode()
        if len(body) > 262144:
            raise ValueError("Event exceeds 256 KiB")
        return await asyncio.wait_for(asyncio.to_thread(self.sender, subscription["url"], body,
            signed_headers(subscription, payload, body)), 12)

    def subscription_identity(self, params):
        schema = next((e for e in EVENTS if e["name"] == params.get("name")), None)
        if schema is None:
            raise ValueError("Unknown event")
        args = params.get("arguments", {})
        validate(args, schema["inputSchema"])
        delivery = params.get("delivery", {})
        if not isinstance(delivery, dict) or delivery.get("mode") != "webhook":
            raise ValueError("Only webhook delivery is supported")
        callback_url(delivery.get("url"))
        identity = canonical([self.owner, delivery["url"], params["name"], args])
        return "sub_" + hashlib.sha256(identity.encode()).hexdigest(), args, delivery

    async def subscribe(self, params):
        sid, args, delivery = self.subscription_identity(params)
        signing_key(delivery.get("secret"))
        if params.get("cursor") is not None:
            raise ValueError("Replay is unsupported; use get_request to recover answers")
        ttl = params.get("ttlMs", 7 * 86400000)
        if ttl is None:
            ttl = 7 * 86400000  # Always grant finite subscriptions.
        if type(ttl) is not int or ttl <= 0:
            raise ValueError("ttlMs must be a positive integer or null")
        ttl = min(max(ttl, 60000), 30 * 86400000)
        async with self.subscription_lock:
            old = self.state["subscriptions"].get(sid)
            subscription = {"id": sid, "owner": self.owner, "name": params["name"], "arguments": args,
                "url": delivery["url"], "secret": delivery["secret"], "expires_at": time.time() + ttl / 1000,
                "last_seq": old["last_seq"] if old and self.active(old) else len(self.bridge.state["events"])}
            if old and old["secret"] != subscription["secret"]:
                subscription.update(old_secret=old["secret"], old_secret_until=time.time() + 300)
            elif old and old.get("old_secret_until", 0) > time.time():
                subscription.update(old_secret=old["old_secret"], old_secret_until=old["old_secret_until"])
            cache_key = (self.owner, subscription["url"], hashlib.sha256(subscription["secret"].encode()).hexdigest())
            if self.verification_cache.get(cache_key, 0) < time.time():
                challenge = secrets.token_urlsafe(32)
                try:
                    status, body = await self.post(subscription, {"type": "verification", "challenge": challenge})
                    response = json.loads(body)
                    answer = response.get("challenge") if isinstance(response, dict) else None
                    if not 200 <= status < 300 or not isinstance(answer, str) or not hmac.compare_digest(answer, challenge):
                        raise ValueError("Challenge mismatch")
                except (ValueError, OSError, TimeoutError, http.client.HTTPException):
                    raise RpcError(-32015, "Callback verification failed", {"reason": "challenge_failed"}) from None
                self.verification_cache = {k: v for k, v in self.verification_cache.items() if v > time.time()}
                self.verification_cache[cache_key] = time.time() + 300
            # The delivery worker can advance the old cursor during verification.
            if old and self.active(old):
                subscription["last_seq"] = old["last_seq"]
            self.state["subscriptions"][sid] = subscription
            self.bridge.save()
            return {"id": sid, "refreshBefore": iso(subscription["expires_at"]), "cursor": None, "truncated": False}

    def enqueue_events(self):
        changed = False
        for subscription in self.state["subscriptions"].values():
            if not self.active(subscription):
                continue
            for event in self.bridge.state["events"]:
                if event["seq"] <= subscription["last_seq"]:
                    continue
                subscription["last_seq"] = event["seq"]
                changed = True
                if subscription["name"] == "device.reply" and event["type"] == "choice":
                    request_id = event["card_id"]
                    if subscription["arguments"].get("request_id", request_id) != request_id:
                        continue
                    record = self.bridge.get(request_id)
                    message = record["message"]
                    option = next(o for o in message["options"] if o["id"] == event["option_id"])
                    data = {"request_id": request_id, "kind": message["kind"],
                        "option_id": option["id"], "option_label": option["label"]}
                elif subscription["name"] == "device.transcript" and event["type"] == "transcript":
                    request_id = event["id"]
                    data = {"recording_id": request_id, "text": event["text"], "audio_seconds": event["audio_seconds"]}
                else:
                    continue
                eid = "evt_" + hashlib.sha256(canonical([request_id, event["seq"], event["timestamp"]]).encode()).hexdigest()
                self.state["outbox"].append({"subscription_id": subscription["id"], "attempts": 0,
                    "next_attempt": 0, "status": "pending", "event": {"eventId": eid, "name": subscription["name"],
                        "timestamp": iso(event["timestamp"]), "cursor": None, "data": data}})
        if changed:
            self.bridge.save()

    async def deliver_once(self):
        self.enqueue_events()
        for item in self.state["outbox"]:
            if item["status"] != "pending":
                continue
            subscription = self.state["subscriptions"].get(item["subscription_id"])
            if subscription is None or not self.active(subscription):
                item["status"] = "stopped"
                self.bridge.save()
                continue
            if item["next_attempt"] > time.time():
                continue
            item["attempts"] += 1
            item["last_attempt_at"] = time.time()
            item.setdefault("first_attempt_at", item["last_attempt_at"])
            item["next_attempt"] = item["last_attempt_at"] + min(3600, 2 ** item["attempts"])
            self.bridge.save()  # A crash may repeat the same event, never invent a new ID.
            started = time.monotonic()
            try:
                status, _ = await self.post(subscription, item["event"])
            except (OSError, ValueError, TimeoutError, http.client.HTTPException):
                status = 0
            item["last_completed_at"] = time.time()
            item["last_duration_ms"] = round((time.monotonic() - started) * 1000, 1)
            item["last_http_status"] = status
            if 200 <= status < 300:
                item["status"] = "delivered"
            elif status == 410:
                subscription["expires_at"] = 0
                item["status"] = "stopped"
            elif status == 413 or (400 <= status < 500 and status not in (408, 429)) or item["attempts"] >= 10:
                item["status"] = "failed"
            self.bridge.save()

    async def deliver_events(self):
        while True:
            await self.deliver_once()
            await asyncio.sleep(0.25)

    async def call_tool(self, name, args):
        definition = next((t for t in TOOLS if t["name"] == name), None)
        if definition is None:
            raise RpcError(-32602, "Unknown tool")
        try:
            validate(args, definition["inputSchema"])
            if name in ("send_message", "ask_question"):
                record = await self.bridge.add({"id": args["request_id"], "body": args["text"],
                    "kind": "decision" if name == "ask_question" else "notice",
                    "importance": args.get("importance", "normal"),
                    **({"options": args["options"]} if name == "ask_question" else {})})
                value = self.request_view(record)
            elif name == "get_request":
                value = self.request_view(self.bridge.get(args["request_id"]))
            elif name == "get_voice_input":
                event = next((e for e in self.bridge.state["events"] if e["type"] == "transcript"
                    and e["id"] == args["recording_id"]), None)
                if event is None:
                    raise ValueError("Unknown recording ID")
                value = {"recording_id": event["id"], "text": event["text"],
                    "audio_seconds": event["audio_seconds"], "created_at": iso(event["timestamp"])}
            elif name == "cancel_request":
                value = self.request_view(await self.bridge.cancel(args["request_id"]))
            else:
                counts = {key: sum(i["status"] == key for i in self.state["outbox"])
                    for key in ("pending", "delivered", "failed", "stopped")}
                value = {**self.bridge.health(), "reply_subscriptions": sum(self.active(s) and s["name"] == "device.reply" for s in self.state["subscriptions"].values()),
                    "voice_subscriptions": sum(self.active(s) and s["name"] == "device.transcript" for s in self.state["subscriptions"].values()),
                    "webhooks": counts, "audio_available": bool(self.bridge.audio and self.bridge.audio.worker.ready),
                    "audio": self.bridge.audio.status() if self.bridge.audio else None}
            return {"content": [{"type": "text", "text": canonical(value)}], "structuredContent": value, "isError": False}
        except (ValueError, TypeError, KeyError):
            return {"content": [{"type": "text", "text": "Invalid request: check the tool schema, display limits, request ID, and duplicate content."}], "isError": True}

    def request_view(self, record):
        message = record["message"]
        return {"request_id": message["id"], "status": record["status"], "text": message["body"],
            "kind": message["kind"], "options": message["options"], "importance": record.get("importance", "normal"),
            "selected_option": next((o for o in message["options"] if o["id"] == record.get("option_id")), None),
            "created_at": iso(record["created_at"]), "device_connected": self.bridge.peer is not None}

    def skill(self):
        text = SKILL.read_text()
        # This skill deliberately has only two single-line YAML frontmatter fields.
        fields = dict(line.split(": ", 1) for line in text.split("---", 2)[1].strip().splitlines())
        return {"uri": SKILL_URI, "frontmatter": fields, "resources": [
            {"uri": SKILL_URI, "digest": "sha256:" + hashlib.sha256(text.encode()).hexdigest()}]}

    async def dispatch(self, method, params):
        capabilities = {"tools": {}, "events": {}, "resources": {},
            "extensions": {"io.modelcontextprotocol/skills": {}}}
        info = {"name": "rsms-dot", "version": "0.1.0"}
        if method == "server/discover":
            return {"resultType": "complete", "supportedVersions": VERSIONS, "_meta": {SERVER_META: info},
                "capabilities": capabilities, "instructions": INSTRUCTIONS, "cacheScope": "private", "ttlMs": 0}
        if method == "initialize":
            version = params.get("protocolVersion")
            return {"protocolVersion": version if version in VERSIONS else VERSIONS[0],
                "serverInfo": info, "capabilities": capabilities, "instructions": INSTRUCTIONS}
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": TOOLS}
        if method == "tools/call":
            return await self.call_tool(params.get("name"), params.get("arguments", {}))
        if method == "events/list":
            return {"events": EVENTS}
        if method == "events/subscribe":
            return await self.subscribe(params)
        if method == "events/unsubscribe":
            sid, _, _ = self.subscription_identity(params)
            async with self.subscription_lock:
                self.state["subscriptions"].pop(sid, None)
                for item in self.state["outbox"]:
                    if item["subscription_id"] == sid and item["status"] == "pending":
                        item["status"] = "stopped"
                self.bridge.save()
            return {}
        if method == "skills/list":
            return {"skills": [self.skill()]}
        if method == "skills/get" and params.get("uri") == SKILL_URI:
            return {"skill": self.skill()}
        if method == "resources/list":
            return {"resources": [{"uri": SKILL_URI, "name": "rsms-dot", "mimeType": "text/markdown"}]}
        if method == "resources/read" and params.get("uri") == SKILL_URI:
            return {"contents": [{"uri": SKILL_URI, "mimeType": "text/markdown", "text": SKILL.read_text()}]}
        raise RpcError(-32601, "Unknown method or resource")

    async def rpc(self, message, headers=None):
        rid = message.get("id") if isinstance(message, dict) else None
        try:
            if (not isinstance(message, dict) or message.get("jsonrpc") != "2.0"
                or not isinstance(message.get("method"), str)
                or not isinstance(message.get("params", {}), dict)
                or (rid is not None and type(rid) not in (str, int))):
                raise RpcError(-32600, "Invalid JSON-RPC request")
            if "id" not in message:
                return None  # Never execute tools or subscribe from a notification.
            params = message.get("params", {})
            meta = params.get("_meta", {})
            if not isinstance(meta, dict):
                raise RpcError(-32602, "Request metadata must be an object")
            modern = VERSION_META in meta
            if modern:
                version = meta[VERSION_META]
                if not isinstance(version, str) or not isinstance(meta.get(CLIENT_META), dict):
                    raise RpcError(-32602, "Missing or invalid protocol version/client capabilities")
                if version != VERSIONS[0]:
                    raise RpcError(-32022, "Unsupported protocol version", {"supported": [VERSIONS[0]], "requested": version})
                if headers is not None and (headers.get("mcp-protocol-version") != version
                    or headers.get("mcp-method") != message["method"]
                    or (message["method"] in ("tools/call", "events/subscribe", "events/unsubscribe")
                        and headers.get("mcp-name") != params.get("name"))):
                    raise RpcError(-32020, "MCP headers do not match the request")
            result = await self.dispatch(message["method"], message.get("params", {}))
            if modern:
                result["resultType"] = "complete"
                result.setdefault("_meta", {})[SERVER_META] = {"name": "rsms-dot", "version": "0.1.0"}
                if message["method"] in ("server/discover", "tools/list", "resources/list", "resources/read"):
                    result.update(cacheScope="private", ttlMs=0)
            return {"jsonrpc": "2.0", "id": rid, "result": result}
        except (ValueError, KeyError, TypeError):
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": "Invalid method parameters"}}
        except RpcError as error:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": error.code, "message": error.message,
                **({"data": error.data} if error.data is not None else {})}}

    async def http_client(self, reader, writer):
        status, body = 200, b""
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
            lines = head.decode("ascii").split("\r\n")
            method, target, version = lines[0].split()
            pairs = [line.split(":", 1) for line in lines[1:] if line]
            headers = {key.lower(): value.strip() for key, value in pairs}
            if len(headers) != len(pairs) or version != "HTTP/1.1" or "transfer-encoding" in headers:
                raise ValueError("Invalid HTTP framing")
            if "origin" in headers or urlsplit("//" + headers.get("host", "")).hostname not in ("localhost", "127.0.0.1"):
                status = 403
            elif not hmac.compare_digest(headers.get("authorization", ""), "Bearer " + self.token):
                status = 401
            elif target != "/mcp":
                status = 404
            elif method != "POST":
                status = 405  # Stateless JSON responses; no GET SSE session.
            elif headers.get("content-type", "").split(";")[0].strip() != "application/json":
                status = 415
            else:
                size = int(headers.get("content-length", "0"))
                if not 0 < size <= 65536:
                    raise ValueError("Invalid request size")
                data = await asyncio.wait_for(reader.readexactly(size), 5)
                try:
                    message = json.loads(data, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                except (ValueError, UnicodeError):
                    result = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
                else:
                    result = await self.rpc(message, headers)
                if result is None:
                    status = 202
                else:
                    body = canonical(result).encode()
        except (ValueError, UnicodeError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError):
            status = 400
        except Exception:
            # Never echo credentials, callback URLs, or raw exceptions to clients.
            status = 500
        extra = 'WWW-Authenticate: Bearer realm="pip"\r\n' if status == 401 else ""
        writer.write(f"HTTP/1.1 {status} Response\r\nContent-Type: application/json\r\n{extra}Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body)
        try:
            await writer.drain()
        except ConnectionError:
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
