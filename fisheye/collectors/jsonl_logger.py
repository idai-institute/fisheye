from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fisheye.collectors.base import AlertSink, Collector
from fisheye.privacy import redact
from fisheye.schema.alerts import Alert
from fisheye.schema.events import EventEnvelope


class JsonlLoggerCollector(Collector, AlertSink):
    name = "jsonl_logger"

    def __init__(
        self,
        events_path: str | Path,
        alerts_path: str | Path | None = None,
        rotate_bytes: int = 10 * 1024 * 1024,
        redact_content: bool = True,
    ) -> None:
        self.events_path = Path(events_path)
        self.alerts_path = Path(alerts_path) if alerts_path else None
        self.rotate_bytes = rotate_bytes
        self.redact_content = redact_content
        self._lock = asyncio.Lock()
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        if self.alerts_path:
            self.alerts_path.parent.mkdir(parents=True, exist_ok=True)

    async def _write_line(self, path: Path, payload: dict) -> None:
        line = (
            json.dumps(redact(payload) if self.redact_content else payload, default=str, separators=(",", ":")) + "\n"
        )
        async with self._lock:
            await asyncio.to_thread(self._rotate_if_needed, path)
            await asyncio.to_thread(self._append_line, path, line)

    def _rotate_if_needed(self, path: Path) -> None:
        if not path.exists() or path.stat().st_size < self.rotate_bytes:
            return
        target = path.with_suffix(path.suffix + ".1")
        if target.exists():
            target.unlink()
        path.rename(target)

    def _append_line(self, path: Path, line: str) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)

    async def handle_event(self, event: EventEnvelope) -> None:
        await self._write_line(self.events_path, event.model_dump(mode="json"))

    async def handle_alert(self, alert: Alert) -> None:
        if self.alerts_path is None:
            return
        await self._write_line(self.alerts_path, alert.model_dump(mode="json"))
