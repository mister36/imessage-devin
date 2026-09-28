#!/usr/bin/env bash
# Install the iMessage MCP server + Cloudflare tunnel as launchd agents on a Mac.
#
#   BLUEBUBBLES_PASSWORD=... ./scripts/install.sh
#
# Optional:
#   BLUEBUBBLES_URL=http://127.0.0.1:1234   where the BlueBubbles server listens
#   CF_TUNNEL_TOKEN=...                     use a named Cloudflare tunnel (stable hostname)
#                                           instead of an ephemeral trycloudflare URL
#   MCP_PORT=18800
set -euo pipefail
umask 077  # the env file and the tunnel plist hold secrets

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_DIR="${HOME}/.config/devin-imessage"
LOG_DIR="${HOME}/Library/Logs/devin-imessage"
AGENT_DIR="${HOME}/Library/LaunchAgents"
MCP_LABEL="ai.devin.imessage-mcp"
TUNNEL_LABEL="ai.devin.imessage-tunnel"

BLUEBUBBLES_URL="${BLUEBUBBLES_URL:-http://127.0.0.1:1234}"
MCP_PORT="${MCP_PORT:-18800}"

die() { echo "error: $*" >&2; exit 1; }

[[ "$(uname -s)" == "Darwin" ]] || die "this installer only runs on macOS (the Mac hosting Messages.app)"
[[ -n "${BLUEBUBBLES_PASSWORD:-}" ]] || die "set BLUEBUBBLES_PASSWORD to your BlueBubbles server password"

echo "==> Checking BlueBubbles at ${BLUEBUBBLES_URL}"
ping_body="$(curl -fsS --max-time 10 "${BLUEBUBBLES_URL}/api/v1/ping?password=${BLUEBUBBLES_PASSWORD}" || true)"
case "${ping_body}" in
  *pong*) echo "    ok" ;;
  "")     die "no response — is BlueBubbles.app running and its server started?" ;;
  *)      die "unexpected reply (wrong password?): ${ping_body}" ;;
esac

echo "==> Installing dependencies"
command -v uv >/dev/null 2>&1 || curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="${HOME}/.local/bin:${PATH}"
command -v cloudflared >/dev/null 2>&1 || {
  command -v brew >/dev/null 2>&1 || die "install Homebrew (or cloudflared) first: https://brew.sh"
  brew install cloudflared
}

uv venv --directory "${REPO_DIR}" --quiet
uv pip install --directory "${REPO_DIR}" --quiet -e "${REPO_DIR}"
PYTHON_BIN="${REPO_DIR}/.venv/bin/python"

mkdir -p "${CONFIG_DIR}" "${LOG_DIR}" "${AGENT_DIR}"

ENV_FILE="${CONFIG_DIR}/env"
if [[ -f "${ENV_FILE}" ]]; then
  echo "==> Keeping existing ${ENV_FILE} (delete it to regenerate the token)"
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
else
  echo "==> Writing ${ENV_FILE}"
  MCP_AUTH_TOKEN="$(openssl rand -hex 32)"
  cat >"${ENV_FILE}" <<EOF
BLUEBUBBLES_URL=${BLUEBUBBLES_URL}
BLUEBUBBLES_PASSWORD=${BLUEBUBBLES_PASSWORD}
MCP_AUTH_TOKEN=${MCP_AUTH_TOKEN}
MCP_HOST=127.0.0.1
MCP_PORT=${MCP_PORT}
# Writes are off until you opt in. Leave the allowlist empty to permit any recipient.
IMESSAGE_ALLOW_SEND=false
IMESSAGE_SEND_ALLOWLIST=
# Set once you know the tunnel hostname, to enable DNS-rebinding protection.
MCP_ALLOWED_HOSTS=
EOF
fi

render() {
  sed -e "s|@@LABEL@@|$1|g" \
      -e "s|@@PYTHON@@|${PYTHON_BIN}|g" \
      -e "s|@@ENV_FILE@@|${ENV_FILE}|g" \
      -e "s|@@LOG_DIR@@|${LOG_DIR}|g" \
      -e "s|@@PORT@@|${MCP_PORT}|g" \
      -e "s|@@TUNNEL_ARGS_SHELL@@|$2|g" \
      "$3"
}

if [[ -n "${CF_TUNNEL_TOKEN:-}" ]]; then
  tunnel_args="run --token ${CF_TUNNEL_TOKEN}"
else
  tunnel_args="--url http://127.0.0.1:${MCP_PORT}"
fi

echo "==> Installing launchd agents"
render "${MCP_LABEL}" "" "${REPO_DIR}/scripts/launchd/mcp.plist.tmpl" >"${AGENT_DIR}/${MCP_LABEL}.plist"
render "${TUNNEL_LABEL}" "${tunnel_args}" "${REPO_DIR}/scripts/launchd/tunnel.plist.tmpl" >"${AGENT_DIR}/${TUNNEL_LABEL}.plist"
chmod 600 "${ENV_FILE}" "${AGENT_DIR}/${TUNNEL_LABEL}.plist"

for label in "${MCP_LABEL}" "${TUNNEL_LABEL}"; do
  launchctl bootout "gui/$(id -u)/${label}" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "${AGENT_DIR}/${label}.plist"
done

echo "==> Waiting for the server"
for _ in $(seq 1 20); do
  if curl -fsS --max-time 2 "http://127.0.0.1:${MCP_PORT}/healthz" >/dev/null 2>&1; then
    healthy=1; break
  fi
  sleep 1
done
[[ -n "${healthy:-}" ]] || die "server did not come up; see ${LOG_DIR}/${MCP_LABEL}.err.log"

echo
echo "MCP server:  http://127.0.0.1:${MCP_PORT}/mcp"
echo "Bearer token: ${MCP_AUTH_TOKEN:-<in ${ENV_FILE}>}"
if [[ -z "${CF_TUNNEL_TOKEN:-}" ]]; then
  echo
  echo "Public URL (ephemeral, changes on restart) — from ${LOG_DIR}/${TUNNEL_LABEL}.err.log:"
  sleep 5
  grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "${LOG_DIR}/${TUNNEL_LABEL}.err.log" | tail -1 || \
    echo "  (not printed yet; check the log in a few seconds)"
fi
echo
echo "Next: add MCP_ALLOWED_HOSTS=<tunnel hostname> to ${ENV_FILE},"
echo "then: launchctl kickstart -k gui/$(id -u)/${MCP_LABEL}"
