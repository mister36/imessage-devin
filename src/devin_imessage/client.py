"""Minimal async client for the BlueBubbles v1 REST API."""

from __future__ import annotations

import uuid
from typing import Any

import httpx


class BlueBubblesError(RuntimeError):
    def __init__(self, message: str, body: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.body = body


class BlueBubblesClient:
    """Wraps the endpoints this MCP server exposes.

    BlueBubbles authenticates every request with a `password` query parameter.
    """

    def __init__(self, base_url: str, password: str, timeout: float = 30.0) -> None:
        self._base = base_url.rstrip("/")
        self._password = password
        self._http = httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        await self._http.aclose()

    def _params(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"password": self._password}
        if extra:
            params.update({k: v for k, v in extra.items() if v is not None})
        return params

    @staticmethod
    def _unwrap(resp: httpx.Response) -> Any:
        try:
            body = resp.json()
        except ValueError:
            raise BlueBubblesError(
                f"Non-JSON response from BlueBubbles (HTTP {resp.status_code})"
            ) from None
        if resp.status_code >= 400 or (body.get("status") or 0) >= 400:
            raise BlueBubblesError(body.get("message") or f"HTTP {resp.status_code}", body)
        return body.get("data")

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        try:
            resp = await self._http.get(self._url(path), params=self._params(params))
        except httpx.RequestError as exc:
            raise BlueBubblesError(self._unreachable(exc)) from exc
        return self._unwrap(resp)

    async def _post(self, path: str, json: dict[str, Any] | None = None) -> Any:
        try:
            resp = await self._http.post(self._url(path), json=json, params=self._params())
        except httpx.RequestError as exc:
            raise BlueBubblesError(self._unreachable(exc)) from exc
        return self._unwrap(resp)

    def _unreachable(self, exc: httpx.RequestError) -> str:
        return (
            f"Cannot reach BlueBubbles at {self._base} ({exc.__class__.__name__}: {exc}). "
            "The Mac may be asleep or the server stopped."
        )

    def _url(self, path: str) -> str:
        return f"{self._base}/api/v1{path}"

    # -- reads ---------------------------------------------------------------

    async def ping(self) -> Any:
        return await self._get("/ping")

    async def server_info(self) -> Any:
        return await self._get("/server/info")

    async def list_chats(self, limit: int = 25, offset: int = 0) -> list[dict[str, Any]]:
        return await self._post(
            "/chat/query",
            {
                "limit": limit,
                "offset": offset,
                "sort": "lastmessage",
                "with": ["lastMessage", "participants"],
            },
        )

    async def get_chat(self, chat_guid: str) -> dict[str, Any]:
        return await self._get(f"/chat/{chat_guid}", {"with": "participants"})

    async def get_chat_messages(
        self,
        chat_guid: str,
        limit: int = 25,
        offset: int = 0,
        after: int | None = None,
        before: int | None = None,
    ) -> list[dict[str, Any]]:
        return await self._get(
            f"/chat/{chat_guid}/message",
            {
                "limit": limit,
                "offset": offset,
                "sort": "DESC",
                "with": "attachment",
                "after": after,
                "before": before,
            },
        )

    async def search_messages(
        self,
        query: str | None = None,
        chat_guid: str | None = None,
        limit: int = 25,
        offset: int = 0,
        after: int | None = None,
        before: int | None = None,
    ) -> list[dict[str, Any]]:
        body: dict[str, Any] = {
            "limit": limit,
            "offset": offset,
            "sort": "DESC",
            "with": ["chats", "attachment"],
        }
        if chat_guid:
            body["chatGuid"] = chat_guid
        if after:
            body["after"] = after
        if before:
            body["before"] = before
        if query:
            body["where"] = [
                {"statement": "message.text LIKE :query", "args": {"query": f"%{query}%"}}
            ]
        return await self._post("/message/query", body)

    async def get_message(self, message_guid: str) -> dict[str, Any]:
        return await self._get(f"/message/{message_guid}", {"with": "chats,attachments"})

    async def get_contacts(self) -> list[dict[str, Any]]:
        return await self._get("/contact")

    async def query_contacts(self, addresses: list[str]) -> list[dict[str, Any]]:
        return await self._post("/contact/query", {"addresses": addresses})

    # -- writes --------------------------------------------------------------

    async def send_message(
        self,
        chat_guid: str,
        message: str,
        method: str = "apple-script",
        reply_to_guid: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "chatGuid": chat_guid,
            "tempGuid": f"temp-{uuid.uuid4().hex}",
            "message": message,
            "method": method,
        }
        if reply_to_guid:
            body["selectedMessageGuid"] = reply_to_guid
        return await self._post("/message/text", body)

    async def send_reaction(
        self, chat_guid: str, message_guid: str, reaction: str, part_index: int = 0
    ) -> Any:
        return await self._post(
            "/message/react",
            {
                "chatGuid": chat_guid,
                "selectedMessageGuid": message_guid,
                "reaction": reaction,
                "partIndex": part_index,
            },
        )

    async def mark_chat_read(self, chat_guid: str) -> Any:
        return await self._post(f"/chat/{chat_guid}/read")
