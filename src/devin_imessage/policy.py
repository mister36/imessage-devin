"""Send-side guardrails: nothing leaves the Mac unless it is explicitly allowed."""

from __future__ import annotations

import re

_DIGITS = re.compile(r"\D")


class PolicyError(PermissionError):
    """Raised when a write is refused by configuration."""


def normalize(address: str) -> str:
    """Canonicalize a handle so `+1 (555) 010-1234` matches `5550101234`."""
    address = address.strip().lower()
    if "@" in address:
        return address
    digits = _DIGITS.sub("", address)
    if len(digits) > 10:
        digits = digits[-10:]
    return digits


class SendPolicy:
    def __init__(self, allow_send: bool, allowlist: list[str]) -> None:
        self._allow_send = allow_send
        self._allowlist = {normalize(a) for a in allowlist}

    @property
    def enabled(self) -> bool:
        return self._allow_send

    def describe(self) -> str:
        if not self._allow_send:
            return "sending disabled (IMESSAGE_ALLOW_SEND is off)"
        if not self._allowlist:
            return "sending enabled for any recipient"
        return f"sending enabled for {len(self._allowlist)} allowlisted recipient(s)"

    def check_enabled(self) -> None:
        if not self._allow_send:
            raise PolicyError(
                "Sending is disabled on this server. Set IMESSAGE_ALLOW_SEND=true to enable it."
            )

    def check(self, addresses: list[str]) -> None:
        self.check_enabled()
        if not self._allowlist:
            return
        blocked = [a for a in addresses if normalize(a) not in self._allowlist]
        if blocked:
            raise PolicyError(
                "Recipient(s) not in IMESSAGE_SEND_ALLOWLIST: " + ", ".join(blocked)
            )


def participants_of(chat: dict) -> list[str]:
    """Extract handle addresses from a BlueBubbles chat payload."""
    return [
        p.get("address", "")
        for p in chat.get("participants", [])
        if p.get("address")
    ]
