#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required" >&2
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "docker compose v2 is required" >&2
  exit 1
fi

if [ ! -f .env ]; then
  TOKEN="hq_$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(24))
PY
)"
  cat > .env <<EOF
HUMAN_QUEUE_TOKEN=$TOKEN
HUMAN_QUEUE_PORT=7482
EOF
  chmod 600 .env 2>/dev/null || true
  echo "Created .env with a new gateway token."
else
  TOKEN="$(grep '^HUMAN_QUEUE_TOKEN=' .env | cut -d= -f2- || true)"
fi

docker compose up -d --build

echo
echo "human:// gateway is starting"
echo "Dashboard: http://127.0.0.1:${HUMAN_QUEUE_PORT:-7482}"
echo "Gateway token: ${TOKEN:-<see .env>}"
echo
echo "Connect an agent:"
echo "curl http://127.0.0.1:${HUMAN_QUEUE_PORT:-7482}/v1/human \\"
echo "  -H \"Authorization: Bearer ${TOKEN:-<token>}\" \\"
echo "  -H 'Content-Type: application/json' \\"
echo "  -d '{\"uri\":\"human://approve\",\"source\":\"my-agent\",\"ref\":\"run-1\",\"title\":\"Continue?\"}'"
