"""Shrink BlueBubbles payloads to the fields a model actually needs.

Raw BlueBubbles rows carry ~60 columns each; returning them verbatim burns the
context window and buries the text.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _iso(ms: Any) -> str | None:
    if not isinstance(ms, (int, float)) or ms <= 0:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def message(row: dict[str, Any]) -> dict[str, Any]:
    handle = row.get("handle") or {}
    out: dict[str, Any] = {
        "guid": row.get("guid"),
        "text": row.get("text"),
        "from_me": bool(row.get("isFromMe")),
        "sender": handle.get("address"),
        "date": _iso(row.get("dateCreated")),
    }
    if row.get("subject"):
        out["subject"] = row["subject"]
    if row.get("dateEdited"):
        out["edited"] = _iso(row["dateEdited"])
    attachments = row.get("attachments") or []
    if attachments:
        out["attachments"] = [
            {
                "guid": a.get("guid"),
                "name": a.get("transferName"),
                "mime_type": a.get("mimeType"),
                "size": a.get("totalBytes"),
            }
            for a in attachments
        ]
    chats = row.get("chats") or []
    if chats:
        out["chat_guid"] = chats[0].get("guid")
    return out


def chat(row: dict[str, Any]) -> dict[str, Any]:
    participants = [
        p.get("address") for p in (row.get("participants") or []) if p.get("address")
    ]
    out: dict[str, Any] = {
        "guid": row.get("guid"),
        "display_name": row.get("displayName") or None,
        "is_group": len(participants) > 1,
        "participants": participants,
        "service": row.get("style") and row.get("chatIdentifier"),
    }
    out.pop("service", None)
    last = row.get("lastMessage")
    if last:
        out["last_message"] = message(last)
    return out


def contact(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": " ".join(
            part for part in [row.get("firstName"), row.get("lastName")] if part
        )
        or row.get("displayName"),
        "phone_numbers": [p.get("address") for p in (row.get("phoneNumbers") or [])],
        "emails": [e.get("address") for e in (row.get("emails") or [])],
    }
