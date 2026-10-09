#!/usr/bin/env python3
"""Forward MCP stdio to the running bridge; used by Codex and Secure MCP Tunnel."""
import json
import sys
import urllib.error
import urllib.request
from pip_mcp import VERSION_META, load_mcp_token


def main():
    token = load_mcp_token()
    # No proxy environment for this strictly local hop.
    client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for line in sys.stdin.buffer:
        message = None
        if len(line) > 65536:
            continue
        try:
            message = json.loads(line)
            meta = message.get("params", {}).get("_meta", {}) if isinstance(message, dict) else {}
            headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                "Authorization": "Bearer " + token}
            if VERSION_META in meta:
                headers.update({"MCP-Protocol-Version": meta[VERSION_META], "Mcp-Method": message["method"]})
                if "name" in message.get("params", {}):
                    headers["Mcp-Name"] = message["params"]["name"]
            request = urllib.request.Request("http://127.0.0.1:8789/mcp", line,
                headers)
            with client.open(request, timeout=30) as response:
                # A bounded 30-second mono WAV is ~1.28 MB after base64.
                result = response.read(2 * 1024 * 1024 + 1)
                if len(result) > 2 * 1024 * 1024:
                    raise ValueError("Oversized MCP response")
            if result:
                sys.stdout.buffer.write(result + b"\n")
                sys.stdout.buffer.flush()
        except (ValueError, TypeError, AttributeError, OSError, urllib.error.URLError):
            if isinstance(locals().get("message"), dict) and "id" in message:
                print(json.dumps({"jsonrpc": "2.0", "id": message["id"],
                    "error": {"code": -32603, "message": "Pip bridge unavailable; start scripts/bridge.py"}}), flush=True)


if __name__ == "__main__":
    main()
