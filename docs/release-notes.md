# Fisheye releases

## 0.2.1

This maintenance release fixes completion and recovery races, makes live and replayed findings consistent, and improves investigation and supervision under malformed or out-of-order input.

- Runtime drain uses one deadline, waits for optional projection work, and preserves producer callbacks after a timeout. Failed optional routes remain isolated and visible in diagnostics. Shutdown cleans up after flush failures.
- A local operating-system lock prevents two analyzers from owning the same SQLite database. Ownership is released after startup failure, shutdown, or process exit; the analyzer polls for events accepted through other database connections. Cancellation waits for an active analysis transaction to finish before releasing ownership.
- Live analysis and replay share finding aggregation, environment identity, bounded evidence, agent attribution, and reopening rules. Replay checks event identity before redaction and reports coverage/configuration versions. Evaluation rejects incomplete coverage and processing errors, including otherwise benign scenarios.
- Causal analysis revisits affected actions and materializes ancestor paths only when needed. Iterative wait-cycle detection handles deep/shared dependencies. Late delegations preserve newer task status and recheck authority. Malformed optional relationships and nonfinite metric strings no longer stall analysis; quoted trust is interpreted consistently.
- Review filters apply before pagination, and direct detail queries find actions beyond the inbox page. Policies have immutable collections, malformed destinations receive explicit denials, current findings participate in the reservation transaction, and expired approvals produce one durable audit transition.
- Tool results are normalized before redaction without invoking arbitrary string representations. Capture failures preserve the actual terminal action status and expose a diagnostic. Investigation links support escaped identities and evidence outside the retained graph.

### Local verification

| Check | Result |
| --- | --- |
| Python 3.12 with all four optional integrations | 103 passed; eight upstream Camel deprecation warnings |
| Python 3.10 and 3.13 core/server environments | 98 passed and five optional integration tests skipped on each |
| Ruff lint and formatting | Passed across library, tests, examples, and tools |
| Wheel and source archive | Built; isolated core/server wheel checks passed |
| Offline regression corpus | Seven of seven assessed scenarios passed |
| Documentation and examples | README/supervision snippets, local workflow example, and relative links passed |

The [validation record](validation/release-checks-0.2.1.json) records these local checks; no hosted CI execution is implied. The [graph microbenchmark](performance.md#graph-traversal-in-021) improved by approximately 41 times for a 150-action linked read chain. It excludes storage, detectors, and exports and does not establish general throughput. The sustained-load and 24-hour soak gates remain open.

### Upgrade notes

Stop the existing analyzer before starting 0.2.1. Version 2 databases remain compatible; indexes are created on open. Keep the persistent `.analysis.lock` file in place, and create a new runtime after closing one. The ownership mechanism assumes a local filesystem and has been exercised on Linux; distributed filesystems and Windows ownership behavior are not certified by these checks.

New evidence can reopen resolved findings; false-positive labels remain until explicitly changed. Replay comparisons now include status, severity, evidence, and environment. Strict evaluation can turn previously incomplete "passes" into failures. Policy collections can no longer be changed in place; construct a new policy when rules change. Application scoping also applies to CLI review reads.

## 0.2.0

This release turns the event-monitoring prototype into a local multi-agent oversight library with an investigation and supervised-action workflow.

### Delivered

- Versioned event and relationship contracts, explicit workflow scopes, source links, agent instances and producer identities.
- Durable acceptance receipts, idempotent retries, conflict detection before redaction, atomic analysis/checkpoint batches, recoverable JSONL export, and isolated optional consumer queues.
- Correlated workflow findings for instruction propagation, sensitive data movement, delegation/wait cycles, duplicate work, task/output contracts, authority observations and aggregate resource usage.
- Corrected statistical scoring, bounded behavioral/plugin state, configurable warmup/frozen baselines, structured coverage, and an optional budgeted semantic evaluator contract.
- Trusted tool execution with policy checks, persistent human reviews, exact action digests, expiration, one-time claims, concurrent budget reservations, and measured-usage reconciliation.
- Protected investigation UI/API with graph, timeline, evidence, finding lifecycle, review inbox and health. Remote ingestion binds producer/application identity and bounds input size.
- Generic async/sync SDK, native LangChain/Agents SDK/Camel bridges, OpenTelemetry span conversion, offline replay/evaluation/comparison, configuration diagnostics and migration/export/retention commands.
- Minimal core dependencies, optional extras, a runnable local example, copy-based database migration, package smoke checks, and CI across Python 3.10–3.13.

### Validation

The release suite covers lifecycle/threaded callbacks, detector regressions, privacy/authentication, event conflicts, transaction rollback, abrupt process exit and restart, graph causality/isolation, plugin failures/expiry, budget/approval concurrency, API reviews, migration/retention, CLI recording/replay, and native integrations.

| Local verification | Result |
| --- | --- |
| Python 3.12 with all four optional integrations | 73 tests passed; eight upstream Camel deprecation warnings |
| Python 3.10 core/server development environment | 68 passed; five optional native integration tests skipped |
| Python 3.13 core/server development environment | 68 passed; five optional native integration tests skipped |
| Ruff lint and formatting | Passed across library, tests, examples and tools |
| Wheel and source archive | Built successfully; isolated core/server wheel checks passed |
| Offline corpus | Seven of seven assessed scenarios passed |
| Local examples and documentation | README/supervision examples, full local workflow example and relative links passed |

These are locally executed checks. The repository also defines CI jobs; no hosted CI run is implied by this report.

See [performance and evaluation](performance.md) for recorded load results and seven-scenario regression metrics, and [integration contracts](integrations.md) for exact tested versions and capabilities. Package checks install the wheel outside the checkout, first with core dependencies and then the server extra. README and supervision examples run without external credentials.

### Compatibility and operating boundary

Version 1 events, generic observation wrappers, and legacy query endpoints remain available throughout 0.2. Old runs preserve their identity without fabricated relationships. The default capture mode is now redacted across storage, evidence and exports. Review previews cannot reconstruct original secret arguments; hosts resuming an approved action must retain the original input securely. Follow the [migration guide](operations.md#upgrade-from-01) when moving an existing database.

Use one trusted team/application and one analysis runtime per SQLite database. Optional in-memory consumers are best effort; JSONL exports are at least once. Supervision governs only registered tools invoked through its boundary. Native callbacks do not enforce pause/resume or prevent direct tool access.

### Remaining roadmap

The [design proposal](overhaul-plan.md) includes broader goals than this release certifies. The functional local observation, investigation and supervision loop is implemented. Distributed analyzer coordination, cross-workflow authority/graph traversal, authenticated delegation grants, a general calibration dataset, a pluggable durable external exporter, and enterprise identity/tenancy remain future work.

The 1,000 events/s sustained target and 24-hour soak gate remain open. Graph checkpoints and queries are intended for bounded local workflows; the dashboard renders a limited graph excerpt. Audit/action history and unresolved findings can extend disk retention. Native integration testing establishes the capabilities in the matrix, not every framework lifecycle path. No production-scale or universal detection-accuracy claim is made.
