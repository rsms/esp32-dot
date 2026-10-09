#!/usr/bin/env python3
"""Check the running bridge with the official Python MCP SDK (test dependency)."""
import asyncio
from pathlib import Path
import httpx2
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.client.streamable_http import streamable_http_client
from pip_mcp import load_mcp_token

ROOT = Path(__file__).resolve().parent.parent


async def main():
    params = StdioServerParameters(command=str(ROOT / '.tools/python/bin/python'),
        args=[str(ROOT / 'scripts/mcp-stdio.py')])
    async def check(server, mode, transport):
        async with Client(server, mode=mode, read_timeout_seconds=15) as client:
            tools = await client.list_tools()
            assert {tool.name for tool in tools.tools} == {
                'send_message', 'ask_question', 'get_request', 'get_device_status', 'cancel_request', 'get_voice_input', 'list_voice_recordings', 'get_voice_recording'}
            result = await client.call_tool('get_device_status', {})
            assert not result.is_error, result
            assert result.structured_content['device'] == 'pip', result
            resources = await client.list_resources()
            assert len(resources.resources) == 1
            resource = await client.read_resource(str(resources.resources[0].uri))
            assert 'name: rsms-dot' in resource.contents[0].text
            print(transport, mode, 'PASS', result.structured_content)
    for mode in ('auto', 'legacy'):
        await check(params, mode, 'stdio')
        async with httpx2.AsyncClient(headers={'Authorization': 'Bearer ' + load_mcp_token()}, trust_env=False) as http:
            await check(streamable_http_client('http://127.0.0.1:8789/mcp', http_client=http), mode, 'HTTP')


if __name__ == '__main__':
    asyncio.run(main())
