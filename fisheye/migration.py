"""Explicit copy-based migration. The source database is never modified."""

import sqlite3
from pathlib import Path

from fisheye.collectors.journal import JournalStore
from fisheye.privacy import capture_event
from fisheye.schema.events import EventEnvelope


async def migrate(source, destination=None, dry_run=True):
    source = Path(source).resolve()
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
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
        rows = store._query("SELECT * FROM events ORDER BY timestamp,event_id")
        for row in rows:
            event = EventEnvelope.model_validate(store._event_row_to_dict(row))
            await store.accept(capture_event(event))
        report.update(destination=str(destination), dry_run=False)
        return report
    finally:
        store.close()
