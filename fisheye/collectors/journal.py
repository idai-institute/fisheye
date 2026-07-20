"""SQLite acceptance journal and atomic analysis checkpoints."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timezone

from fisheye.bus.async_bus import DeliveryReceipt, OverloadedError
from fisheye.collectors.sqlite_store import SQLiteStore
from fisheye.findings import merge_finding
from fisheye.schema.domain import Finding
from fisheye.schema.events import EventEnvelope


class EventConflictError(ValueError):
    pass


class JournalStore(SQLiteStore):
    def __init__(self, db_path, max_pending: int = 100000):
        super().__init__(db_path)
        self.max_pending = max_pending
        self._transaction = False
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=FULL")
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS journal (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE, scope TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, event_json TEXT NOT NULL,
                    accepted_at TEXT NOT NULL, processed INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_journal_pending ON journal(processed,sequence);
                CREATE INDEX IF NOT EXISTS idx_journal_scope ON journal(scope,sequence);
                CREATE TABLE IF NOT EXISTS checkpoints (
                    scope TEXT PRIMARY KEY, sequence INTEGER NOT NULL,
                    state_json TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS findings (
                    finding_id TEXT PRIMARY KEY, scope TEXT NOT NULL, category TEXT NOT NULL,
                    status TEXT NOT NULL, last_seen TEXT NOT NULL, data_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_findings_scope ON findings(scope,last_seen);
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
                    actor TEXT NOT NULL, operation TEXT NOT NULL, data_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS dead_letters (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, sequence INTEGER, plugin TEXT,
                    error_type TEXT NOT NULL, timestamp TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS export_outbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, item_key TEXT NOT NULL UNIQUE,
                    kind TEXT NOT NULL, data_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS journal_settings (key TEXT PRIMARY KEY, value BLOB NOT NULL);
                PRAGMA user_version=2;
            """)
            self._conn.commit()
            self._conn.execute(
                "INSERT OR IGNORE INTO journal_settings VALUES(?,?)", ("identity_key", secrets.token_bytes(32))
            )
            self._identity_key = self._conn.execute(
                "SELECT value FROM journal_settings WHERE key='identity_key'"
            ).fetchone()[0]
            self._conn.commit()

    def _commit(self):
        if not getattr(self, "_transaction", False):
            self._conn.commit()

    async def accept(self, event: EventEnvelope, identity_event: EventEnvelope | None = None) -> DeliveryReceipt:
        return (await self.accept_many([event], [identity_event or event]))[0]

    async def accept_many(self, events, identity_events=None):
        """Accept a validated batch atomically, including backlog and identity checks."""
        events = list(events)
        identities = list(identity_events) if identity_events is not None else events
        if len(events) != len(identities):
            raise ValueError("Identity count must match event count")

        def accept_batch():
            with self._lock:
                self._conn.execute("BEGIN IMMEDIATE")
                self._transaction = True
                try:
                    receipts = [self._accept(event, identity) for event, identity in zip(events, identities)]
                    self._conn.commit()
                    return receipts
                except BaseException:
                    self._conn.rollback()
                    raise
                finally:
                    self._transaction = False

        return await asyncio.to_thread(accept_batch)

    def _accept(self, event, identity_event=None):
        data = event.model_dump(mode="json")
        # Arrival time and route metadata do not change producer identity.
        canonical = (identity_event or event).model_dump(mode="json")
        canonical.pop("observed_at", None)
        for key in ["_analysis", "features", "_route_mode"]:
            canonical["meta"].pop(key, None)
        fingerprint = hmac.new(
            self._identity_key, json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode(), hashlib.sha256
        ).hexdigest()
        with self._lock:
            row = self._conn.execute(
                "SELECT sequence,fingerprint FROM journal WHERE event_id=?", (event.event_id,)
            ).fetchone()
            if row:
                if row["fingerprint"] != fingerprint:
                    raise EventConflictError("Event ID already exists with different content")
                return DeliveryReceipt(event.event_id, 0, durable=True, duplicate=True, sequence=row["sequence"])
            pending = self._conn.execute("SELECT COUNT(*) FROM journal WHERE processed=0").fetchone()[0]
            if pending >= self.max_pending:
                raise OverloadedError("Durable backlog limit reached")
            cursor = self._conn.execute(
                "INSERT INTO journal(event_id,scope,fingerprint,event_json,accepted_at) VALUES(?,?,?,?,?)",
                (event.event_id, event.scope, fingerprint, self._json(data), datetime.now(timezone.utc).isoformat()),
            )
            self._commit()
            return DeliveryReceipt(event.event_id, 0, durable=True, sequence=cursor.lastrowid)

    async def pending(self, limit=100):
        rows = await asyncio.to_thread(
            self._query, "SELECT sequence,event_json FROM journal WHERE processed=0 ORDER BY sequence LIMIT ?", (limit,)
        )
        return [(r["sequence"], EventEnvelope.model_validate_json(r["event_json"])) for r in rows]

    async def checkpoint(self, scope):
        rows = await asyncio.to_thread(self._query, "SELECT state_json FROM checkpoints WHERE scope=?", (scope,))
        return json.loads(rows[0]["state_json"]) if rows else None

    async def commit_analysis(self, sequence, event, state, alerts=(), findings=(), signals=(), errors=()):
        await self.commit_analysis_batch([(sequence, event, state, alerts, findings, signals, errors)])

    async def commit_analysis_batch(self, items):
        items = list(items)

        def commit_batch():
            with self._lock:
                self._conn.execute("BEGIN IMMEDIATE")
                self._transaction = True
                try:
                    last = {item[1].scope: index for index, item in enumerate(items)}
                    for index, item in enumerate(items):
                        self._commit_analysis(*item, save_checkpoint=last[item[1].scope] == index)
                    self._conn.commit()
                except BaseException:
                    self._conn.rollback()
                    raise
                finally:
                    self._transaction = False

        task = asyncio.create_task(asyncio.to_thread(commit_batch))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            # SQLite writes in a thread cannot be cancelled. Retain ownership until
            # the transaction settles, then let shutdown release the analyzer lock.
            await task
            raise

    def _commit_analysis(self, sequence, event, state, alerts, findings, signals, errors, save_checkpoint=True):
        with self._lock:
            try:
                row = self._conn.execute("SELECT processed FROM journal WHERE sequence=?", (sequence,)).fetchone()
                if row is None or row["processed"]:
                    return
                self._insert_event(event)
                self._conn.execute(
                    "INSERT OR IGNORE INTO export_outbox(item_key,kind,data_json) VALUES(?,?,?)",
                    (event.event_id, "event", self._json(event.model_dump(mode="json"))),
                )
                for alert in alerts:
                    self._insert_alert(alert)
                    self._conn.execute(
                        "INSERT OR IGNORE INTO export_outbox(item_key,kind,data_json) VALUES(?,?,?)",
                        (alert.alert_id, "alert", self._json(alert.model_dump(mode="json"))),
                    )
                for signal in signals:
                    self._insert_detector_signal(signal, event)
                for finding in findings:
                    old = self._conn.execute(
                        "SELECT status,data_json FROM findings WHERE finding_id=?", (finding.finding_id,)
                    ).fetchone()
                    finding = merge_finding(Finding.model_validate_json(old["data_json"]) if old else None, finding)
                    if old:
                        if old["status"] == "resolved" and finding.status == "open":
                            self._conn.execute(
                                "INSERT INTO audit(timestamp,actor,operation,data_json) VALUES(?,?,?,?)",
                                (
                                    event.observed_at.isoformat(),
                                    "analysis",
                                    "finding.reopened",
                                    self._json({"finding_id": finding.finding_id, "reason": "new_evidence"}),
                                ),
                            )
                    self._conn.execute(
                        "INSERT OR REPLACE INTO findings VALUES(?,?,?,?,?,?)",
                        (
                            finding.finding_id,
                            event.scope,
                            finding.category,
                            finding.status,
                            str(finding.last_seen),
                            self._json(finding.model_dump(mode="json")),
                        ),
                    )
                for plugin, error_type in errors:
                    self._conn.execute(
                        "INSERT INTO dead_letters(sequence,plugin,error_type,timestamp) VALUES(?,?,?,?)",
                        (sequence, plugin, error_type, event.observed_at.isoformat()),
                    )
                if save_checkpoint:
                    self._conn.execute(
                        "INSERT OR REPLACE INTO checkpoints VALUES(?,?,?,?)",
                        (event.scope, sequence, self._json(state), event.observed_at.isoformat()),
                    )
                self._conn.execute("UPDATE journal SET processed=1 WHERE sequence=?", (sequence,))
                self._commit()
            except BaseException:
                self._conn.rollback()
                raise

    async def journal_events(self, scope=None, after=0, limit=1000):
        where, args = "sequence>?", [after]
        if scope is not None:
            where += " AND scope=?"
            args.append(scope)
        rows = await asyncio.to_thread(
            self._query,
            f"SELECT sequence,event_json FROM journal WHERE {where} ORDER BY sequence LIMIT ?",
            tuple(args + [limit]),
        )
        return [dict(sequence=r["sequence"], event=json.loads(r["event_json"])) for r in rows]

    async def get_event(self, event_id, application_id=None):
        where, args = "event_id=?", [event_id]
        if application_id is not None:
            where += " AND json_extract(scope,'$[0]')=?"
            args.append(application_id)
        rows = await asyncio.to_thread(
            self._query, "SELECT sequence,event_json FROM journal WHERE " + where, tuple(args)
        )
        return dict(sequence=rows[0]["sequence"], event=json.loads(rows[0]["event_json"])) if rows else None

    async def list_findings(self, scope=None, status=None, limit=100, offset=0):
        terms, args = [], []
        for field, value in [("scope", scope), ("status", status)]:
            if value is not None:
                terms.append(field + "=?")
                args.append(value)
        where = " WHERE " + " AND ".join(terms) if terms else ""
        rows = await asyncio.to_thread(
            self._query,
            "SELECT data_json FROM findings" + where + " ORDER BY last_seen DESC,finding_id LIMIT ? OFFSET ?",
            tuple(args + [limit, offset]),
        )
        return [json.loads(r["data_json"]) for r in rows]

    async def update_finding(self, finding_id, status, actor):
        if status not in {"open", "acknowledged", "resolved", "false_positive"} or not actor.strip():
            raise ValueError("Invalid finding status or actor")

        def update():
            with self._lock, self._conn:
                self._conn.execute("BEGIN IMMEDIATE")
                row = self._conn.execute("SELECT data_json FROM findings WHERE finding_id=?", (finding_id,)).fetchone()
                if row is None:
                    raise KeyError(finding_id)
                data = json.loads(row["data_json"])
                data["status"] = status
                self._conn.execute(
                    "UPDATE findings SET status=?,data_json=? WHERE finding_id=?",
                    (status, self._json(data), finding_id),
                )
                self._conn.execute(
                    "INSERT INTO audit(timestamp,actor,operation,data_json) VALUES(?,?,?,?)",
                    (
                        datetime.now(timezone.utc).isoformat(),
                        actor,
                        "finding.status",
                        self._json(dict(finding_id=finding_id, status=status)),
                    ),
                )
                return data

        return await asyncio.to_thread(update)

    async def journal_metrics(self):
        def read():
            with self._lock:
                row = self._conn.execute(
                    "SELECT COUNT(*) pending, MIN(accepted_at) oldest FROM journal WHERE processed=0"
                ).fetchone()
                return dict(
                    accepted=self._conn.execute("SELECT COUNT(*) FROM journal").fetchone()[0],
                    pending=row["pending"] or 0,
                    oldest_pending=row["oldest"],
                    dead_letters=self._conn.execute("SELECT COUNT(*) FROM dead_letters").fetchone()[0],
                )

        return await asyncio.to_thread(read)

    async def list_workflows(self, application_id=None, limit=100, offset=0):
        where = " WHERE json_extract(event_json,'$.application_id')=?" if application_id else ""
        args = ([application_id] if application_id else []) + [limit, offset]
        rows = await asyncio.to_thread(
            self._query,
            "SELECT scope, MIN(accepted_at) first_seen, MAX(accepted_at) last_seen, COUNT(*) event_count, "
            "GROUP_CONCAT(DISTINCT json_extract(event_json,'$.agent_id')) agents FROM journal"
            + where
            + " GROUP BY scope ORDER BY last_seen DESC LIMIT ? OFFSET ?",
            tuple(args),
        )
        return [
            dict(
                application_id=json.loads(r["scope"])[0],
                environment=json.loads(r["scope"])[1],
                workflow_id=json.loads(r["scope"])[2],
                agent_ids=r["agents"].split(","),
                first_seen=r["first_seen"],
                last_seen=r["last_seen"],
                event_count=r["event_count"],
            )
            for r in rows
        ]

    async def workflow_graph(self, scope):
        checkpoint = await self.checkpoint(scope)
        if checkpoint is None:
            return dict(nodes=[], edges=[], tasks={}, usage={}, truncated=False, invalid_relationships=0)
        from fisheye.state.codec import decode

        graph = decode(checkpoint["data"]).get("graph", {})
        nodes = graph.get("nodes", {})
        return dict(
            nodes=list(nodes.values()),
            edges=[{"source": p, "target": key} for key, node in nodes.items() for p in node["parents"]],
            tasks=graph.get("tasks", {}),
            usage=graph.get("usage", {}),
            truncated=graph.get("truncated", False),
            invalid_relationships=graph.get("invalid_relationships", 0),
        )

    async def prune(self, retention_days=30, state_ttl_seconds=86400, now=None):
        from datetime import timedelta

        now = now or datetime.now(timezone.utc)
        cutoff = (now - timedelta(days=retention_days)).isoformat()
        state_cutoff = (now - timedelta(seconds=state_ttl_seconds)).isoformat()

        def remove():
            with self._lock, self._conn:
                states = self._conn.execute(
                    "DELETE FROM checkpoints WHERE updated_at<? AND scope NOT IN (SELECT scope FROM journal WHERE processed=0)",
                    (state_cutoff,),
                ).rowcount
                rows = self._conn.execute(
                    "SELECT event_id FROM journal j WHERE processed=1 AND accepted_at<? AND NOT EXISTS (SELECT 1 FROM findings f,json_each(json_extract(f.data_json,'$.event_ids')) e WHERE f.status IN ('open','acknowledged') AND e.value=j.event_id)",
                    (cutoff,),
                ).fetchall()
                ids = [(r["event_id"],) for r in rows]
                self._conn.executemany("DELETE FROM events WHERE event_id=?", ids)
                self._conn.executemany("DELETE FROM detector_signals WHERE event_id=?", ids)
                self._conn.executemany("DELETE FROM journal WHERE event_id=?", ids)
                self._conn.execute("DELETE FROM behavior_stats WHERE timestamp<?", (cutoff,))
                self._conn.execute("DELETE FROM dead_letters WHERE timestamp<?", (cutoff,))
                self._conn.execute(
                    "UPDATE runs SET event_count=(SELECT COUNT(*) FROM events WHERE events.run_id=runs.run_id)"
                )
                self._conn.execute("DELETE FROM runs WHERE event_count=0")
                return dict(events=len(ids), checkpoints=states)

        return await asyncio.to_thread(remove)

    async def pending_exports(self, limit=100):
        return [
            dict(r)
            for r in await asyncio.to_thread(self._query, "SELECT * FROM export_outbox ORDER BY id LIMIT ?", (limit,))
        ]

    async def acknowledge_export(self, item_id):
        await self.acknowledge_exports([item_id])

    async def acknowledge_exports(self, item_ids):
        def ack():
            with self._lock, self._conn:
                self._conn.executemany("DELETE FROM export_outbox WHERE id=?", [(item_id,) for item_id in item_ids])

        await asyncio.to_thread(ack)

    async def scoped_alerts(
        self, application_id, limit=100, triggered_only=False, min_score=None, run_id=None, alert_id=None
    ):
        terms = [
            "EXISTS (SELECT 1 FROM json_each(a.related_event_ids_json) e JOIN journal j ON j.event_id=e.value WHERE json_extract(j.event_json,'$.application_id')=?)"
        ]
        args = [application_id]
        for field, value in [("run_id", run_id), ("alert_id", alert_id)]:
            if value is not None:
                terms.append("a." + field + "=?")
                args.append(value)
        if triggered_only:
            terms.append("a.triggered=1")
        if min_score is not None:
            terms.append("a.score>=?")
            args.append(min_score)
        rows = await asyncio.to_thread(
            self._query,
            "SELECT a.* FROM alerts a WHERE " + " AND ".join(terms) + " ORDER BY a.timestamp DESC LIMIT ?",
            tuple(args + [limit]),
        )
        return [self._alert_row_to_dict(r) for r in rows]

    async def scoped_runs(self, application_id, limit=100):
        rows = await asyncio.to_thread(
            self._query,
            "SELECT json_extract(event_json,'$.run_id') run_id, MIN(accepted_at) first_seen, MAX(accepted_at) last_seen, COUNT(*) event_count, GROUP_CONCAT(DISTINCT json_extract(event_json,'$.agent_id')) agents FROM journal WHERE json_extract(event_json,'$.application_id')=? GROUP BY json_extract(event_json,'$.run_id') ORDER BY last_seen DESC LIMIT ?",
            (application_id, limit),
        )
        return [
            dict(
                run_id=r["run_id"],
                first_seen=r["first_seen"],
                last_seen=r["last_seen"],
                event_count=r["event_count"],
                agent_ids=r["agents"].split(","),
            )
            for r in rows
        ]

    async def scoped_run_events(self, application_id, run_id, limit=500):
        rows = await asyncio.to_thread(
            self._query,
            "SELECT event_json FROM journal WHERE json_extract(event_json,'$.application_id')=? AND json_extract(event_json,'$.run_id')=? ORDER BY sequence LIMIT ?",
            (application_id, run_id, limit),
        )
        return [json.loads(r["event_json"]) for r in rows]

    async def get_finding(self, finding_id):
        rows = await asyncio.to_thread(self._query, "SELECT data_json FROM findings WHERE finding_id=?", (finding_id,))
        return json.loads(rows[0]["data_json"]) if rows else None
