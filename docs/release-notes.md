# Fisheye releases

## 0.3.0

Fisheye Instant adds a local web control room for unified anomaly scoring and configurable responses. Install `fisheye[instant]` and launch `fisheye-instant --demo` to try the complete workflow. See the [setup guide](instant.md).

- The dashboard combines detector signals, continuous behavior measurements, and graph findings into a 0–100 score per workflow/environment. It exposes contributing evidence, recent history, detector coverage, stale data, and response outcomes. Duplicate observations in one channel do not accumulate.
- Warning, email, and host-registered shutdown rules support independent thresholds, cooldowns, and rearm margins. Score updates and queued responses share the analysis transaction. Rule changes cancel unclaimed old actions; interrupted external actions remain visible for manual outcome checks.
- SMTP delivery requires TLS and keeps passwords outside SQLite and API responses. Revision checks protect concurrent settings edits, and rejected updates restore the previous password. Shutdown callbacks are bound to a registered workflow/environment and receive an idempotency key.
- The responsive interface includes rule editing, email setup, connection examples, and a sample workflow processed by the real detectors. The existing investigation dashboard and API remain available. Remote CLI binding requires separate API and reviewer credentials.
- Continuous behavior measurements now reach the unified score below the legacy alert threshold; existing library alert thresholds remain unchanged. Coverage is reset for each analyzed event to avoid reporting a previous event's detector status.

### Local verification

| Check | Result |
| --- | --- |
| Python 3.12 with all four optional integrations | 140 passed; eight upstream Camel deprecation warnings |
| Python 3.10 and 3.13 core/server environments | 135 passed and five optional integration tests skipped on each |
| Ruff lint/format and JavaScript syntax | Passed |
| Wheel and source archive | Built using locally available dependencies |
| Fresh core/server wheel installs | Passed, including Instant assets, CLI and service lifecycle |
| API and response behavior | Authentication, revision conflicts, rollback, demo flow, TLS mail transport, shutdown scope, and interruption recovery passed |
| Offline regression corpus | Seven of seven assessed scenarios passed |
| Documentation and examples | Three Python snippets, both runnable examples, relative links and the copyable connection command passed |

The [validation record](validation/release-checks-0.3.0.json) records these local checks. The restricted environment required a temporary OS-pipe adapter for asyncio wake-ups; database transactions and worker threads ran normally. Chromium could not launch under the environment's process/IPC restrictions, so visual and browser interaction verification was not completed locally. A browser smoke job is defined in CI; no hosted CI execution is implied. Mail transport was exercised with a test SMTP implementation, without sending external email.

### Upgrade and operating limits

Stop the existing analyzer and back up the database before upgrading. Instant adds its own tables to the existing version 2 database. Score projections and responses begin with newly analyzed events; attaching Instant does not replay earlier events or take retroactive action. Keep one runtime/service owner per database and run the ASGI app with one worker.

Scores are inspectable heuristics, not calibrated probabilities. Email has no exactly-once guarantee, and real agent shutdown requires a host callback. The demo email action records a preview; the demo shutdown hook stops future sample runs. Action history has no automatic retention limit. This release does not establish new throughput results, a 24-hour soak result, distributed operation, or multi-tenant identity support.

## 0.2.2

This release adds an application-scoped audit reader and fixes interruption, capture, query, and replay behavior discovered after 0.2.1.

- `GET /v2/audit` and the `audit` CLI command expose ordered action/finding transitions with cursor pagination, workflow/environment filters, and exact operation filtering. Existing records acquire scope from retained actions/findings; unattributed records remain in SQLite and stay outside scoped reads. New records retain scope through event/finding retention.
- Supervised execution settles database claims before propagating cancellation and never starts a tool after a cancelled claim. Completion writes and JSONL exports keep ownership through repeated cancellation. Secondary completion-event failures preserve tool results/original exceptions and appear in supervision metrics.
- Failed workflow/task entry restores parent context. Workflows inherit the configured application, reject simultaneous reentry, and distinguish cancellation from task failure. Background maintenance reports failures, continues serving, and retries; failed periodic route flushes leave other routes running.
- Dataclass/Pydantic result capture detects cycles, bounds object depth, uses enum values, and avoids opaque key string conversions and custom model serializers. Extension fields are redacted before optional consumer delivery. Live first-occurrence evidence limits now match replay.
- Workflow/run lists preserve comma-containing agent names and have deterministic ordering on timestamp ties. CLI review mutations respect application scope. Unicode credentials compare safely, and empty credentials/application/producer configuration fails validation.
- Replay comparison uses stable finding identity when available, so new evidence and occurrence counts appear as changes to an existing finding. Duplicate identities in a report are rejected. Reports without finding IDs retain the legacy evidence-based matching rule.
- Package verification can select an exact wheel and install offline from a wheel directory; it checks packaged templates, version consistency, server startup/drain/shutdown, and evaluation outside the source checkout.

### Local verification

| Check | Result |
| --- | --- |
| Python 3.12 with all four optional integrations | 127 passed; eight upstream Camel deprecation warnings |
| Python 3.10 and 3.13 core/server environments | 122 passed and five optional integration tests skipped on each |
| Ruff lint and formatting | Passed across library, tests, examples, and tools |
| Wheel and source archive | Built using locally available build dependencies |
| Fresh core/server wheel installs | Passed offline, including the packaged server lifecycle |
| Offline regression corpus | Seven of seven assessed scenarios passed |
| Documentation and examples | Three README/supervision Python snippets, local workflow example, and relative links passed |

The [validation record](validation/release-checks-0.2.2.json) describes these checks. The restricted local environment blocks writes to asyncio's wake-up sockets, so the test runner used a temporary OS-pipe wake-up adapter. Database workers and tool threads ran normally. No hosted CI run or Windows verification is implied. These changes do not establish a new throughput measurement or complete the sustained-load/24-hour soak gates.

### Upgrade notes

Stop the existing analyzer before upgrading. Version 2 databases remain compatible; startup adds the audit scope column/indexes and backfills recoverable scopes. Make a backup before opening an existing database. Earlier releases can continue to read these databases, but their newly written audit rows acquire scope only after a later 0.2.2 reopen.

Set the CLI configuration's application explicitly when reviewing actions created under another application. Replace empty credential strings with a nonempty credential or `null` to disable that credential. Result previews may contain `[CIRCULAR]` or `[MAX_DEPTH]`; hosts must retain original execution arguments. Audit history is operational evidence and does not provide cryptographic tamper detection.

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
