import contextlib

import httpx
import pytest
import respx

from devin_imessage.config import Config
from devin_imessage.server import build_app

BASE = "http://127.0.0.1:1234"
TOKEN = "t" * 32

CHAT_GUID = "iMessage;-;+15550101234"
CHAT_PAYLOAD = {
    "status": 200,
    "data": {"guid": CHAT_GUID, "participants": [{"address": "+15550101234"}]},
}


def make_app(**overrides):
    config = Config(
        bluebubbles_url=BASE,
        bluebubbles_password="hunter2",
        auth_token=TOKEN,
        **overrides,
    )
    return build_app(config)


@contextlib.asynccontextmanager
async def running(app):
    """Drive the app's lifespan so the MCP session manager's task group exists."""
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as http:
        yield http


async def call_tool(app, name, arguments, token=TOKEN):
    headers = {
        "content-type": "application/json",
        "accept": "application/json, text/event-stream",
    }
    if token:
        headers["authorization"] = f"Bearer {token}"
    async with running(app) as http:
        return await http.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
        )


@pytest.fixture
def app():
    return make_app()


async def test_requests_without_a_token_are_rejected(app):
    assert (await call_tool(app, "list_chats", {}, token=None)).status_code == 401


async def test_requests_with_a_wrong_token_are_rejected(app):
    assert (await call_tool(app, "list_chats", {}, token="x" * 32)).status_code == 401


async def test_healthz_is_open(app):
    async with running(app) as http:
        assert (await http.get("/healthz")).status_code == 200


@respx.mock
async def test_list_chats_projects_payload(app):
    respx.post(f"{BASE}/api/v1/chat/query").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": 200,
                "data": [
                    {
                        "guid": CHAT_GUID,
                        "displayName": "",
                        "participants": [{"address": "+15550101234"}],
                        "lastMessage": {
                            "guid": "m1",
                            "text": "hey",
                            "isFromMe": False,
                            "dateCreated": 1700000000000,
                            "handle": {"address": "+15550101234"},
                        },
                    }
                ],
            },
        )
    )
    resp = await call_tool(app, "list_chats", {})
    assert resp.status_code == 200
    assert CHAT_GUID in resp.text
    assert "2023-11-14" in resp.text  # dateCreated rendered as ISO
    assert "hey" in resp.text


@respx.mock
async def test_send_is_refused_when_disabled(app):
    respx.get(f"{BASE}/api/v1/chat/{CHAT_GUID}").mock(
        return_value=httpx.Response(200, json=CHAT_PAYLOAD)
    )
    send = respx.post(f"{BASE}/api/v1/message/text")
    resp = await call_tool(app, "send_message", {"chat_guid": CHAT_GUID, "message": "hi"})
    assert "disabled" in resp.text
    assert not send.called


@respx.mock
async def test_send_is_refused_for_unlisted_recipient():
    app = make_app(allow_send=True, send_allowlist=["+15550100000"])
    respx.get(f"{BASE}/api/v1/chat/{CHAT_GUID}").mock(
        return_value=httpx.Response(200, json=CHAT_PAYLOAD)
    )
    send = respx.post(f"{BASE}/api/v1/message/text")
    resp = await call_tool(app, "send_message", {"chat_guid": CHAT_GUID, "message": "hi"})
    assert "IMESSAGE_SEND_ALLOWLIST" in resp.text
    assert not send.called


@respx.mock
async def test_send_succeeds_for_allowlisted_recipient():
    app = make_app(allow_send=True, send_allowlist=["555-010-1234"])
    respx.get(f"{BASE}/api/v1/chat/{CHAT_GUID}").mock(
        return_value=httpx.Response(200, json=CHAT_PAYLOAD)
    )
    send = respx.post(f"{BASE}/api/v1/message/text").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": 200,
                "data": {
                    "guid": "m2",
                    "text": "hi",
                    "isFromMe": True,
                    "dateCreated": 1700000000000,
                },
            },
        )
    )
    resp = await call_tool(app, "send_message", {"chat_guid": CHAT_GUID, "message": "hi"})
    assert send.called
    assert "m2" in resp.text
