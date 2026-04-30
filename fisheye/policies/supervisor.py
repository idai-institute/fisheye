from __future__ import annotations

import asyncio
import inspect
import json
from datetime import datetime, timedelta, timezone
from typing import Callable

from fisheye.policies.models import Action, ActionDenied, Decision, Policy, ReviewRequired


class Supervisor:
    """Trusted execution gate with persistent review and atomic reservations.

    Tools must be registered by the host. An executing action is never retried
    automatically after a crash because its external effect may have occurred.
    """

    def __init__(self, store, policy: Policy, tools: dict[str, Callable] | None = None, runtime=None, clock=None):
        self.store, self.policy, self.tools, self.runtime = store, policy, dict(tools or {}), runtime
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        with store._lock:
            store._conn.executescript("""
                CREATE TABLE IF NOT EXISTS actions (
                    action_id TEXT PRIMARY KEY, scope TEXT NOT NULL, digest TEXT NOT NULL,
                    policy_version TEXT NOT NULL, decision TEXT NOT NULL, reason TEXT NOT NULL,
                    status TEXT NOT NULL, expires_at TEXT NOT NULL, created_at TEXT NOT NULL,
                    action_json TEXT NOT NULL, reserved_cost REAL NOT NULL, reserved_tokens INTEGER NOT NULL,
                    result_json TEXT, reviewer TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_actions_scope ON actions(scope,status);
                CREATE TABLE IF NOT EXISTS policies (version TEXT PRIMARY KEY, data_json TEXT NOT NULL);
            """)
            store._conn.execute(
                "INSERT OR IGNORE INTO policies VALUES(?,?)",
                (policy.version, store._json(policy.model_dump(mode="json"))),
            )
            store._conn.commit()

    def _audit(self, actor, operation, data):
        self.store._conn.execute(
            "INSERT INTO audit(timestamp,actor,operation,data_json) VALUES(?,?,?,?)",
            (self.clock().isoformat(), actor, operation, self.store._json(data)),
        )

    def _expire(self):
        self.store._conn.execute(
            "UPDATE actions SET status='expired',reason='expired' WHERE status IN ('approved','pending') AND expires_at<=?",
            (self.clock().isoformat(),),
        )

    def _budget_available(self, action, exclude=None):
        row = self.store._conn.execute(
            "SELECT COALESCE(SUM(reserved_cost),0) cost, COALESCE(SUM(reserved_tokens),0) tokens, COUNT(*) calls FROM actions WHERE scope=? AND status IN ('approved','executing','completed','failed','unknown') AND action_id!=?",
            (action.scope, exclude or ""),
        ).fetchone()
        return (
            row["cost"] + action.estimated_cost <= self.policy.max_cost
            and row["tokens"] + action.estimated_tokens <= self.policy.max_tokens
            and row["calls"] + 1 <= self.policy.max_calls
        )

    @staticmethod
    def _decision(row):
        return Decision(
            action_id=row["action_id"],
            decision=row["decision"],
            reason=row["reason"],
            policy_version=row["policy_version"],
            action_digest=row["digest"],
            status=row["status"],
            expires_at=row["expires_at"],
            created_at=row["created_at"],
        )

    async def propose(self, action: Action):
        # Copy the nested arguments so callers cannot mutate the approval input.
        action = Action.model_validate(action.model_dump(mode="json"))
        findings = []
        if self.runtime:
            await self.runtime.publish(
                dict(
                    schema_version="2",
                    event_id="proposal-" + action.action_id,
                    timestamp=action.created_at,
                    application_id=action.application_id,
                    environment=action.environment,
                    workflow_id=action.workflow_id,
                    agent_id=action.agent_id,
                    run_id=action.workflow_id,
                    event_type="action.proposed",
                    links=action.source_event_ids,
                    payload=dict(
                        tool_name=action.tool_name,
                        destination=action.destination,
                        classification=action.classification,
                        action_id=action.action_id,
                        arguments=action.arguments,
                    ),
                )
            )
            await self.runtime.drain(10)
            findings = await self.store.list_findings(scope=action.scope, limit=1000)
        choice, reason = self.policy.evaluate(action, findings)

        def persist():
            with self.store._lock:
                conn = self.store._conn
                conn.execute("BEGIN IMMEDIATE")
                try:
                    self._expire()
                    existing = conn.execute("SELECT * FROM actions WHERE action_id=?", (action.action_id,)).fetchone()
                    if existing:
                        if existing["digest"] != action.digest or existing["policy_version"] != self.policy.version:
                            raise ActionDenied("Action ID or policy changed")
                        conn.commit()
                        return self._decision(existing)
                    decision, why = choice, reason
                    if decision == "allow" and not self._budget_available(action):
                        decision, why = "deny", "budget_exhausted"
                    status = {"allow": "approved", "deny": "denied", "require_review": "pending"}[decision]
                    now = self.clock()
                    conn.execute(
                        "INSERT INTO actions(action_id,scope,digest,policy_version,decision,reason,status,expires_at,created_at,action_json,reserved_cost,reserved_tokens) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            action.action_id,
                            action.scope,
                            action.digest,
                            self.policy.version,
                            decision,
                            why,
                            status,
                            (now + timedelta(seconds=self.policy.approval_ttl_seconds)).isoformat(),
                            now.isoformat(),
                            self.store._json(action.model_dump(mode="json")),
                            action.estimated_cost,
                            action.estimated_tokens,
                        ),
                    )
                    self._audit(
                        action.agent_id,
                        "action.proposed",
                        dict(action_id=action.action_id, decision=decision, reason=why),
                    )
                    row = conn.execute("SELECT * FROM actions WHERE action_id=?", (action.action_id,)).fetchone()
                    conn.commit()
                    return self._decision(row)
                except BaseException:
                    conn.rollback()
                    raise

        return await asyncio.to_thread(persist)

    async def review(self, action: Action, approve: bool, reviewer: str):
        return await self.review_id(action.action_id, action.digest, approve, reviewer)

    async def review_id(self, action_id: str, action_digest: str, approve: bool, reviewer: str):
        if reviewer not in self.policy.reviewers:
            raise ActionDenied("Reviewer is not authorized")

        def update():
            with self.store._lock:
                conn = self.store._conn
                conn.execute("BEGIN IMMEDIATE")
                try:
                    self._expire()
                    row = conn.execute("SELECT * FROM actions WHERE action_id=?", (action_id,)).fetchone()
                    if (
                        not row
                        or row["status"] != "pending"
                        or row["digest"] != action_digest
                        or row["policy_version"] != self.policy.version
                    ):
                        raise ActionDenied("Review is stale, expired, or action has changed")
                    action = Action.model_validate_json(row["action_json"])
                    allowed = approve and self.policy.evaluate(action)[0] != "deny" and self._budget_available(action)
                    conn.execute(
                        "UPDATE actions SET decision=?,status=?,reason=?,reviewer=? WHERE action_id=?",
                        (
                            "allow" if allowed else "deny",
                            "approved" if allowed else "denied",
                            "human_approved" if allowed else "human_or_budget_denied",
                            reviewer,
                            action.action_id,
                        ),
                    )
                    self._audit(reviewer, "action.reviewed", dict(action_id=action.action_id, approved=allowed))
                    row = conn.execute("SELECT * FROM actions WHERE action_id=?", (action.action_id,)).fetchone()
                    conn.commit()
                    return self._decision(row)
                except BaseException:
                    conn.rollback()
                    raise

        return await asyncio.to_thread(update)

    async def execute(self, action: Action):
        action = Action.model_validate(action.model_dump(mode="json"))
        if action.tool_name not in self.tools:
            raise ActionDenied("Tool is not registered at this execution boundary")

        def claim():
            with self.store._lock, self.store._conn:
                self._expire()
                row = self.store._conn.execute(
                    "SELECT * FROM actions WHERE action_id=?", (action.action_id,)
                ).fetchone()
                if row and row["status"] == "pending":
                    raise ReviewRequired(action.action_id)
                if (
                    not row
                    or row["status"] != "approved"
                    or row["digest"] != action.digest
                    or row["policy_version"] != self.policy.version
                ):
                    raise ActionDenied("Action is denied, expired, changed, or already consumed")
                self.store._conn.execute("UPDATE actions SET status='executing' WHERE action_id=?", (action.action_id,))
                self._audit(action.agent_id, "action.executing", dict(action_id=action.action_id))

        await asyncio.to_thread(claim)
        tool = self.tools[action.tool_name]
        try:
            if inspect.iscoroutinefunction(tool):
                result = await tool(**action.arguments)
            else:
                result = await asyncio.to_thread(tool, **action.arguments)
                if inspect.isawaitable(result):
                    result = await result
        except BaseException as exc:
            await asyncio.shield(
                self._finish(
                    action,
                    "unknown" if isinstance(exc, asyncio.CancelledError) else "failed",
                    {"error_type": type(exc).__name__},
                )
            )
            raise
        await self._finish(action, "completed", {"result": result})
        return result

    async def _finish(self, action, status, result):
        def finish():
            with self.store._lock, self.store._conn:
                self.store._conn.execute(
                    "UPDATE actions SET status=?,result_json=? WHERE action_id=?",
                    (status, self.store._json(result), action.action_id),
                )
                self._audit(action.agent_id, "action." + status, dict(action_id=action.action_id))

        await asyncio.to_thread(finish)
        if self.runtime:
            await self.runtime.publish(
                dict(
                    schema_version="2",
                    application_id=action.application_id,
                    environment=action.environment,
                    workflow_id=action.workflow_id,
                    agent_id=action.agent_id,
                    run_id=action.workflow_id,
                    event_type="action.completed",
                    links=["proposal-" + action.action_id],
                    payload=dict(action_id=action.action_id, status=status),
                )
            )

    async def list_reviews(self, status="pending", limit=100):
        rows = await asyncio.to_thread(
            self.store._query, "SELECT * FROM actions WHERE status=? ORDER BY created_at LIMIT ?", (status, limit)
        )
        return [
            dict(self._decision(row).model_dump(mode="json"), action=json.loads(row["action_json"])) for row in rows
        ]
