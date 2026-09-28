"""HTTP MCP server exposing iMessage (via BlueBubbles) to remote MCP clients."""

from __future__ import annotations

import functools
import hmac
import logging
import os
from typing import Any

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from . import projection
from .client import BlueBubblesClient, BlueBubblesError
from .config import Config
from .policy import PolicyError, SendPolicy, participants_of

logger = logging.getLogger("devin_imessage")

def guarded(fn):
    """Surface anticipated failures to the client instead of a generic crash."""

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except (PolicyError, BlueBubblesError) as exc:
            raise ToolError(str(exc)) from exc

    return wrapper


READ_ONLY = ToolAnnotations(readOnlyHint=True)
WRITES = ToolAnnotations(readOnlyHint=False, destructiveHint=False)


class BearerAuthMiddleware:
    """Reject anything without the shared bearer token.

    The tunnel puts this server on the public internet, so this is the only
    thing standing between the world and the message database.
    """

    def __init__(self, app: ASGIApp, token: str, exempt_paths: tuple[str, ...] = ("/healthz",)):
        self._app = app
        self._token = token
        self._exempt = exempt_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in self._exempt:
            await self._app(scope, receive, send)
            return

        header = Request(scope).headers.get("authorization", "")
        scheme, _, presented = header.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(presented.strip(), self._token):
            response = JSONResponse({"error": "unauthorized"}, status_code=401)
            await response(scope, receive, send)
            return

        await self._app(scope, receive, send)


def build_server(config: Config) -> tuple[MCPServer, BlueBubblesClient]:
    client = BlueBubblesClient(
        config.bluebubbles_url, config.bluebubbles_password, config.request_timeout
    )
    policy = SendPolicy(config.allow_send, config.send_allowlist)
    mcp = MCPServer(
        name="imessage",
        instructions=(
            "Read and search the user's iMessage history and send messages, backed by a "
            "BlueBubbles server on the user's Mac. Chats and messages are identified by "
            "GUIDs: call list_chats or search_messages first to obtain them. "
            f"Write policy: {policy.describe()}."
        ),
    )

    @mcp.tool(annotations=READ_ONLY)
    @guarded
    async def ping() -> dict[str, Any]:
        """Check that the Mac's BlueBubbles server is awake and reachable."""
        await client.ping()
        info = await client.server_info()
        return {
            "ok": True,
            "os_version": info.get("os_version"),
            "server_version": info.get("server_version"),
            "private_api": info.get("private_api"),
            "write_policy": policy.describe(),
        }

    @mcp.tool(annotations=READ_ONLY)
    @guarded
    async def list_chats(limit: int = 25, offset: int = 0) -> list[dict[str, Any]]:
        """List conversations ordered by most recent activity, with their GUIDs."""
        rows = await client.list_chats(limit=limit, offset=offset)
        return [projection.chat(row) for row in rows]

    @mcp.tool(annotations=READ_ONLY)
    @guarded
    async def get_chat_messages(
        chat_guid: str,
        limit: int = 25,
        offset: int = 0,
        after: int | None = None,
        before: int | None = None,
    ) -> list[dict[str, Any]]:
        """Read a conversation's messages, newest first.

        `after`/`before` are Unix epoch milliseconds.
        """
        rows = await client.get_chat_messages(
            chat_guid, limit=limit, offset=offset, after=after, before=before
        )
        return [projection.message(row) for row in rows]

    @mcp.tool(annotations=READ_ONLY)
    @guarded
    async def search_messages(
        query: str | None = None,
        chat_guid: str | None = None,
        limit: int = 25,
        offset: int = 0,
        after: int | None = None,
        before: int | None = None,
    ) -> list[dict[str, Any]]:
        """Search message text across all chats, or within one chat.

        `after`/`before` are Unix epoch milliseconds.
        """
        rows = await client.search_messages(
            query=query,
            chat_guid=chat_guid,
            limit=limit,
            offset=offset,
            after=after,
            before=before,
        )
        return [projection.message(row) for row in rows]

    @mcp.tool(annotations=READ_ONLY)
    @guarded
    async def get_message(message_guid: str) -> dict[str, Any]:
        """Fetch a single message by GUID, including attachment metadata."""
        return projection.message(await client.get_message(message_guid))

    @mcp.tool(annotations=READ_ONLY)
    @guarded
    async def find_contact(name: str, limit: int = 10) -> list[dict[str, Any]]:
        """Look up a contact's phone numbers and emails by (partial) name."""
        needle = name.strip().lower()
        matches = []
        for row in await client.get_contacts():
            projected = projection.contact(row)
            if needle in (projected["name"] or "").lower():
                matches.append(projected)
            if len(matches) >= limit:
                break
        return matches

    @mcp.tool(annotations=READ_ONLY)
    @guarded
    async def lookup_handles(addresses: list[str]) -> list[dict[str, Any]]:
        """Resolve phone numbers or emails to contact names."""
        return [projection.contact(row) for row in await client.query_contacts(addresses)]

    @mcp.tool(annotations=WRITES)
    @guarded
    async def send_message(
        chat_guid: str, message: str, reply_to_guid: str | None = None
    ) -> dict[str, Any]:
        """Send a message to an existing conversation.

        Refused unless the server is configured with IMESSAGE_ALLOW_SEND, and the
        chat's participants are in IMESSAGE_SEND_ALLOWLIST when one is set.
        """
        policy.check_enabled()
        policy.check(participants_of(await client.get_chat(chat_guid)))
        method = "private-api" if reply_to_guid else "apple-script"
        sent = await client.send_message(
            chat_guid, message, method=method, reply_to_guid=reply_to_guid
        )
        return projection.message(sent)

    @mcp.tool(annotations=WRITES)
    @guarded
    async def send_reaction(
        chat_guid: str, message_guid: str, reaction: str
    ) -> dict[str, Any]:
        """React to a message (love, like, dislike, laugh, emphasize, question).

        Requires the BlueBubbles Private API helper, and obeys the same send policy.
        """
        policy.check_enabled()
        policy.check(participants_of(await client.get_chat(chat_guid)))
        await client.send_reaction(chat_guid, message_guid, reaction)
        return {"ok": True}

    @mcp.tool(annotations=WRITES)
    @guarded
    async def mark_chat_read(chat_guid: str) -> dict[str, Any]:
        """Mark a conversation as read."""
        policy.check_enabled()
        policy.check(participants_of(await client.get_chat(chat_guid)))
        await client.mark_chat_read(chat_guid)
        return {"ok": True}

    return mcp, client


def build_app(config: Config) -> Starlette:
    mcp, client = build_server(config)

    allowed_hosts = [h.strip() for h in os.environ.get("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
    transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=bool(allowed_hosts),
        allowed_hosts=allowed_hosts,
        allowed_origins=[f"https://{h}" for h in allowed_hosts],
    )

    app = mcp.streamable_http_app(
        json_response=True,
        stateless_http=True,
        transport_security=transport_security,
        host=config.host,
    )

    async def healthz(_: Request) -> Response:
        return JSONResponse({"ok": True})

    app.router.add_route("/healthz", healthz, methods=["GET"])
    app.state.bluebubbles = client
    app.add_middleware(BearerAuthMiddleware, token=config.auth_token)
    return app


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    config = Config.from_env()
    logger.info(
        "serving MCP on http://%s:%s/mcp against %s (%s)",
        config.host,
        config.port,
        config.bluebubbles_url,
        SendPolicy(config.allow_send, config.send_allowlist).describe(),
    )
    uvicorn.run(build_app(config), host=config.host, port=config.port, log_level="info")


if __name__ == "__main__":
    main()
