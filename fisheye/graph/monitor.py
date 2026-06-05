"""Bounded causal graphs with explainable multi-agent monitors."""

from __future__ import annotations

import hashlib

from fisheye.schema.domain import Finding


class WorkflowGraph:
    def __init__(self, max_events=2000, token_budget=100000, cost_budget=100.0, call_budget=1000):
        self.max_events = max_events
        self.budgets = dict(tokens=token_budget, cost=cost_budget, calls=call_budget)

    def process(self, event, state):
        nodes = state.setdefault("nodes", {})
        tasks = state.setdefault("tasks", {})
        p = event.payload
        parents = list(dict.fromkeys(event.links + p.get("source_event_ids", [])))
        nodes[event.event_id] = dict(
            id=event.event_id,
            agent=event.agent_id,
            kind=event.event_type,
            timestamp=event.timestamp.isoformat(),
            parents=parents,
            payload=self.summary(p),
            analysis=event.meta.get("_analysis", {}),
        )
        if len(nodes) > self.max_events:
            for key in list(nodes)[: len(nodes) - self.max_events]:
                del nodes[key]
            state["truncated"] = True
        findings = []

        def emit(category, key, title, ids, score=0.9, **evidence):
            ids = list(dict.fromkeys(ids))
            fid = hashlib.sha256(f"{event.scope}:{category}:{key}".encode()).hexdigest()[:32]
            findings.append(
                Finding(
                    finding_id=fid,
                    application_id=event.application_id,
                    workflow_id=event.workflow,
                    category=category,
                    severity="high" if score >= 0.8 else "medium",
                    score=score,
                    title=title,
                    agent_ids=sorted({nodes[i]["agent"] for i in ids if i in nodes}),
                    event_ids=ids,
                    evidence=dict(evidence, graph_truncated=state.get("truncated", False)),
                    first_seen=event.timestamp,
                    last_seen=event.timestamp,
                )
            )

        if event.event_type == "task.delegated":
            task = p["task_id"]
            old = tasks.get(task)
            if (
                old
                and old.get("recipient") != p["recipient_id"]
                and old.get("status") not in {"completed", "cancelled", "failed"}
            ):
                emit(
                    "coordination",
                    task + ":duplicate",
                    "Task assigned to multiple active agents",
                    [old["event_id"], event.event_id],
                    reason="duplicate_assignment",
                )
            tasks[task] = dict(
                parent=p.get("parent_task_id"),
                recipient=p["recipient_id"],
                sender=event.agent_id,
                tools=p.get("allowed_tools", []),
                event_id=event.event_id,
                status="delegated",
                deadline=p.get("deadline"),
                waits_for=[],
            )
            chain, current = [], task
            while current in tasks and current not in chain:
                chain.append(current)
                current = tasks[current].get("parent")
            if current in chain:
                emit(
                    "coordination",
                    ":".join(sorted(chain)),
                    "Delegation cycle detected",
                    [tasks[t]["event_id"] for t in chain],
                    reason="delegation_cycle",
                )
        if event.event_type == "task.status":
            task = tasks.setdefault(p["task_id"], dict(event_id=event.event_id))
            task.update(status=p["status"], waits_for=p.get("waits_for", []), last_event_id=event.event_id)
            if p["status"] == "completed":
                missing = sorted(set(p.get("required_artifacts", [])) - set(p.get("artifact_ids", [])))
                if missing or (p.get("requires_verification") and not p.get("verified")):
                    emit(
                        "output_contract",
                        p["task_id"],
                        "Task completed without required evidence",
                        [event.event_id],
                        missing_artifacts=missing,
                        verification_missing=bool(p.get("requires_verification") and not p.get("verified")),
                    )

            def cycle(current, path):
                if current in path:
                    return path[path.index(current) :]
                for nxt in tasks.get(current, {}).get("waits_for", []):
                    if len(path) < 100:
                        found = cycle(nxt, path + [current])
                        if found:
                            return found
                return []

            loop = cycle(p["task_id"], [])
            if loop:
                emit(
                    "coordination",
                    ":".join(sorted(loop)),
                    "Tasks are waiting on one another",
                    [tasks[t].get("last_event_id", tasks[t]["event_id"]) for t in loop],
                    reason="wait_cycle",
                )
        if event.event_type in {"agent.heartbeat", "workflow.stop"}:
            for task_id, task in tasks.items():
                if task.get("status") in {"completed", "failed", "cancelled"}:
                    continue
                deadline = task.get("deadline")
                if event.event_type == "workflow.stop" or (deadline and deadline < event.timestamp.isoformat()):
                    emit(
                        "coordination",
                        task_id + ":overdue",
                        "Task remains unfinished past its deadline",
                        [task["event_id"], event.event_id],
                        reason="orphan" if event.event_type == "workflow.stop" else "deadline",
                        task_id=task_id,
                    )
        if len(tasks) > self.max_events:
            for key in list(tasks)[: len(tasks) - self.max_events]:
                del tasks[key]
            state["truncated"] = True

        if event.event_type == "usage.recorded":
            totals = state.setdefault("usage", dict(tokens=0, cost=0.0, calls=0))
            for key in totals:
                totals[key] += p.get(key, 0)
                if totals[key] > self.budgets[key]:
                    emit(
                        "resource_budget",
                        key,
                        f"Workflow {key} budget exceeded",
                        [event.event_id],
                        value=totals[key],
                        budget=self.budgets[key],
                    )

        # Revisit retained actions when late source/transfer events arrive.
        candidates = [
            n
            for n in nodes.values()
            if n["kind"]
            in {"action.proposed", "tool.call.start", "network.request", "artifact.transferred", "file.write"}
        ]
        emitted = state.setdefault("emitted", {})
        for sink in candidates:
            paths = self.ancestors(nodes, sink["id"])
            sp = sink["payload"]
            tool = str(sp.get("tool_name") or "")
            for source_id, path in paths.items():
                source = nodes[source_id]
                trust = source["payload"].get("trust", "unknown")
                injection = (
                    source["analysis"].get("injection_rules")
                    and trust not in {"trusted", "quoted"}
                    and not source["payload"].get("quoted")
                )
                sensitive = source["analysis"].get("sensitive_types") or source["payload"].get("classification") in {
                    "secret",
                    "confidential",
                }
                outbound = sink["kind"] in {"network.request", "file.write", "artifact.transferred"} or bool(
                    sp.get("destination") or sp.get("url") or sink["analysis"].get("outbound")
                )
                signatures = []
                if injection and (tool in {"shell", "http", "upload", "send", "file_write", "python_exec"} or outbound):
                    signatures.append(("injection_propagation", source_id))
                if sensitive and outbound and not sp.get("authorized", False):
                    signatures.append(("data_movement", source_id))
                for category, key in signatures:
                    dedup = category + ":" + source_id + ":" + sink["id"]
                    if dedup not in emitted:
                        emit(
                            category,
                            key,
                            "Untrusted instructions reached an action"
                            if category == "injection_propagation"
                            else "Sensitive data reached an outbound action",
                            path,
                            source=source_id,
                            action=sink["id"],
                            destination=sp.get("destination", sp.get("url")),
                            causal_path=path,
                        )
                        emitted[dedup] = True
            if event.task_id and sink["id"] == event.event_id and event.task_id in tasks:
                task = tasks[event.task_id]
                if task.get("tools") and tool and tool not in task["tools"]:
                    emit(
                        "authority",
                        event.task_id + ":" + tool,
                        "Action exceeds delegated tool authority",
                        [task["event_id"], event.event_id],
                        tool=tool,
                        allowed_tools=task["tools"],
                    )
        if len(emitted) > self.max_events * 2:
            for key in list(emitted)[: len(emitted) - self.max_events * 2]:
                del emitted[key]
        return findings

    @staticmethod
    def summary(payload):
        """Bound graph duplication; the journal retains the complete captured payload."""
        structural = {"trust", "quoted", "classification", "tool_name", "destination", "url", "authorized"}
        result = {key: value for key, value in payload.items() if key in structural}
        for key in ("content", "input", "output", "text"):
            if isinstance(payload.get(key), str):
                result[key] = payload[key][:256]
        return result

    @staticmethod
    def ancestors(nodes, event_id):
        paths = {event_id: [event_id]}
        pending = [event_id]
        while pending:
            current = pending.pop()
            for parent in nodes[current]["parents"]:
                if parent in nodes and parent not in paths:
                    paths[parent] = [parent] + paths[current]
                    pending.append(parent)
        return paths
