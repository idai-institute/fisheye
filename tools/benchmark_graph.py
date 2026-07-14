"""Measure linked read actions in memory; excludes journal and detector work."""

import argparse
import json
import platform
import statistics
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from fisheye.graph.monitor import WorkflowGraph
from fisheye.schema.events import EventEnvelope

ROOT = Path(__file__).resolve().parents[1]


def revision(value):
    return subprocess.check_output(
        ["git", "rev-parse", "--verify", "--end-of-options", value + "^{commit}"], cwd=ROOT, text=True
    ).strip()


def measure(graph_class, events, samples):
    durations = []
    for _ in range(samples):
        graph = graph_class(max_events=max(2000, len(events)))
        state = {}
        started = time.perf_counter()
        for event in events:
            assert not graph.process(event, state), "Benign read chain unexpectedly produced findings"
        durations.append(round(time.perf_counter() - started, 6))
        assert len(state["nodes"]) == len(events)
    return durations


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=int, default=150)
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--baseline", help="Load the graph module from a trusted local Git revision")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if min(args.events, args.samples) < 1:
        parser.error("Events and samples must be positive")
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    events = [
        EventEnvelope(
            event_id=f"read-{i}",
            event_type="tool.call.start",
            agent_id="reader",
            run_id="chain",
            workflow_id="chain",
            timestamp=timestamp,
            observed_at=timestamp,
            links=[f"read-{i - 1}"] if i else [],
            payload={"tool_name": "read"},
        )
        for i in range(args.events)
    ]
    report = dict(
        workload="Linked read-tool starts in one workflow; graph only, no journal, detectors, exports or event validation",
        python=platform.python_version(),
        platform=platform.system(),
        architecture=platform.machine(),
        implementation_revision=revision("HEAD"),
        events=args.events,
        samples=args.samples,
        seconds={"updated": measure(WorkflowGraph, events, args.samples)},
    )
    if args.baseline:
        baseline = revision(args.baseline)
        source = subprocess.check_output(["git", "show", baseline + ":fisheye/graph/monitor.py"], cwd=ROOT, text=True)
        namespace = {"__name__": "fisheye_graph_baseline"}
        exec(compile(source, f"{baseline}:fisheye/graph/monitor.py", "exec"), namespace)
        report["baseline_revision"] = baseline
        report["baseline_scope"] = "Historical graph module with the current event and finding schemas"
        report["seconds"]["baseline"] = measure(namespace["WorkflowGraph"], events, args.samples)
        report["median_speedup"] = round(
            statistics.median(report["seconds"]["baseline"]) / statistics.median(report["seconds"]["updated"]), 2
        )
    encoded = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded)
    else:
        print(encoded, end="")


if __name__ == "__main__":
    main()
