"""Explicit copy-based migration. The source database is never modified."""

import json
import sqlite3
from pathlib import Path

from fisheye.collectors.journal import JournalStore
from fisheye.privacy import capture_event
from fisheye.schema.events import EventEnvelope


async def migrate(source, destination=None, dry_run=True):
    source = Path(source).resolve()
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version > 2:
            raise ValueError("Database schema is newer than this library")
        count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        report = dict(source=str(source), source_version=version, target_version=2, events=count, dry_run=dry_run)
        if dry_run:
            return report
        if not destination:
            raise ValueError("A new destination is required")
        destination = Path(destination).resolve()
        if destination.exists() or destination == source:
            raise ValueError("Migration destination must not exist")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(destination) as out:
            conn.backup(out)
    store = JournalStore(destination)
    try:
        if version == 2:
            report.update(destination=str(destination), dry_run=False, existing_journal_preserved=True)
            return report
        rows = store._query("SELECT * FROM events ORDER BY timestamp,event_id")
        for row in rows:
            event = EventEnvelope.model_validate(store._event_row_to_dict(row))
            await store.accept(capture_event(event), identity_event=event)
        # Sanitize the copied legacy projections as well as newly accepted events.
        with store._lock, store._conn:
            for table, columns in {
                "events": ["tags_json", "meta_json", "payload_json"],
                "alerts": ["sources_json", "evidence_json", "related_event_ids_json"],
                "detector_signals": ["evidence_json"],
            }.items():
                for row in store._conn.execute(f"SELECT rowid AS migration_rowid,* FROM {table}").fetchall():
                    values = [
                        store._json(json.loads(row[column])) if row[column] else row[column] for column in columns
                    ]
                    store._conn.execute(
                        f"UPDATE {table} SET " + ",".join(column + "=?" for column in columns) + " WHERE rowid=?",
                        values + [row["migration_rowid"]],
                    )
        with store._lock:
            store._conn.execute("VACUUM")
            store._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        report.update(destination=str(destination), dry_run=False)
        return report
    finally:
        store.close()
