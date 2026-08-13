from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
from itertools import islice
from pathlib import Path

from fisheye.config import FisheyeConfig
from fisheye.runtime import build_default_runtime


def _parser():
    parser = argparse.ArgumentParser(prog="fisheye", description="Observe, investigate, and supervise agent workflows.")
    parser.add_argument("--config", help="TOML or JSON configuration")
    parser.add_argument("--db", help="SQLite database path")
    parser.add_argument("--events-jsonl")
    parser.add_argument("--alerts-jsonl")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="Serve the investigation dashboard and API")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.add_argument("--policy", help="JSON policy enabling review endpoints")
    for name in ("ingest", "record"):
        p = sub.add_parser(name, help="Record events from JSONL without executing tools")
        p.add_argument("path")
    replay = sub.add_parser("replay", help="Analyze a recording offline")
    replay.add_argument("path")
    replay.add_argument("--output")
    ev = sub.add_parser("evaluate", help="Evaluate labeled offline scenarios")
    ev.add_argument("--corpus", help="JSON scenario array; defaults to regression corpus")
    ev.add_argument("--split", choices=["all", "test", "tune"], default="test")
    ev.add_argument("--output")
    compare = sub.add_parser("compare", help="Compare replay reports")
    compare.add_argument("before")
    compare.add_argument("after")
    inspect = sub.add_parser("inspect", help="Inspect a workflow graph and its findings")
    inspect.add_argument("workflow_id")
    inspect.add_argument("--environment", default="local")
    audit = sub.add_parser("audit", help="Read scoped action and finding transitions")
    audit.add_argument("--workflow-id")
    audit.add_argument("--environment", default="local")
    audit.add_argument("--after", type=int, default=0)
    audit.add_argument("--limit", type=int, default=100)
    audit.add_argument("--operation")
    export = sub.add_parser("export", help="Export journal events as JSONL")
    export.add_argument("path")
    doctor = sub.add_parser("doctor", help="Validate configuration and report installed integrations")
    doctor.add_argument("--effective-config", action="store_true")
    policy = sub.add_parser("policy", help="Validate an action policy")
    policy.add_argument("operation", choices=["validate"])
    policy.add_argument("path")
    reviews = sub.add_parser("reviews", help="List or decide persistent reviews")
    reviews.add_argument("operation", choices=["list", "show", "approve", "deny"])
    reviews.add_argument("--policy", required=True)
    reviews.add_argument("--action-id")
    reviews.add_argument("--digest")
    reviews.add_argument("--reviewer", default="operator")
    reviews.add_argument("--limit", type=int, default=100)
    reviews.add_argument("--offset", type=int, default=0)
    reviews.add_argument(
        "--status",
        default="pending",
        choices=["pending", "approved", "denied", "expired", "executing", "completed", "failed", "unknown"],
    )
    reviews.add_argument("--workflow-id")
    reviews.add_argument("--environment", default="local")
    for name in ("alerts", "runs", "detectors"):
        p = sub.add_parser(name)
        p.add_argument("operation", choices=["list", "tail"] if name == "alerts" else ["list"])
        if name == "alerts":
            p.add_argument("--interval", type=float, default=1)
    demo = sub.add_parser("demo", help="Run local oversight examples")
    demo.add_argument("scenario", choices=["attack-scenarios", "multi-agent"], default="multi-agent", nargs="?")
    migration = sub.add_parser("migrate", help="Copy and migrate an existing database")
    migration.add_argument("source")
    migration.add_argument("--destination")
    migration.add_argument("--apply", action="store_true")
    sub.add_parser("prune", help="Apply configured event and state retention")
    return parser


def _config(args):
    overrides = {}
    storage = {
        k: v
        for k, v in dict(
            sqlite_path=args.db, events_jsonl_path=args.events_jsonl, alerts_jsonl_path=args.alerts_jsonl
        ).items()
        if v is not None
    }
    if storage:
        overrides["storage"] = storage
    return FisheyeConfig.load(args.config, overrides)


def _output(data, path=None):
    value = json.dumps(data, indent=2, default=str)
    if path:
        Path(path).write_text(value + "\n")
    else:
        print(value)


async def _run(args, cfg):
    from fisheye.evaluation import compare, evaluate, replay
    from fisheye.evaluation.replay import load_events
    from fisheye.evaluation.scenarios import corpus
    from fisheye.policies import Policy, Supervisor
    from fisheye.runtime import build_analysis

    if args.command == "doctor":
        versions = {}
        for name in ["fisheye", "pydantic", "fastapi", "langchain-core", "openai-agents", "camel-ai"]:
            try:
                versions[name] = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                versions[name] = None
        _output(
            dict(
                status="ok",
                config_version=cfg.fingerprint,
                installed=versions,
                configuration=cfg.public_dict() if args.effective_config else None,
            )
        )
        return 0
    if args.command == "policy":
        policy = Policy.model_validate_json(Path(args.path).read_text())
        _output(dict(valid=True, version=policy.version, policy=policy.model_dump(mode="json")))
        return 0
    if args.command == "compare":
        _output(compare(json.loads(Path(args.before).read_text()), json.loads(Path(args.after).read_text())))
        return 0
    if args.command == "replay":
        report = await replay(
            load_events(args.path),
            build_analysis(cfg),
        )
        _output(report, args.output)
        return int(bool(report["errors"]))
    if args.command == "evaluate":
        scenarios = json.loads(Path(args.corpus).read_text()) if args.corpus else corpus()
        if args.split != "all":
            scenarios = [s for s in scenarios if s.get("split", "test") == args.split]
        if not scenarios:
            raise ValueError("No scenarios selected")
        report = await evaluate(
            scenarios,
            lambda: build_analysis(cfg),
        )
        _output(report, args.output)
        return int(report["passed"] != report["scenarios"])
    if args.command == "migrate":
        from fisheye.migration import migrate

        _output(await migrate(args.source, args.destination, not args.apply))
        return 0
    runtime = build_default_runtime(cfg)
    if args.command in {"record", "ingest", "demo"}:
        async with runtime:
            events = iter(load_events(args.path) if args.command != "demo" else corpus()[0]["events"])
            receipts = []
            while batch := list(islice(events, 100)):
                receipts.extend(await runtime.ingest(batch))
            await runtime.drain(30)
            _output(
                dict(
                    accepted=len(receipts),
                    duplicates=sum(r.duplicate for r in receipts),
                    findings=await runtime.store.list_findings(),
                )
            )
        return 0
    store = runtime.store
    try:
        if args.command == "audit":
            scope = (
                json.dumps([cfg.api.application_id, args.environment, args.workflow_id], separators=(",", ":"))
                if args.workflow_id is not None
                else None
            )
            rows = await store.audit_entries(
                cfg.api.application_id, scope=scope, after=args.after, limit=args.limit, operation=args.operation
            )
            _output(dict(items=rows, next_cursor=rows[-1]["id"] if rows else None))
        elif args.command == "inspect":
            scope = json.dumps([cfg.api.application_id, args.environment, args.workflow_id], separators=(",", ":"))
            _output(dict(graph=await store.workflow_graph(scope), findings=await store.list_findings(scope)))
        elif args.command == "export":
            cursor = 0
            with Path(args.path).open("x") as stream:
                while rows := await store.journal_events(after=cursor):
                    for row in rows:
                        stream.write(json.dumps(row) + "\n")
                    cursor = rows[-1]["sequence"]
            _output(dict(exported_through=cursor, path=args.path))
        elif args.command == "prune":
            _output(await store.prune(cfg.storage.retention_days, cfg.storage.state_ttl_seconds))
        elif args.command == "reviews":
            policy = Policy.model_validate_json(Path(args.policy).read_text())
            supervisor = Supervisor(store, policy)
            if args.operation == "list":
                scope = (
                    json.dumps([cfg.api.application_id, args.environment, args.workflow_id], separators=(",", ":"))
                    if args.workflow_id
                    else None
                )
                _output(
                    await supervisor.list_reviews(
                        args.status, args.limit, args.offset, application_id=cfg.api.application_id, scope=scope
                    )
                )
            elif args.operation == "show":
                if not args.action_id:
                    raise ValueError("--action-id is required")
                review = await supervisor.get_review(args.action_id, cfg.api.application_id)
                if review is None:
                    raise ValueError("Review not found")
                _output(review)
            else:
                if not args.action_id or not args.digest:
                    raise ValueError("--action-id and --digest are required")
                if await supervisor.get_review(args.action_id, cfg.api.application_id) is None:
                    raise ValueError("Review not found")
                _output(
                    (
                        await supervisor.review_id(
                            args.action_id, args.digest, args.operation == "approve", args.reviewer
                        )
                    ).model_dump(mode="json")
                )
        elif args.command == "runs":
            _output(await store.list_workflows())
        elif args.command == "detectors":
            _output(dict(configured=runtime.analysis.list_detector_ids(), observed=await store.list_detectors()))
        elif args.command == "alerts":
            if args.operation == "list":
                _output(await store.list_alerts())
            else:
                if args.interval <= 0:
                    raise ValueError("interval must be positive")
                seen = set()
                while True:
                    alerts = await store.list_alerts()
                    for a in reversed(alerts):
                        if a["alert_id"] not in seen:
                            print(json.dumps(a))
                    seen = {a["alert_id"] for a in alerts}
                    await asyncio.sleep(args.interval)
        return 0
    finally:
        store.close()


def main(argv=None):
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        cfg = _config(args)
        if args.command == "serve":
            import uvicorn

            from fisheye.api.app import create_app

            if args.host:
                cfg.api.host = args.host
            if args.port:
                cfg.api.port = args.port
            if cfg.api.host not in {"localhost", "127.0.0.1", "::1"} and not cfg.api.api_key:
                raise ValueError("Remote binding requires api.api_key")
            runtime = build_default_runtime(cfg)
            if args.policy:
                from fisheye.policies import Policy

                runtime.supervise(Policy.model_validate_json(Path(args.policy).read_text()))
            uvicorn.run(create_app(runtime), host=cfg.api.host, port=cfg.api.port)
            return 0
        return asyncio.run(_run(args, cfg))
    except KeyboardInterrupt:
        return 130
    except (ValueError, OSError, RuntimeError) as exc:
        from fisheye.privacy import redact

        parser.exit(2, f"fisheye: {redact(str(exc))}\n")


if __name__ == "__main__":
    raise SystemExit(main())
