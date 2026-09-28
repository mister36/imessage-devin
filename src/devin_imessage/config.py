"""Runtime configuration, loaded from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _csv(name: str) -> list[str]:
    raw = os.environ.get(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    bluebubbles_url: str
    bluebubbles_password: str
    auth_token: str
    host: str = "127.0.0.1"
    port: int = 18800
    request_timeout: float = 30.0
    allow_send: bool = False
    send_allowlist: list[str] = field(default_factory=list)

    @classmethod
    def from_env(cls) -> Config:
        password = os.environ.get("BLUEBUBBLES_PASSWORD", "")
        if not password:
            raise ConfigError("BLUEBUBBLES_PASSWORD is required")

        token = os.environ.get("MCP_AUTH_TOKEN", "")
        if len(token) < 32:
            raise ConfigError(
                "MCP_AUTH_TOKEN is required and must be at least 32 characters; "
                "generate one with `openssl rand -hex 32`"
            )

        return cls(
            bluebubbles_url=os.environ.get("BLUEBUBBLES_URL", "http://127.0.0.1:1234"),
            bluebubbles_password=password,
            auth_token=token,
            host=os.environ.get("MCP_HOST", "127.0.0.1"),
            port=int(os.environ.get("MCP_PORT", "18800")),
            request_timeout=float(os.environ.get("BLUEBUBBLES_TIMEOUT", "30")),
            allow_send=_bool("IMESSAGE_ALLOW_SEND"),
            send_allowlist=_csv("IMESSAGE_SEND_ALLOWLIST"),
        )
