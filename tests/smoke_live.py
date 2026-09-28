"""End-to-end smoke test: real uvicorn + real MCP client against a stub BlueBubbles.

    python tests/smoke_live.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

TOKEN = "s" * 32
STUB_PORT = 14321
MCP_PORT = 14322


async def _ping(_):
    return JSONResponse({"status": 200, "data": "pong"})


async def _info(_):
    return JSONResponse({"status": 200, "data": {"os_version": "14.5", "server_version": "1.9.9"}})


async def _chats(_):
    return JSONResponse(
        {
            "status": 200,
            "data": [
                {
                    "guid": "iMessage;-;+15550101234",
                    "displayName": "",
                    "participants": [{"address": "+15550101234"}],
                    "lastMessage": {
                        "guid": "m1",
                        "text": "dinner at 7?",
                        "isFromMe": False,
                        "dateCreated": 1700000000000,
                        "handle": {"address": "+15550101234"},
                    },
                }
            ],
        }
    )


stub = Starlette(
    routes=[
        Route("/api/v1/ping", _ping),
        Route("/api/v1/server/info", _info),
        Route("/api/v1/chat/query", _chats, methods=["POST"]),
    ]
)


def serve(app, port):
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    return server


async def main() -> int:
    os.environ.update(
        BLUEBUBBLES_URL=f"http://127.0.0.1:{STUB_PORT}",
        BLUEBUBBLES_PASSWORD="hunter2",
        MCP_AUTH_TOKEN=TOKEN,
        MCP_PORT=str(MCP_PORT),
    )
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    from devin_imessage.config import Config
    from devin_imessage.server import build_app

    serve(stub, STUB_PORT)
    serve(build_app(Config.from_env()), MCP_PORT)
    await asyncio.sleep(2)

    url = f"http://127.0.0.1:{MCP_PORT}/mcp"

    async with httpx.AsyncClient() as probe:
        unauthorized = await probe.post(
            url, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        )
    assert unauthorized.status_code == 401, unauthorized.status_code
    print("unauthenticated request rejected:", unauthorized.status_code)

    http_client = httpx.AsyncClient(headers={"Authorization": f"Bearer {TOKEN}"})
    async with http_client, streamable_http_client(url, http_client=http_client) as streams:
        read, write = streams[0], streams[1]
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("tools:", ", ".join(t.name for t in tools.tools))

            pong = await session.call_tool("ping", {})
            print("ping:", pong.content[0].text)

            chats = await session.call_tool("list_chats", {})
            print("list_chats:", chats.content[0].text)

            blocked = await session.call_tool(
                "send_message", {"chat_guid": "iMessage;-;+15550101234", "message": "hi"}
            )
            print("send_message (expected refusal):", blocked.content[0].text)
            assert blocked.is_error
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
