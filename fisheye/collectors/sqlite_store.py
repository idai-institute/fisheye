from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fisheye.collectors.base import AlertSink, Collector
from fisheye.detectors.base import DetectorSignal
from fisheye.privacy import redact
from fisheye.schema.alerts import Alert
from fisheye.schema.events import EventEnvelope


@dataclass(slots=True)
class BehaviorStat:
    timestamp: str
    agent_id: str
    run_id: str
    metric_name: str
    value: float
    zscore: float
    tool_name: str | None = None


class SQLiteStore(Collector, AlertSink):
    name = "sqlite_store"

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        if self._conn.execute("PRAGMA user_version").fetchone()[0] > 2:
            self._conn.close()
            raise ValueError("Database schema is newer than this library")
        self._init_schema()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _commit(self) -> None:
        self._conn.commit()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    trace_id TEXT,
                    span_id TEXT,
                    parent_span_id TEXT,
                    session_id TEXT,
                    framework TEXT,
                    tags_json TEXT,
                    meta_json TEXT,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    event_count INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS alerts (
                    alert_id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    score REAL NOT NULL,
                    threshold REAL NOT NULL,
                    triggered INTEGER NOT NULL,
                    sources_json TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    related_event_ids_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS detector_signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    detector_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    score REAL NOT NULL,
                    event_id TEXT,
                    agent_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    evidence_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS behavior_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    metric_name TEXT NOT NULL,
                    value REAL NOT NULL,
                    zscore REAL NOT NULL,
                    tool_name TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
                CREATE INDEX IF NOT EXISTS idx_events_agent_run_ts ON events(agent_id, run_id, timestamp);
                CREATE INDEX IF NOT EXISTS idx_alerts_category_score_ts ON alerts(category, score, timestamp);
                """
            )
            self._commit()

    def _json(self, data: Any) -> str:
        return json.dumps(
            data if getattr(self, "raw_capture", False) else redact(data), default=str, separators=(",", ":")
        )

    @staticmethod
    def _decode_json(value: str | None, default: Any) -> Any:
        if not value:
            return default
        return json.loads(value)

    async def handle_event(self, event: EventEnvelope) -> None:
        await asyncio.to_thread(self._insert_event, event)

    async def handle_alert(self, alert: Alert) -> None:
        await asyncio.to_thread(self._insert_alert, alert)

    async def record_detector_signal(self, signal: DetectorSignal, event: EventEnvelope) -> None:
        await asyncio.to_thread(self._insert_detector_signal, signal, event)

    async def record_behavior_stat(self, stat: BehaviorStat) -> None:
        await asyncio.to_thread(self._insert_behavior_stat, stat)

    def _insert_event(self, event: EventEnvelope) -> None:
        with self._lock:
            if self._conn.execute("SELECT 1 FROM events WHERE event_id=?", (event.event_id,)).fetchone():
                return
            self._conn.execute(
                """
                INSERT INTO events (
                    event_id,timestamp,event_type,agent_id,run_id,trace_id,span_id,parent_span_id,
                    session_id,framework,tags_json,meta_json,payload_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    event.event_id,
                    event.timestamp.isoformat(),
                    event.event_type,
                    event.agent_id,
                    event.run_id,
                    event.trace_id,
                    event.span_id,
                    event.parent_span_id,
                    event.session_id,
                    event.framework,
                    self._json(event.tags),
                    self._json(event.meta),
                    self._json(event.payload),
                ),
            )
            self._conn.execute(
                """
                INSERT INTO runs (run_id, agent_id, first_seen, last_seen, event_count)
                VALUES (?, ?, ?, ?, 1)
                ON CONFLICT(run_id) DO UPDATE SET
                    first_seen=MIN(first_seen,excluded.first_seen),
                    last_seen=MAX(last_seen,excluded.last_seen),
                    event_count=event_count+1
                """,
                (
                    event.run_id,
                    event.agent_id,
                    event.timestamp.isoformat(),
                    event.timestamp.isoformat(),
                ),
            )
            self._commit()

    def _insert_alert(self, alert: Alert) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO alerts (
                    alert_id,timestamp,agent_id,run_id,category,score,threshold,triggered,
                    sources_json,evidence_json,related_event_ids_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    alert.alert_id,
                    alert.timestamp.isoformat(),
                    alert.agent_id,
                    alert.run_id,
                    alert.category,
                    alert.score,
                    alert.threshold,
                    int(alert.triggered),
                    self._json(alert.sources),
                    self._json(alert.evidence),
                    self._json(alert.related_event_ids),
                ),
            )
            self._commit()

    def _insert_detector_signal(self, signal: DetectorSignal, event: EventEnvelope) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO detector_signals (
                    timestamp,detector_id,category,score,event_id,agent_id,run_id,evidence_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.timestamp.isoformat(),
                    signal.detector_id,
                    signal.category,
                    signal.score,
                    event.event_id,
                    event.agent_id,
                    event.run_id,
                    self._json(signal.evidence),
                ),
            )
            self._commit()

    def _insert_behavior_stat(self, stat: BehaviorStat) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO behavior_stats (
                    timestamp,agent_id,run_id,metric_name,value,zscore,tool_name
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stat.timestamp,
                    stat.agent_id,
                    stat.run_id,
                    stat.metric_name,
                    stat.value,
                    stat.zscore,
                    stat.tool_name,
                ),
            )
            self._commit()

    def _query(self, sql: str, args: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return rows

    async def list_alerts(
        self,
        limit: int = 100,
        triggered_only: bool = False,
        min_score: float | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        args: list[Any] = []

        if triggered_only:
            clauses.append("triggered = 1")
        if min_score is not None:
            clauses.append("score >= ?")
            args.append(min_score)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        args.append(limit)

        rows = await asyncio.to_thread(
            self._query,
            f"""
            SELECT *
            FROM alerts
            {where}
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            tuple(args),
        )
        return [self._alert_row_to_dict(row) for row in rows]

    async def get_alert(self, alert_id: str) -> dict[str, Any] | None:
        rows = await asyncio.to_thread(
            self._query,
            "SELECT * FROM alerts WHERE alert_id = ?",
            (alert_id,),
        )
        if not rows:
            return None
        return self._alert_row_to_dict(rows[0])

    async def list_runs(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = await asyncio.to_thread(
            self._query,
            "SELECT * FROM runs ORDER BY last_seen DESC LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in rows]

    async def get_run_events(self, run_id: str, limit: int = 500) -> list[dict[str, Any]]:
        rows = await asyncio.to_thread(
            self._query,
            """
            SELECT * FROM events
            WHERE run_id = ?
            ORDER BY timestamp ASC
            LIMIT ?
            """,
            (run_id, limit),
        )
        return [self._event_row_to_dict(row) for row in rows]

    async def get_run_alerts(self, run_id: str, limit: int = 200) -> list[dict[str, Any]]:
        rows = await asyncio.to_thread(
            self._query,
            """
            SELECT * FROM alerts
            WHERE run_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (run_id, limit),
        )
        return [self._alert_row_to_dict(row) for row in rows]

    async def list_detectors(self) -> list[str]:
        rows = await asyncio.to_thread(
            self._query,
            "SELECT DISTINCT detector_id FROM detector_signals ORDER BY detector_id ASC",
        )
        return [row["detector_id"] for row in rows]

    @classmethod
    def _alert_row_to_dict(cls, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "alert_id": row["alert_id"],
            "timestamp": row["timestamp"],
            "agent_id": row["agent_id"],
            "run_id": row["run_id"],
            "category": row["category"],
            "score": row["score"],
            "threshold": row["threshold"],
            "triggered": bool(row["triggered"]),
            "sources": cls._decode_json(row["sources_json"], []),
            "evidence": cls._decode_json(row["evidence_json"], {}),
            "related_event_ids": cls._decode_json(row["related_event_ids_json"], []),
        }

    @classmethod
    def _event_row_to_dict(cls, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "event_id": row["event_id"],
            "timestamp": row["timestamp"],
            "event_type": row["event_type"],
            "agent_id": row["agent_id"],
            "run_id": row["run_id"],
            "trace_id": row["trace_id"],
            "span_id": row["span_id"],
            "parent_span_id": row["parent_span_id"],
            "session_id": row["session_id"],
            "framework": row["framework"],
            "tags": cls._decode_json(row["tags_json"], {}),
            "meta": cls._decode_json(row["meta_json"], {}),
            "payload": cls._decode_json(row["payload_json"], {}),
        }
