# Agent Effect Authority v0.1 mapping

HumanQueue consumes the public `agent-effect-authority.v0.1` contract published by SpatialRuntime.

Source contract:

https://github.com/hippoley/SpatialRuntime/tree/main/interop/agent-effect-authority

This is a **cross-project adoption by the same maintainer**, not independent third-party adoption. It exists to prove that the contract can be consumed by a non-physical, approval-gated runtime without importing SpatialRuntime itself.

## Mapping

| AEA | HumanQueue evidence boundary |
| --- | --- |
| AEA-001 Proposal is not authority | agent/tool intent can reach a human boundary without becoming authorization; policy candidates remain shadow-only until explicitly adopted |
| AEA-002 Runtime-owned effect identity | canonical request identity and idempotency are owned by the Gateway/store rather than model output |
| AEA-003 Effect class known before execution | resume / approval paths are explicit before callback execution; the system distinguishes observation from consequence-bearing resume |
| AEA-004 Authorization is separate | pending human resolution, finalized decision, and machine resume are separate lifecycle states |
| AEA-005 ACK is not effect evidence | a receiver may execute once while its HTTP acknowledgement is lost; HumanQueue records the delivery as uncertain rather than assuming failure |
| AEA-006 Unresolved is first-class | ambiguous resume delivery remains `uncertain` until operator reconciliation |
| AEA-007 Retry identity prevents duplicate intent | retries reuse canonical identity/idempotency while delivery attempts remain distinct |
| AEA-008 Compensation/recovery is distinct | reconciliation is an explicit recovery operation; confirming executed closes without replay, confirming not-executed authorizes a new audited attempt |
| AEA-009 Fresh authority closes the loop | recovery requires receiver/operator evidence about whether the canonical request executed before a new attempt is authorized |

## Strongest case: lost acknowledgement after execution

HumanQueue intentionally exercises this sequence:

```text
human decision finalized
→ canonical resume request
→ receiver executes
→ ACK is lost
→ delivery becomes UNCERTAIN
→ automatic replay disabled
→ operator audits receiver
   ├─ executed     → close without resend
   └─ not executed → authorize one new audited attempt
```

The relevant packaged E2E paths are:

- `scripts/resume_ack_loss_e2e.py`
- `scripts/resume_outbox_uncertain_e2e.py`
- `scripts/resume_reconciliation_e2e.py`
- `scripts/resume_outbox_multiworker_e2e.py`

This case is intentionally different from SpatialRuntime's physical-effect reconciliation. It demonstrates the same authority boundary on webhook-based human approval and machine resume.
