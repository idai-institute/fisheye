"""SQLite acceptance journal and atomic analysis checkpoints."""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from fisheye.bus.async_bus import DeliveryReceipt, OverloadedError
from fisheye.collectors.sqlite_store import SQLiteStore
from fisheye.schema.events import EventEnvelope


class EventConflictError(ValueError):
    pass


class JournalStore(SQLiteStore):
    def __init__(self, db_path, max_pending: int = 100000):
        super().__init__(db_path)
        self.max_pending = max_pending
        self._transaction = False
        with self._lock:
            self._conn.execute('PRAGMA journal_mode=WAL')
            self._conn.execute('PRAGMA synchronous=FULL')
            self._conn.executescript('''
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
                PRAGMA user_version=2;
            ''')
            self._conn.commit()

    def _commit(self):
        if not getattr(self, '_transaction', False):
            self._conn.commit()

    async def accept(self, event: EventEnvelope) -> DeliveryReceipt:
        return await asyncio.to_thread(self._accept, event)

    def _accept(self, event):
        data = event.model_dump(mode='json')
        # Arrival time and route metadata do not change producer identity.
        canonical = dict(data)
        canonical.pop('observed_at', None)
        fingerprint = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        with self._lock:
            row = self._conn.execute('SELECT sequence,fingerprint FROM journal WHERE event_id=?', (event.event_id,)).fetchone()
            if row:
                if row['fingerprint'] != fingerprint:
                    raise EventConflictError('Event ID already exists with different content')
                return DeliveryReceipt(event.event_id, 0, durable=True, duplicate=True, sequence=row['sequence'])
            pending = self._conn.execute('SELECT COUNT(*) FROM journal WHERE processed=0').fetchone()[0]
            if pending >= self.max_pending:
                raise OverloadedError('Durable backlog limit reached')
            cursor = self._conn.execute('INSERT INTO journal(event_id,scope,fingerprint,event_json,accepted_at) VALUES(?,?,?,?,?)',
                (event.event_id, event.scope, fingerprint, self._json(data), datetime.now(timezone.utc).isoformat()))
            self._conn.commit()
            return DeliveryReceipt(event.event_id, 0, durable=True, sequence=cursor.lastrowid)

    async def pending(self, limit=100):
        rows = await asyncio.to_thread(self._query, 'SELECT sequence,event_json FROM journal WHERE processed=0 ORDER BY sequence LIMIT ?', (limit,))
        return [(r['sequence'], EventEnvelope.model_validate_json(r['event_json'])) for r in rows]

    async def checkpoint(self, scope):
        rows = await asyncio.to_thread(self._query, 'SELECT state_json FROM checkpoints WHERE scope=?', (scope,))
        return json.loads(rows[0]['state_json']) if rows else None

    async def commit_analysis(self, sequence, event, state, alerts=(), findings=(), signals=(), errors=()):
        await asyncio.to_thread(self._commit_analysis, sequence, event, state, alerts, findings, signals, errors)

    def _commit_analysis(self, sequence, event, state, alerts, findings, signals, errors):
        with self._lock:
            self._conn.execute('BEGIN IMMEDIATE')
            self._transaction = True
            try:
                row = self._conn.execute('SELECT processed FROM journal WHERE sequence=?', (sequence,)).fetchone()
                if row is None or row['processed']:
                    self._conn.rollback()
                    return
                self._insert_event(event)
                for alert in alerts:
                    self._insert_alert(alert)
                for signal in signals:
                    self._insert_detector_signal(signal, event)
                for finding in findings:
                    old = self._conn.execute('SELECT status,data_json FROM findings WHERE finding_id=?', (finding.finding_id,)).fetchone()
                    if old:
                        previous = json.loads(old['data_json'])
                        finding.status = old['status']
                        finding.first_seen = previous['first_seen']
                        finding.event_ids = sorted(set(finding.event_ids + previous['event_ids']))[-200:]
                        finding.occurrences = previous.get('occurrences',1) + 1
                    self._conn.execute('INSERT OR REPLACE INTO findings VALUES(?,?,?,?,?,?)',
                        (finding.finding_id, event.scope, finding.category, finding.status, str(finding.last_seen), self._json(finding.model_dump(mode='json'))))
                for plugin, error_type in errors:
                    self._conn.execute('INSERT INTO dead_letters(sequence,plugin,error_type,timestamp) VALUES(?,?,?,?)',
                        (sequence, plugin, error_type, event.observed_at.isoformat()))
                self._conn.execute('INSERT OR REPLACE INTO checkpoints VALUES(?,?,?,?)',
                    (event.scope, sequence, self._json(state), event.observed_at.isoformat()))
                self._conn.execute('UPDATE journal SET processed=1 WHERE sequence=?', (sequence,))
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise
            finally:
                self._transaction = False

    async def journal_events(self, scope=None, after=0, limit=1000):
        where, args = 'sequence>?', [after]
        if scope is not None:
            where += ' AND scope=?'
            args.append(scope)
        rows = await asyncio.to_thread(self._query,
            f'SELECT sequence,event_json FROM journal WHERE {where} ORDER BY sequence LIMIT ?', tuple(args+[limit]))
        return [dict(sequence=r['sequence'], event=json.loads(r['event_json'])) for r in rows]

    async def list_findings(self, scope=None, status=None, limit=100, offset=0):
        terms, args = [], []
        for field, value in [('scope',scope),('status',status)]:
            if value is not None:
                terms.append(field+'=?')
                args.append(value)
        where = ' WHERE '+' AND '.join(terms) if terms else ''
        rows = await asyncio.to_thread(self._query, 'SELECT data_json FROM findings'+where+' ORDER BY last_seen DESC,finding_id LIMIT ? OFFSET ?', tuple(args+[limit,offset]))
        return [json.loads(r['data_json']) for r in rows]

    async def update_finding(self, finding_id, status, actor):
        if status not in {'open','acknowledged','resolved','false_positive'} or not actor.strip():
            raise ValueError('Invalid finding status or actor')
        def update():
            with self._lock, self._conn:
                row = self._conn.execute('SELECT data_json FROM findings WHERE finding_id=?', (finding_id,)).fetchone()
                if row is None:
                    raise KeyError(finding_id)
                data=json.loads(row['data_json'])
                data['status']=status
                self._conn.execute('UPDATE findings SET status=?,data_json=? WHERE finding_id=?', (status,self._json(data),finding_id))
                self._conn.execute('INSERT INTO audit(timestamp,actor,operation,data_json) VALUES(?,?,?,?)',
                    (datetime.now(timezone.utc).isoformat(),actor,'finding.status',self._json(dict(finding_id=finding_id,status=status))))
                return data
        return await asyncio.to_thread(update)

    async def journal_metrics(self):
        def read():
            with self._lock:
                row=self._conn.execute('SELECT COUNT(*) total, SUM(processed=0) pending, MIN(CASE WHEN processed=0 THEN accepted_at END) oldest FROM journal').fetchone()
                return dict(accepted=row['total'], pending=row['pending'] or 0, oldest_pending=row['oldest'],
                            dead_letters=self._conn.execute('SELECT COUNT(*) FROM dead_letters').fetchone()[0])
        return await asyncio.to_thread(read)
