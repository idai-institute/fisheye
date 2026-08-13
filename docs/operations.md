# Operations, API and migration

## Configuration and serving

Start with [local.toml](../examples/local.toml). `FisheyeConfig.load()` and the CLI apply defaults, file, environment, then explicit overrides. Environment keys use `FISHEYE__SECTION__FIELD`; JSON literals work for numbers, booleans, and collections. `FisheyeConfig()` itself uses defaults without loading a file/environment.

Core defaults: 100,000 pending events, 1,000 slots per optional consumer, one consumer retry, 30-day event retention, 24-hour inactive checkpoint TTL, and a 2,000-event graph. Thresholds and behavioral warmup/freeze controls are configurable. Inspect the effective configuration with `fisheye doctor --effective-config`; its hash is attached to findings.

The CLI binds to loopback by default and requires an API key for remote binding. Set `FISHEYE__API__API_KEY` and, for mutations, a different `FISHEYE__API__REVIEW_API_KEY`. The API accepts `X-API-Key`; browsers can use HTTP Basic with any username and the API key as password. Deploy remote access behind TLS. One configured application/team is the authorization boundary, not a multi-tenant identity system.

Use one server worker per database. Native adapters and host code may run in that process, or remote producers may submit events over HTTP. Raw capture is explicit and applies to all data stored by that runtime. Turning redaction on does not rewrite existing raw history.

Starting a second analyzer against the same local database now fails immediately. Its persistent `.analysis.lock` file is harmless when no process holds the lock; do not remove it to bypass ownership. Startup failures release ownership and stop workers. A closed runtime cannot be restarted; construct a new runtime after `close()`/`aclose()`.

## API

Data endpoints and dashboards require the configured API credential. `/v1/health` exposes minimal liveness without credentials; `/v2/health` includes protected diagnostics. OpenAPI schemas are available at `/docs`.

| Endpoint | Purpose |
| --- | --- |
| `POST /v1/events` | Accept one envelope or up to 1,000; return durable per-event receipts |
| `GET /v1/alerts`, `/v1/alerts/{id}` | Legacy alert access scoped to configured application |
| `GET /v1/runs`, `/v1/runs/{id}/events`, `/v1/runs/{id}/alerts` | Legacy run queries |
| `GET /v1/detectors`, `/v1/metrics` | Detector and delivery diagnostics |
| `GET /v2/workflows` | Workflow list with limit/offset |
| `GET /v2/workflows/{id}/graph` | Bounded graph, tasks and usage |
| `GET /v2/workflows/{id}/events` | Captured events with `after` sequence cursor |
| `GET /v2/events/{id}` | Retrieve one retained event by its exact identity |
| `GET /v2/findings?workflow_id=...` | Findings with status/limit/offset |
| `PATCH /v2/findings/{id}` | Set lifecycle status; requires reviewer credential |
| `GET /v2/reviews`, `GET /v2/reviews/{id}`, `POST /v2/reviews/{id}` | Page through scoped reviews, inspect one action, or decide its exact digest |
| `GET /v2/health` | Journal backlog, coverage, errors, consumer metrics and config hash |
| `GET /v2/audit` | Application-scoped action/finding transitions with an `after` ID cursor |
| `GET /`, `/dashboard`, `/workflows/{id}` | Investigation UI |

Workflow queries accept `environment`, defaulting to `local`. Remote application and producer IDs are bound to server configuration. Internal detector annotations supplied by remote producers are discarded. An event is limited to 256 KiB; request bodies default to 2 MiB. Invalid input returns 422, oversized requests 413, identity conflicts 409, and backlog overload 429. A 409/429 may include earlier accepted receipts. A 202 still acknowledges acceptance and indicates processing is pending/degraded.

Workflow and event identities may contain slashes, spaces, or URL-reserved characters; URL-encode identities when building links. Investigation evidence links jump to the visible timeline entry or retrieve the captured event when it is outside the retained graph. Event lookup uses the same application scope and authentication as workflow queries. Pruned events return 404; a finding's evidence summary remains available.

Finding mutations accept `{"status":"resolved"}` (also `open`, `acknowledged`, `false_positive`). Reviews accept `{"action_digest":"...","approve":true}`. Both require `X-Review-Key` as well as API authentication when configured. Review routes require a runtime supervisor or CLI `serve --policy ...`.

Review lists accept `limit`, `offset`, `status`, and optional `workflow_id`/`environment`. Application and workflow filters are applied before pagination. A direct action lookup remains available even when the item is beyond the current inbox page. CLI equivalents are `reviews list --limit 50 --offset 50 --status pending` and `reviews show --action-id ID`, with the usual `--policy` argument. CLI reads and review decisions use the configured application scope. Expired reviews remain inspectable under `status=expired`; attempting to approve a stale review returns 409.

Audit queries accept `workflow_id`/`environment`, an exact `operation` such as `action.reviewed`, `limit` (1–1,000), and `after` (the last audit ID consumed). Responses contain `items` and `next_cursor`; use the latter as the next `after` value. Filtering happens before pagination. The CLI exposes the same query as `fisheye --config local.toml audit --workflow-id WORKFLOW --after 0 --limit 100`. Audit reads do not require a policy file or reviewer credential. They use the configured application and retain actor, timestamp, operation, scope, and captured transition data.

On open, existing audit rows acquire scope from their retained action/finding when available. Rows whose scope cannot be recovered remain in SQLite and are excluded from scoped queries. New rows retain scope independently of event/finding retention. This audit history is local operational evidence, not a tamper-evident ledger.

## Backup, export and retention

Use SQLite's backup API for a live database, or stop the runtime before taking a filesystem copy. Copying only a live `.db` file may omit WAL transactions. Backups include event identity keys, pending journal work, checkpoints, outbox entries, findings, action approvals, and audit records.

`fisheye --db events.db export recording.jsonl` creates a new file and refuses to overwrite an existing one. Exports contain captured events with journal sequence wrappers, accepted by `record` and `replay`. JSONL logs rotate independently and are not full backups.

`fisheye --db events.db prune` applies configured retention. Maintenance also runs every minute while the runtime is active. Pending events and evidence linked to open/acknowledged findings survive event pruning. Inactive checkpoints expire independently. Resolved findings, action/review audit history, and outstanding export entries can extend disk use; there is no hard disk quota or archive backend. Monitor available space and backlog, archive before removing history, and resolve findings according to your team's evidence policy.

Important diagnostics are pending count/oldest pending time, analysis/export errors, dead-letter count, plugin coverage, optional consumer queued/dropped/errors, and graph truncation. A sink failure leaves its outbox pending while analysis can continue. `drain()` makes that degradation visible to callers. Investigate plugin errors before treating absent findings as clean runs.

## Upgrade from 0.1

1. Stop writes to the old deployment, back up its database, and keep the original package/configuration available for rollback.
2. Inspect the migration report: `fisheye migrate old.db` (dry run; no destination written).
3. Apply to a new file: `fisheye migrate old.db --destination upgraded.db --apply`.
4. Start 0.2 with `--db upgraded.db`; startup processes imported events and reconstructs analysis. Inspect findings and export/replay representative workflows before enabling supervision.
5. Roll back by stopping 0.2 and restoring the old deployment against its untouched original database. Events recorded only in 0.2 require a separate export/compatibility review; rollback does not merge histories.

Migration copies through SQLite backup and never edits the source. A legacy copy receives redacted projections and journal entries; its original run grouping remains intact, without invented agent relationships. Version 2 copies preserve their existing journal, capture contents, scopes, and identity key. Future schema versions are rejected. If conversion fails, keep the source and discard the incomplete destination before retrying.

Redacted storage is the new default, including findings, action previews, and checkpoints. Existing v2 raw data remains raw when copied. Version 1 event input, generic wrappers, and legacy `/v1` query endpoints remain supported throughout 0.2; no removal occurs in this release.
