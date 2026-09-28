# devin-imessage

An HTTP MCP server that lets cloud Devin sessions read your iMessage history from the Mac you
already own. No dedicated hardware: BlueBubbles talks to Messages.app, this server wraps its REST
API as MCP tools, and one Cloudflare tunnel publishes it behind a bearer token.

```
Your Mac                                              Devin cloud
  Messages.app ── chat.db / AppleScript ──┐
  BlueBubbles server (:1234) ─────────────┤
  this MCP server (127.0.0.1:18800) ──────┴── cloudflared ──► https://<host>/mcp
                                                                    ▲
                                          custom remote MCP server + bearer token secret
```

Reads are always available; writes (send, react, mark-read) are off unless you turn them on, and
can be restricted to an allowlist of recipients.

## 1. Set up BlueBubbles on the Mac

1. Install the server from https://bluebubbles.app and sign in to Messages with your Apple ID.
2. Grant **Full Disk Access** (reads `~/Library/Messages/chat.db`) and, when prompted,
   **Automation → Messages** in System Settings → Privacy & Security.
3. Set a server password and note it. Leave the default port `1234`.
4. Optional: install the Private API helper if you want tapbacks/replies/edits.
5. System Settings → Lock Screen: don't let the Mac sleep, or accept that queries fail while it
   sleeps (the launchd job runs the server under `caffeinate -s`, which only prevents sleep while
   the Mac is on AC power).

## 2. Install this server

```bash
git clone <this repo> ~/devin-imessage && cd ~/devin-imessage
BLUEBUBBLES_PASSWORD='your-bluebubbles-password' ./scripts/install.sh
```

The installer pings BlueBubbles, installs deps with `uv`, installs `cloudflared` via Homebrew,
generates a bearer token into `~/.config/devin-imessage/env` (mode 600), installs two launchd
agents, and prints the public URL plus the token.

Logs: `~/Library/Logs/devin-imessage/`. Restart after editing the env file:

```bash
launchctl kickstart -k "gui/$(id -u)/ai.devin.imessage-mcp"
```

### Stable hostname (recommended)

Without a Cloudflare account the tunnel uses a `trycloudflare.com` URL that changes every restart,
which means re-registering the MCP server in Devin each time. With a free account, create a named
tunnel (Zero Trust → Networks → Tunnels), point a hostname at `http://127.0.0.1:18800`, and rerun:

```bash
CF_TUNNEL_TOKEN='eyJ...' BLUEBUBBLES_PASSWORD='...' ./scripts/install.sh
```

Then set `MCP_ALLOWED_HOSTS=imessage.example.com` in `~/.config/devin-imessage/env` and restart, so
the server only answers requests carrying that Host header.

## 3. Register it with Devin

Add a custom remote MCP server in Devin settings:

- URL: `https://<your-tunnel-host>/mcp` (Streamable HTTP)
- Header: `Authorization: Bearer <token from the installer>` — store the token as a secret

## Tools

| Tool | Access | Purpose |
| --- | --- | --- |
| `ping` | read | BlueBubbles reachability, versions, current write policy |
| `list_chats` | read | Recent conversations with their GUIDs |
| `get_chat_messages` | read | Messages in one conversation |
| `search_messages` | read | Text search, optionally scoped to a chat or date range |
| `get_message` | read | One message by GUID, with attachment metadata |
| `find_contact` | read | Phone numbers/emails for a (partial) name |
| `lookup_handles` | read | Names for phone numbers/emails |
| `send_message` | write | Send to an existing conversation |
| `send_reaction` | write | Tapback (needs the Private API helper) |
| `mark_chat_read` | write | Mark a conversation read |

Responses are projected down to the fields a model needs, so a chat listing costs tens of tokens
per row rather than the full BlueBubbles record.

## Configuration

Read from the environment (the installer writes `~/.config/devin-imessage/env`):

| Variable | Default | Meaning |
| --- | --- | --- |
| `BLUEBUBBLES_PASSWORD` | — | required; BlueBubbles server password |
| `MCP_AUTH_TOKEN` | — | required; bearer token, ≥32 chars (`openssl rand -hex 32`) |
| `BLUEBUBBLES_URL` | `http://127.0.0.1:1234` | where BlueBubbles listens |
| `MCP_HOST` / `MCP_PORT` | `127.0.0.1` / `18800` | bind address; keep it on loopback |
| `BLUEBUBBLES_TIMEOUT` | `30` | per-request timeout, seconds |
| `IMESSAGE_ALLOW_SEND` | `false` | master switch for the write tools |
| `IMESSAGE_SEND_ALLOWLIST` | empty | comma-separated handles; empty means any recipient |
| `MCP_ALLOWED_HOSTS` | empty | allowed `Host` headers (set to your tunnel hostname) |
| `LOG_LEVEL` | `INFO` | |

To enable sending only to two people:

```
IMESSAGE_ALLOW_SEND=true
IMESSAGE_SEND_ALLOWLIST=+15550101234,partner@example.com
```

Numbers are compared on their last 10 digits and emails case-insensitively; in a group chat every
participant must be allowlisted.

## Security model

- Everything is behind one bearer token, compared with `hmac.compare_digest`; only `/healthz` is
  open. The token is the whole boundary — treat it like your message history, and rotate it by
  deleting the env file and rerunning the installer.
- The server binds to loopback, so the tunnel is the only way in.
- Writes are refused by default and refused before any request reaches Messages.
- Nothing is cached or logged beyond request-level uvicorn lines; message contents only travel
  Mac → Cloudflare → Devin session.

## Development

```bash
uv venv && uv pip install -e '.[dev]'
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/python tests/smoke_live.py   # real uvicorn + MCP client against a stub BlueBubbles
```
