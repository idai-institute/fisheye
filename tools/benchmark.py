"""Reproducible local load probe. Longer release gates use --events/--rate."""

import argparse
import asyncio
import json
import platform
import tempfile
import time
from pathlib import Path

from fisheye import FisheyeConfig, build_default_runtime
from fisheye.collectors.base import Collector


class Counter(Collector):
    name = "benchmark-counter"

    def __init__(self):
        self.count = 0

    async def handle_event(self, event):
        self.count += 1


def quantiles(values):
    values = sorted(values)
    return {
        name: round(values[min(int((len(values) - 1) * p), len(values) - 1)], 3) if values else 0
        for name, p in [("p50", 0.5), ("p95", 0.95), ("p99", 0.99)]
    }


async def measure(args):
    with tempfile.TemporaryDirectory(prefix="fisheye-benchmark-") as directory:
        root = Path(directory)
        cfg = FisheyeConfig.from_dict(
            {
                "storage": {
                    "sqlite_path": str(root / "events.db"),
                    "events_jsonl_path": str(root / "events.jsonl"),
                    "alerts_jsonl_path": str(root / "alerts.jsonl"),
                }
            }
        )
        acceptance, analysis, latency, projection_latency = [], [], [], []
        starts = {}
        runtime = build_default_runtime(cfg)
        counters = [Counter() for _ in range(4)]
        for c in counters:
            runtime.register_collector(c, mode="feature_only")
        original = runtime.analysis.analyze

        async def instrumented(event, checkpoint=None):
            started = time.perf_counter()
            result = await original(event, checkpoint)
            analysis.append((time.perf_counter() - started) * 1000)
            latency.append((time.perf_counter() - starts[event.event_id]) * 1000)
            return result

        runtime.analysis.analyze = instrumented
        original_commit = runtime.store.commit_analysis_batch

        async def committed(items):
            await original_commit(items)
            for item in items:
                projection_latency.append((time.perf_counter() - starts.pop(item[1].event_id)) * 1000)

        runtime.store.commit_analysis_batch = committed
        rss = []

        def sample_memory():
            if Path("/proc/self/status").exists():
                status = Path("/proc/self/status").read_text()
                rss.append(int(next(line.split()[1] for line in status.splitlines() if line.startswith("VmRSS:"))))

        async def sample_until_closed():
            while True:
                sample_memory()
                await asyncio.sleep(0.1)

        sampler = asyncio.create_task(sample_until_closed())
        async with runtime:
            started = time.perf_counter()
            batch = []
            for i in range(args.events):
                if args.rate:
                    await asyncio.sleep(max(0, started + i / args.rate - time.perf_counter()))
                event_id = f"bench-{i}"
                batch.append(
                    dict(
                        event_id=event_id,
                        schema_version="2",
                        event_type="llm.message",
                        agent_id=f"agent-{i % 4}",
                        run_id=f"run-{i // args.workflow_size}",
                        workflow_id=f"w-{i // args.workflow_size}",
                        payload={"content": "A normal message. " * 120, "trust": "trusted"},
                    )
                )
                if len(batch) >= args.batch_size or i == args.events - 1:
                    tick = time.perf_counter()
                    starts.update({event["event_id"]: tick for event in batch})
                    await runtime.ingest(batch)
                    acceptance.append((time.perf_counter() - tick) * 1000)
                    batch = []
                    sample_memory()
            admitted = time.perf_counter() - started
            await runtime.drain(args.timeout)
            elapsed = time.perf_counter() - started
            metrics = await runtime.store.journal_metrics()
            report = dict(
                python=platform.python_version(),
                platform=platform.system(),
                architecture=platform.machine(),
                events=args.events,
                workflow_size=args.workflow_size,
                batch_size=args.batch_size,
                consumers=4,
                payload_bytes=2040,
                offered_rate=args.rate,
                admission_seconds=round(admitted, 3),
                completion_seconds=round(elapsed, 3),
                completed_events_per_second=round(args.events / elapsed, 2),
                batch_acceptance_ms=quantiles(acceptance),
                analysis_ms=quantiles(analysis),
                publish_to_analysis_ms=quantiles(latency),
                publish_to_projection_ms=quantiles(projection_latency),
                sampled_peak_rss_kib=max(rss) if rss else None,
                journal=metrics,
                consumer_counts=[c.count for c in counters],
                delivery=runtime.metrics,
            )
        sampler.cancel()
        await asyncio.gather(sampler, return_exceptions=True)
        return report


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--events", type=int, default=1000)
    p.add_argument("--workflow-size", type=int, default=10)
    p.add_argument("--batch-size", type=int, default=1)
    p.add_argument("--rate", type=float, default=0)
    p.add_argument("--timeout", type=float, default=300)
    p.add_argument("--output")
    args = p.parse_args()
    if min(args.events, args.workflow_size, args.batch_size) < 1 or args.rate < 0:
        p.error("Counts must be positive and rate nonnegative")
    report = asyncio.run(measure(args))
    encoded = json.dumps(report, indent=2)
    if args.output:
        Path(args.output).write_text(encoded + "\n")
    else:
        print(encoded)
