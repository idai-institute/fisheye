# Fisheye 0.2.0

This release turns the event-monitoring prototype into a local multi-agent oversight library with an investigation and supervised-action workflow.

## Delivered

- Versioned event and relationship contracts, explicit workflow scopes, source links, agent instances and producer identities.
- Durable acceptance receipts, idempotent retries, conflict detection before redaction, atomic analysis/checkpoint batches, recoverable JSONL export, and isolated optional consumer queues.
- Correlated workflow findings for instruction propagation, sensitive data movement, delegation/wait cycles, duplicate work, task/output contracts, authority observations and aggregate resource usage.
- Corrected statistical scoring, bounded behavioral/plugin state, configurable warmup/frozen baselines, structured coverage, and an optional budgeted semantic evaluator contract.
- Trusted tool execution with policy checks, persistent human reviews, exact action digests, expiration, one-time claims, concurrent budget reservations, and measured-usage reconciliation.
- Protected investigation UI/API with graph, timeline, evidence, finding lifecycle, review inbox and health. Remote ingestion binds producer/application identity and bounds input size.
- Generic async/sync SDK, native LangChain/Agents SDK/Camel bridges, OpenTelemetry span conversion, offline replay/evaluation/comparison, configuration diagnostics and migration/export/retention commands.
- Minimal core dependencies, optional extras, a runnable local example, copy-based database migration, package smoke checks, and CI across Python 3.10–3.13.

## Validation

The release suite covers lifecycle/threaded callbacks, detector regressions, privacy/authentication, event conflicts, transaction rollback, abrupt process exit and restart, graph causality/isolation, plugin failures/expiry, budget/approval concurrency, API reviews, migration/retention, CLI recording/replay, and native integrations.

See [performance and evaluation](performance.md) for recorded load results and seven-scenario regression metrics, and [integration contracts](integrations.md) for exact tested versions and capabilities. Package checks install the wheel outside the checkout, first with core dependencies and then the server extra. README and supervision examples run without external credentials.

## Compatibility and operating boundary

Version 1 events, generic observation wrappers, and legacy query endpoints remain available throughout 0.2. Old runs preserve their identity without fabricated relationships. The default capture mode is now redacted across storage, evidence and exports. Review previews cannot reconstruct original secret arguments; hosts resuming an approved action must retain the original input securely. Follow the [migration guide](operations.md#upgrade-from-01) when moving an existing database.

Use one trusted team/application and one analysis runtime per SQLite database. Optional in-memory consumers are best effort; JSONL exports are at least once. Supervision governs only registered tools invoked through its boundary. Native callbacks do not enforce pause/resume or prevent direct tool access.

## Remaining roadmap

The [design proposal](overhaul-plan.md) includes broader goals than this release certifies. The functional local observation, investigation and supervision loop is implemented. Distributed analyzer coordination, cross-workflow authority/graph traversal, authenticated delegation grants, a general calibration dataset, a pluggable durable external exporter, and enterprise identity/tenancy remain future work.

The 1,000 events/s sustained target and 24-hour soak gate remain open. Graph checkpoints and queries are intended for bounded local workflows; the dashboard renders a limited graph excerpt. Audit/action history and unresolved findings can extend disk retention. Native integration testing establishes the capabilities in the matrix, not every framework lifecycle path. No production-scale or universal detection-accuracy claim is made.
