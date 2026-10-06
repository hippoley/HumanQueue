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

function patternsOf(value: unknown): string[] {
  if (Array.isArray(value)) return value.map(String)
  if (value === undefined || value === null || value === "") return []
  return [String(value)]
}

async function recordSession(input: {
  event_name: string
  session_id: string
  user?: string
  assistant?: string
  action?: string
  resources?: readonly string[]
  message_id?: string
  tool_response?: unknown
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
        tool_response: input.tool_response ?? null,
        metadata: { runtime: "opencode-plugin-current" },
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

// Current OpenCode local-plugin API: export one or more async plugin functions.
// OpenCode loads each function export and expects it to return a Hooks object.
export const HumanQueuePlugin = async ({ directory }: { directory: string }) => {
  const probeFile = process.env.HUMAN_QUEUE_OPENCODE_PROBE_FILE
  if (probeFile) {
    try {
      await Bun.write(probeFile, JSON.stringify({
        loaded: true,
        directory,
        runtime: "opencode-plugin-current",
        pid: process.pid,
      }) + "\n")
    } catch {
      // Diagnostic-only probe must never break OpenCode startup.
    }
  }

  return {
  "chat.message": async (input: any, output: any) => {
    await recordSession({
      event_name: "UserPromptSubmit",
      session_id: String(input.sessionID || ""),
      user: textOf(output?.parts || output?.message || ""),
      message_id: input.messageID ? String(input.messageID) : undefined,
    })
  },

  "permission.ask": async (input: any, output: { status: "ask" | "deny" | "allow" }) => {
    // Preserve upstream policy. Only intercept decisions OpenCode itself left at "ask".
    if (output.status !== "ask") return

    const resources = patternsOf(input.pattern)
    const permissionID = String(input.id || input.callID || "permission")
    const messageID = String(input.messageID || "message")
    const callID = input.callID ? String(input.callID) : permissionID
    const permissionType = String(input.type || "permission")
    const sessionID = String(input.sessionID || "")

    await recordSession({
      event_name: "PermissionRequest",
      session_id: sessionID,
      action: permissionType,
      resources,
      message_id: callID,
    })

    const ref = [sessionID, permissionType, messageID, callID].join(":")

    try {
      const response = await hq("/v1/human", {
        method: "POST",
        body: JSON.stringify({
          uri: "human://approve",
          source: "opencode",
          ref,
          title: String(input.title || ("Allow OpenCode " + permissionType + "?")),
          summary: resources.length
            ? "OpenCode is waiting on: " + resources.join(", ").slice(0, 900)
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
              session_id: sessionID,
              turn_id: messageID,
              tool_use_id: callID,
              resume_kind: "opencode_permission_hook",
            },
            permission: {
              id: permissionID,
              type: permissionType,
              pattern: input.pattern ?? null,
              title: input.title ?? null,
              metadata: input.metadata ?? null,
            },
            runtime: {
              plugin_api: "permission.ask",
              directory,
            },
          },
        }),
      })
      if (!response.ok) return
      const created = await response.json()
      const decision = await waitForDecision(created.request.id)
      const action = String(decision.action || "").toLowerCase()

      if (["approve", "allow", "accept", "continue"].includes(action)) {
        output.status = "allow"
      } else if (["reject", "deny", "decline", "cancel"].includes(action)) {
        output.status = "deny"
      }
    } catch {
      // Keep OpenCode's original ask state so the native prompt remains authoritative.
    }
  },

  "tool.execute.after": async (input: any, output: any) => {
    await recordSession({
      event_name: "PostToolUse",
      session_id: String(input.sessionID || ""),
      action: String(input.tool || ""),
      resources: [textOf(input.args || {})],
      message_id: input.callID ? String(input.callID) : undefined,
      tool_response: {
        title: output?.title ?? null,
        output: textOf(output?.output ?? ""),
        metadata: output?.metadata ?? null,
      },
    })
  },

  event: async ({ event }: any) => {
    const properties = event?.properties || {}
    const sessionID = properties.sessionID || properties.info?.id || properties.id
    if (!sessionID) return

    if (event.type === "session.created") {
      await recordSession({ event_name: "SessionStart", session_id: String(sessionID) })
    } else if (event.type === "session.idle") {
      await recordSession({ event_name: "Stop", session_id: String(sessionID) })
    } else if (event.type === "session.deleted") {
      await recordSession({ event_name: "SessionEnd", session_id: String(sessionID) })
    }
  },
  }
}
