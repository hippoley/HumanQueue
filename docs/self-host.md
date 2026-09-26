# Self-hosting human://

human:// is designed to run as a **personal or team Human Gateway**. Your queue, audit trail, gateway token and SQLite state can stay on your own machine or server.

## Fastest local install

macOS / Linux / WSL:

```bash
curl -fsSL https://raw.githubusercontent.com/hippoley/HumanQueue/main/scripts/install.sh | bash
humanq gateway run
```

Then:

```bash
humanq dashboard
humanq doctor
humanq gateway status
```

The first `humanq onboard` creates:

```text
~/.human-queue/
├── config.json        # bind address, port, gateway token
└── human-queue.db     # requests, votes, audit events, budgets
```

The dashboard is local by default at `http://127.0.0.1:7482`.

## Docker

From a checkout:

```bash
bash scripts/docker/setup.sh
```

The setup script creates a private `.env` with an `hq_...` gateway token, builds the image, starts the gateway and prints a ready-to-run curl example.

Manual Compose is also supported:

```bash
cp .env.example .env
# replace HUMAN_QUEUE_TOKEN before exposing the service
docker compose up -d --build
```

## Connect a machine

Every caller uses the same small protocol surface:

```bash
curl http://127.0.0.1:7482/v1/human \
  -H "Authorization: Bearer hq_xxx" \
  -H "Content-Type: application/json" \
  -d '{
    "uri":"human://approve",
    "source":"my-agent",
    "ref":"run-42",
    "title":"Deploy to production?",
    "risk":0.85,
    "downstream":4,
    "seconds":8
  }'
```

The request appears in your dashboard. When you decide, the machine can either poll the request or receive a signed resume webhook.

## Python

```python
from humanqueue import HumanBoundary

human = HumanBoundary(
    base_url="http://127.0.0.1:7482",
    token="hq_xxx",
)

decision = human.ask(
    "human://approve",
    source="my-agent",
    ref="run-42",
    title="Deploy to production?",
    wait=True,
)
```

When the SDK runs on the same user account as the gateway, it can read the local gateway token automatically.

## Remote exposure

The default bind is loopback on purpose. If you expose human:// beyond the host:

1. keep gateway-token authentication enabled;
2. terminate TLS in front of the gateway;
3. use a private network, VPN, reverse proxy or firewall allow-list where possible;
4. rotate the token after accidental disclosure with `humanq token rotate`;
5. do not put gateway tokens in public browser JavaScript or repository files.

Example:

```bash
humanq onboard --host 0.0.0.0 --port 7482
humanq gateway run
```

Put Caddy, nginx, Tailscale, Cloudflare Tunnel or another TLS/private-network layer in front of it according to your environment.

## Mental model

```text
your agents / CI / MCP / n8n
              │
              │  Bearer hq_...
              ▼
       your Human Gateway
       127.0.0.1:7482
          │       │
          │       └── SQLite + audit history
          ▼
      Control UI
          │
          ▼
      human decision
          │
          ▼
       resume source
```

Hosted human:// can exist later, but it is not required by the protocol or runtime.
