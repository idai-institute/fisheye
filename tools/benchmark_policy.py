"""Separate deterministic policy evaluation from durable proposal latency."""

import argparse
import asyncio
import json
import platform
import tempfile
import time
from pathlib import Path

from benchmark import quantiles

from fisheye import FisheyeConfig, build_default_runtime
from fisheye.policies import Action, Policy


async def measure(count):
    policy = Policy(allowed_tools={"send"}, allowed_destinations={"approved.example"}, max_calls=count + 1)
    actions = [
        Action(
            workflow_id="policy-benchmark", agent_id="worker", tool_name="send", destination="https://approved.example"
        )
        for _ in range(count)
    ]
    evaluation = []
    for action in actions:
        start = time.perf_counter()
        assert policy.evaluate(action)[0] == "allow"
        evaluation.append((time.perf_counter() - start) * 1000)
    proposals = []
    with tempfile.TemporaryDirectory(prefix="fisheye-policy-") as directory:
        root = Path(directory)
        config = FisheyeConfig.from_dict(
            {
                "storage": {
                    "sqlite_path": str(root / "events.db"),
                    "events_jsonl_path": str(root / "events.jsonl"),
                    "alerts_jsonl_path": str(root / "alerts.jsonl"),
                }
            }
        )
        async with build_default_runtime(config) as runtime:
            supervisor = runtime.supervise(policy)
            for action in actions:
                start = time.perf_counter()
                assert (await supervisor.propose(action)).decision == "allow"
                proposals.append((time.perf_counter() - start) * 1000)
    return dict(
        actions=count,
        python=platform.python_version(),
        architecture=platform.machine(),
        evaluation_ms=quantiles(evaluation),
        durable_proposal_ms=quantiles(proposals),
        external_tools_executed=0,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--actions", type=int, default=100)
    parser.add_argument("--output")
    args = parser.parse_args()
    if args.actions < 1:
        parser.error("actions must be positive")
    encoded = json.dumps(asyncio.run(measure(args.actions)), indent=2)
    if args.output:
        Path(args.output).write_text(encoded + "\n")
    else:
        print(encoded)
