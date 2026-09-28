import httpx
import pytest
import respx

from devin_imessage.client import BlueBubblesClient, BlueBubblesError

BASE = "http://127.0.0.1:1234"


@pytest.fixture
async def client():
    c = BlueBubblesClient(BASE, "hunter2")
    yield c
    await c.aclose()


@respx.mock
async def test_password_is_sent_as_query_param(client):
    route = respx.get(f"{BASE}/api/v1/ping").mock(
        return_value=httpx.Response(200, json={"status": 200, "data": "pong"})
    )
    assert await client.ping() == "pong"
    assert route.calls.last.request.url.params["password"] == "hunter2"


@respx.mock
async def test_search_builds_like_clause(client):
    route = respx.post(f"{BASE}/api/v1/message/query").mock(
        return_value=httpx.Response(200, json={"status": 200, "data": []})
    )
    await client.search_messages(query="dinner")
    body = route.calls.last.request.read().decode()
    assert "message.text LIKE :query" in body
    assert "%dinner%" in body


@respx.mock
async def test_api_level_error_raises(client):
    respx.get(f"{BASE}/api/v1/ping").mock(
        return_value=httpx.Response(200, json={"status": 401, "message": "bad password"})
    )
    with pytest.raises(BlueBubblesError, match="bad password"):
        await client.ping()


@respx.mock
async def test_http_error_raises(client):
    respx.get(f"{BASE}/api/v1/ping").mock(return_value=httpx.Response(500, json={}))
    with pytest.raises(BlueBubblesError):
        await client.ping()


@respx.mock
async def test_sleeping_mac_reports_a_readable_error(client):
    respx.get(f"{BASE}/api/v1/ping").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(BlueBubblesError, match="Cannot reach BlueBubbles"):
        await client.ping()


@respx.mock
async def test_non_json_response_raises(client):
    respx.get(f"{BASE}/api/v1/ping").mock(return_value=httpx.Response(502, text="<html>"))
    with pytest.raises(BlueBubblesError, match="Non-JSON"):
        await client.ping()


@respx.mock
async def test_none_valued_params_are_dropped(client):
    route = respx.get(f"{BASE}/api/v1/chat/x/message").mock(
        return_value=httpx.Response(200, json={"status": 200, "data": []})
    )
    await client.get_chat_messages("x", after=None)
    assert "after" not in route.calls.last.request.url.params
