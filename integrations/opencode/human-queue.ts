import { Plugin } from "@opencode/plugin"

const gatewayURL = () => (process.env.HUMAN_QUEUE_URL || "http://127.0.0.1:7482").replace(/\/$/, "")

async function gatewayToken(): Promise<string> {
  if (process.env.HUMAN_QUEUE_TOKEN) return process.env.HUMAN_QUEUE_TOKEN
  const home = process.env.HOME || process.env.USERPROFILE || ""
  if (!home) return ""
  try {
    const cfg = await Bun.file(home + "/.human-queue/config.json").json()
    return String(cfg.token || "")
  } catch {
    return ""
  }
}

async function hq(path: string, init: RequestInit = {}) {
  const token = await gatewayToken()
  const headers = new Headers(init.headers || {})
  if (token) headers.set("authorization", "Bearer " + token)
  if (!headers.has("content-type") && init.body) headers.set("content-type", "application/json")
  return fetch(gatewayURL() + path, { ...init, headers })
}

function textOf(value: unknown): string {
  if (typeof value === "string") return value
  if (!value) return ""
  try {
    const json = JSON.stringify(value)
    return json.length > 1200 ? json.slice(0, 1200) + "…" : json
  } catch {
    return String(value)
  }
}

async function recordSession(input: {
  event_name: string
  session_id: string
  user?: string
  assistant?: string
  action?: string
  resources?: readonly string[]
  message_id?: string
}) {
  try {
    await hq("/v1/connectors/events", {
      method: "POST",
      body: JSON.stringify({
        provider: "opencode",
        event_name: input.event_name,
        session_id: input.session_id,
        cwd: process.cwd(),
        latest_user_prompt: input.user || null,
        latest_assistant_message: input.assistant || null,
        tool_name: input.action || null,
        tool_use_id: input.message_id || null,
        tool_input: input.resources ? { resources: input.resources } : null,
        metadata: { runtime: "opencode-v2-plugin" },
      }),
    })
  } catch {
    // Observability must never break OpenCode.
  }
}

async function waitForDecision(requestID: string) {
  while (true) {
    const response = await hq("/v1/requests/" + encodeURIComponent(requestID))
    if (!response.ok) throw new Error("human:// returned " + response.status)
    const body = await response.json()
    const request = body.request
    if (request.status === "resolved") return request.resolution || {}
    if (["cancelled", "expired", "superseded"].includes(request.status)) {
      throw new Error("human:// request ended with " + request.status)
    }
    await Bun.sleep(800)
  }
}

export default Plugin.define({
  id: "human-queue",

  async setup(ctx) {
    await ctx.session.hook("prompt", async (event) => {
      await recordSession({
        event_name: "UserPromptSubmit",
        session_id: event.sessionID,
        user: event.prompt.text,
        message_id: event.messageID,
      })
    })

    await ctx.permission.hook("evaluate", async (event) => {
      // Explicit deny never reaches this hook. Preserve configured auto-allows;
      // route only decisions that OpenCode would otherwise ask a person.
      if (event.effect !== "ask") return

      let context: unknown[] = []
      try {
        context = Array.from(await ctx.session.context({ sessionID: event.sessionID })).slice(-6)
      } catch {
        context = []
      }

      await recordSession({
        event_name: "PermissionRequest",
        session_id: event.sessionID,
        action: event.action,
        resources: event.resources,
        message_id: event.source?.id,
      })

      const ref = [
        event.sessionID,
        event.action,
        event.source?.messageID || "message",
        event.source?.id || "permission",
      ].join(":")

      try {
        const response = await hq("/v1/human", {
          method: "POST",
          body: JSON.stringify({
            uri: "human://approve",
            source: "opencode",
            ref,
            title: "Allow OpenCode " + event.action + "?",
            summary: event.resources.length
              ? "OpenCode is waiting on: " + event.resources.map(String).join(", ").slice(0, 900)
              : "OpenCode is waiting for a permission decision.",
            why_now: "OpenCode evaluated this action as ask and cannot continue without a human decision.",
            risk: 0.8,
            unblock: 0.95,
            seconds: 8,
            downstream: 1,
            idempotency_key: "opencode:" + ref,
            context: {
              native_handle: {
                provider: "opencode",
                session_id: event.sessionID,
                turn_id: event.source?.messageID || null,
                tool_use_id: event.source?.id || null,
                resume_kind: "opencode_permission_hook",
              },
              permission: {
                action: event.action,
                resources: event.resources,
                agent: event.agent,
              },
              recent_context: context.map(textOf),
            },
          }),
        })
        if (!response.ok) return
        const created = await response.json()
        const decision = await waitForDecision(created.request.id)
        const action = String(decision.action || "").toLowerCase()

        if (["approve", "allow", "accept", "continue"].includes(action)) {
          event.effect = "allow"
          event.message = "Approved in human://"
        } else if (["reject", "deny", "decline", "cancel"].includes(action)) {
          event.effect = "deny"
          event.message = String(decision.comment || "Denied in human://")
        }
      } catch {
        // Preserve OpenCode's original "ask" effect, which falls back to the
        // native client permission prompt. Never fail open.
      }
    })
  },
})
