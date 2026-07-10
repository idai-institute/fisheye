# Supervised actions

The host owns the tool registry. Agent/model output describes a proposed action; it must never become executable Python or a callable supplied to the supervisor.

```python
import asyncio
from fisheye import build_default_runtime
from fisheye.policies import Action, Policy

async def main():
    sent = []
    async with build_default_runtime() as runtime:
        supervisor = runtime.supervise(
            Policy(allowed_tools={"send"}, review_tools={"send"}, max_cost=10),
            {"send": lambda body: sent.append(body)},
        )
        action = Action(
            workflow_id="review-example", agent_id="writer", tool_name="send",
            arguments={"body": "Public summary"}, estimated_cost=0.01,
        )
        assert (await supervisor.propose(action)).status == "pending"
        # The host calls this only after a trusted human review.
        await supervisor.review(action, approve=True, reviewer="operator")
        await supervisor.execute(action)
        await supervisor.record_usage(action.action_id, cost=0.008, tokens=0)
        assert sent == ["Public summary"]

asyncio.run(main())
```

`propose()` records the prospective action, waits for analysis, evaluates policy, and persists its decision. Approval is bound to the exact action digest, actor/workflow, policy version, expiration, and one-time use. Keep original arguments in trusted host storage for execution after restart: persisted review previews are redacted.

`execute()` claims an approved action under a database write transaction, rechecks current policy/findings and budget, then invokes the registered tool. Concurrent connections cannot consume the same approval twice. Pending, denied, expired, changed, or consumed actions raise `ActionDenied` (pending raises its `ReviewRequired` subclass).

## Policy and budget semantics

Policies restrict tool names, exact destination hostnames, sensitive egress, delegation depth, current finding categories, and shared cost/token/call budgets. Destination checks cover the declared URL; the host tool must validate its actual URL, redirects, and arguments against the approved action. Self-reported classification and delegation depth are not authenticated authority proofs.

Approved actions reserve estimates atomically. Pending reviews reserve nothing; approval checks current availability. Completed/failed/unknown actions continue to consume reservations until the trusted host reconciles actual usage with `record_usage()`. Overruns return `exceeded=True` and block later reservations. Estimates cannot prevent a running tool exceeding its actual budget; enforce hard limits in the tool/provider when necessary.

A changed policy has a different version and invalidates old approvals. A reviewed action can still be denied at execution if a new blocking finding or overrun appears. Denied actions need a new proposal.

Policy collections are immutable. Malformed URLs, invalid ports, and control characters produce `invalid_destination` denials. Proposal evaluation checks current findings in the same database transaction as reservation. Expiring an approval/review persists its state and records one audit transition, including when a subsequent review attempt is rejected.

## Human review and recovery

Start the dashboard with `fisheye --config examples/local.toml serve --policy examples/review-policy.json`. Configure separate API and reviewer keys. HTTP mutations use `X-Review-Key`, map to policy reviewer `operator`, and are audited. CLI review relies on the local operator's trusted database access:

```bash
fisheye --db example-data/fisheye.db reviews list --policy examples/review-policy.json
fisheye --db example-data/fisheye.db reviews approve --policy examples/review-policy.json --action-id ACTION_ID --digest EXACT_DIGEST --reviewer operator
```

Approving changes durable state; it does not invoke a tool in the dashboard/CLI process. The original host resumes with `execute(original_action)`.

Cancellation records an unknown outcome. A process crash may leave an action `executing`; its external effect may already have happened. Fisheye never automatically retries it. Inspect the external system and use application-specific reconciliation. Exactly-once external effects require tool-level idempotency or transactional support.

Captured results normalize structured objects before redaction. Unknown object types are represented by type metadata rather than private string representations. If a result cannot be captured as finite JSON, the action still records its actual terminal status with a `capture_error` type, and the caller receives the original result. A telemetry formatting failure does not make a completed tool eligible to run again. Review detail queries expose the captured result/diagnostic using the same application authorization as the action.

This boundary protects only tools executed through it. Native callbacks, code outside the registry, and direct network/filesystem access remain under host control. Observation does not imply enforced pause, cancellation, or sandboxing.
