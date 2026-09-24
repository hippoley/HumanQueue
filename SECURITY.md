# Security

Human Queue sits on a sensitive boundary: it carries human decisions back into paused software.

The security model is therefore intentionally narrower than “the user clicked Approve.”

## Trust boundaries

### Human Gateway

All `/v1/*` routes are protected by the Gateway bearer token when authentication is enabled.

Treat that token as an administrative credential. A caller holding it can create requests, inspect queue state, and submit decisions through the trusted Gateway API.

Do not expose the Gateway directly to an untrusted network. Prefer localhost, a private LAN, or a private overlay network.

### Human-facing channels

Channel identity is only as strong as the channel that supplies it.

- Slack Socket Mode derives the actor from Slack's authenticated interaction payload.
- Telegram long polling derives the actor from Telegram callback data and verifies the configured chat.
- Generic webhook channels are trusted through their per-channel HMAC secret.
- Direct Gateway API calls are trusted at the Gateway bearer-token boundary.

`route.actors` constrains accepted actor identifiers, but Human Queue does not currently operate an independent per-human identity provider or IAM directory.

A string such as `slack:U123` is meaningful only when it came through the authenticated Slack connector path.

### Resume callbacks

A signed resume callback binds:

- canonical Human Queue `request_id`;
- source;
- source reference;
- human resolution.

The callback signature proves that Human Queue produced the payload when a resume secret is configured.

HTTP 2xx proves transport delivery only.

It does **not** prove that the intended paused action resumed.

For semantic confirmation, the target can return:

```json
{
  "request_id": "attn_...",
  "resumed": true
}
```

Human Queue accepts this as confirmed only when the returned `request_id` exactly matches the request being resumed.

## Fail-closed rules

Human Queue should never convert infrastructure failure into implicit permission.

The following must not mean “approve”:

- Gateway unavailable;
- channel unreachable;
- timeout;
- malformed callback;
- unauthorized responder;
- stale or superseded request;
- ambiguous owner/session;
- resume transport failure;
- unconfirmed HTTP callback.

Native connectors should fall back to the host runtime's own permission path when Human Queue cannot produce a valid decision.

## Secrets

Never put these in issues, logs, screenshots, demo fixtures, or test snapshots:

- Gateway bearer tokens (`hq_...`);
- channel secrets (`hqc_...`);
- Slack bot/app tokens;
- Telegram bot tokens;
- resume webhook secrets;
- GitHub tokens;
- provider API keys;
- full private transcripts.

Human Queue deliberately projects bounded context into external channels. Connector-private fields such as transcript locators should remain inside the trusted local control plane unless explicitly required.

## Supported security claims

Human Queue currently provides:

- Gateway bearer-token protection;
- per-channel signed webhook callbacks;
- duplicate terminal-resolution protection;
- route actor constraints;
- request idempotency and supersession;
- bounded external context projection;
- auditable delivery and resume outcome events;
- exact-request semantic resume receipts for webhook targets that opt in.

Human Queue does **not** currently claim:

- independent end-user authentication;
- enterprise RBAC;
- CODEOWNER/directory-backed responder authorization;
- cryptographic proof that a native agent runtime consumed a returned hook decision;
- security isolation between mutually untrusted users sharing one Gateway process.

## Reporting a vulnerability

Please do not include exploitable secrets or private user data in a public issue.

If GitHub's private security-reporting flow is available for this repository, use it for vulnerabilities that could expose credentials, bypass authorization, approve actions without a valid human decision, or resume the wrong session/action.

For non-sensitive security hardening, open a normal issue with a minimal reproduction and sanitized evidence.

The most useful reports identify the exact boundary that failed:

```text
request created
→ presented (or not)
→ responder authenticated
→ decision accepted
→ resume transported
→ exact request acknowledged
```

A failure at one step should never be silently reported as success at the next.
