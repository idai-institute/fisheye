# fisheye: Full Technical Specification (MVP)

## 1. Scope

`fisheye` is a Python library for anomaly detection in tool-calling agent systems (single-agent and multi-agent).

MVP detection combines:
- Specific detectors (rule-based first, model-ready later)
- Behavioral anomaly monitoring (online statistical baselines)

MVP produces alerts only. It does not block, terminate, or quarantine execution.

## 2. Confirmed Product Decisions

- Deliverable: library
- Language/runtime: Python `3.10+`
- Agent support: single-agent and multi-agent from day one
- Adapters: generic/custom, LangChain, Camel, OpenAI Agents SDK
- API model: async-first with sync wrappers
- Transport: in-process async bus + HTTP ingest collector in MVP
- Detection output: confidence score `0.0-1.0` + evidence payload
- Initial threats: prompt injection, data exfiltration, DoS
- Behavior monitor: statistical baselines (EWMA/z-score style)
- Storage: JSONL logs + SQLite indexed store
- Raw data allowed in dev; redaction not default-on
- Pluggability: preprocessors/collectors/detectors are plugin interfaces
- Interfaces: CLI + REST API + simple dashboard
- REST security mode: local/dev by default, no auth; API key optional

## 3. Goals and Non-Goals

### Goals
- Detect high-signal anomalies in real-time with low integration friction.
- Support heterogeneous agent frameworks with a shared event schema.
- Preserve enough evidence for triage while enabling preprocessing/privacy controls.
- Provide local operational UX (CLI, REST, dashboard) for development and evaluation.

### Non-Goals (MVP)
- Autonomous remediation/blocking.
- Full enterprise RBAC, SSO, multi-tenant isolation.
- Perfect semantic understanding of all prompt attacks.
- Distributed stream infrastructure (Kafka/NATS/Redis) as a hard dependency.

## 4. High-Level Architecture

Pipeline:

1. Adapter emits events from agent runtime.
2. Preprocessor chain derives/redacts/features events.
3. Async event bus fans out to collectors.
4. Collectors include:
   - JSONL logger
   - SQLite indexer
   - Specific detector engine
   - Behavior monitor engine
   - HTTP ingest collector (for remote producers)
5. Alert store and API expose findings to CLI/dashboard.

Design principle: any collector may consume from one or many agents/runs.

## 5. Package Layout (MVP)

```text
fisheye/
  adapters/
    base.py
    generic.py
    langchain.py
    camel.py
    openai_agents.py
  schema/
    events.py
    alerts.py
  preprocessors/
    base.py
    pipeline.py
    redaction.py
    hashing.py
    features.py
    urls.py
    buffering.py
    embeddings.py
  bus/
    async_bus.py
    routing.py
  collectors/
    base.py
    jsonl_logger.py
    sqlite_store.py
    http_ingest.py
  detectors/
    base.py
    engine.py
    prompt_injection.py
    exfiltration.py
    dos.py
    score_aggregation.py
  behavior/
    base.py
    online_stats.py
    monitor.py
  api/
    app.py
    routes.py
    dashboard.py
  cli/
    main.py
  sync/
    wrappers.py
  tests/
```

## 6. Core Data Model

Pydantic v2 models (strict typed envelopes, JSON-native payloads).

### 6.1 EventEnvelope

Required:
- `event_id: str` (UUID/ULID)
- `timestamp: datetime` (UTC ISO-8601)
- `event_type: str` (typed enum + extension namespace)
- `agent_id: str`
- `run_id: str`
- `payload: dict[str, Any]`

Optional:
- `trace_id: str | None`
- `span_id: str | None`
- `parent_span_id: str | None`
- `session_id: str | None`
- `framework: str | None` (generic/langchain/camel/openai_agents/...)
- `tags: dict[str, str]`
- `meta: dict[str, Any]`

### 6.2 Standard Event Types (MVP)

- `agent.start`
- `agent.stop`
- `agent.error`
- `llm.request`
- `llm.response`
- `llm.message`
- `tool.call.start`
- `tool.call.end`
- `tool.call.error`
- `state.update`
- `network.request`
- `file.read`
- `file.write`
- `custom.*` (extension)

### 6.3 Alert

- `alert_id: str`
- `timestamp: datetime`
- `agent_id: str`
- `run_id: str`
- `category: Literal["prompt_injection","data_exfiltration","dos","behavioral"]`
- `score: float` (`0.0-1.0`)
- `threshold: float`
- `triggered: bool` (`score >= threshold`)
- `sources: list[str]` (detector IDs contributing)
- `evidence: dict[str, Any]` (matched rules, event ids, aggregates, snippets/features)
- `related_event_ids: list[str]`

## 7. Adapter Layer

### 7.1 Adapter Contract

Each adapter must:
- Extract `agent_id` and `run_id` (required)
- Emit schema-valid `EventEnvelope` objects
- Preserve timing and causality where available
- Expose explicit integration hooks (no monkeypatch required)

### 7.2 Supported Adapters (MVP)

- `GenericAdapter`: SDK-style helper API for custom frameworks
- `LangChainAdapter`: callbacks/hooks mapping chain/tool/LLM events
- `CamelAdapter`: message/tool lifecycle mapping
- `OpenAIAgentsAdapter`: run/step/tool events mapping

If framework metadata is missing, adapters still emit minimal valid envelopes.

## 8. Preprocessing Layer

Preprocessors are ordered, configurable, and pluggable.

### 8.1 Built-in Preprocessors (MVP)

- `BufferingPreprocessor`: windowed buffering/release (time/count)
- `SecretRedactionPreprocessor`: regex/pattern masking for keys/tokens
- `PIIRedactionPreprocessor`: basic email/phone/SSN-like masking
- `HashFingerprintPreprocessor`: stable digests for selected fields
- `FeatureExtractionPreprocessor`: character length, token estimates, counts
- `URLDomainExtractionPreprocessor`: outbound URL/domain extraction
- `EmbeddingPreprocessor`: pluggable embedding provider interface (optional execution)

### 8.2 Processing Modes

- Raw pass-through
- Redacted event shadow
- Feature-only shadow

A route can deliver different modes to different collectors.

## 9. Event Bus and Routing

### 9.1 In-Process Bus

- `asyncio` pub/sub fanout
- Backpressure handling via bounded queues
- Collector isolation so slow consumers do not stop whole pipeline
- Per-collector retry policy with drop/error accounting

### 9.2 HTTP Ingest (MVP)

REST endpoint receives event envelopes and publishes to in-process bus.
- Batching supported
- Optional API key
- Intended for local/dev and simple remote producers

## 10. Detector System

### 10.1 Detector Plugin Interface

Each detector defines:
- `detector_id`
- `supported_event_types`
- `analyze(event, context) -> DetectorSignal | None`

`DetectorSignal` includes:
- `category`
- `score (0.0-1.0)`
- `evidence`
- `related_event_ids`

### 10.2 Built-in Detectors (MVP)

#### Prompt Injection Detector

Signals from:
- Instruction-override phrases (ignore rules, reveal secrets, bypass policy)
- Role confusion markers (pretend/system/developer override prompts)
- Suspicious sequencing (injection text followed by privileged tool calls)

Evidence:
- Matched pattern IDs and text spans (configurable truncation)
- Sequence context (event IDs, tool invoked, timing delta)

#### Data Exfiltration Detector

Signals from:
- Sensitive pattern detection (API keys, credentials, high-entropy strings)
- Outbound channels (`network.request`, `file.write`, tool outputs)
- Volume/context anomalies (large outbound payload after secret exposure)

Evidence:
- Sensitive artifact type, destination domain/path, payload size stats

#### DoS Detector

Signals from:
- Tool-call bursts beyond configured rate
- Tight loops/repeated calls with low variance
- Token explosion in prompts/responses
- Error storms/retry storms

Evidence:
- Rolling rate metrics, repetitive signature counts, time-window summaries

### 10.3 Score Aggregation

When multiple detector signals target the same alert window, combine with weighted noisy-or:

`combined = 1 - Π(1 - w_i * s_i)` clipped to `[0,1]`

- Default `w_i = 1.0`
- Per-detector weights configurable
- Threshold applied on combined score

## 11. Behavioral Monitoring

Behavior monitor maintains online baselines per:
- `agent_id`
- `agent_id + tool_name` (when available)

Metrics:
- Tool-call rate
- Latency (LLM/tool)
- Token usage estimates
- Tool distribution drift
- Error rate

Method:
- EWMA mean/variance tracking
- z-score style deviations
- windowed drift checks for categorical distributions

Output:
- Behavioral alert with score and metric-level evidence.

## 12. Storage and Query

### 12.1 JSONL Logger

- Append-only event logging to rotating JSONL files
- Optional separate stream for alerts

### 12.2 SQLite Store

Tables:
- `events`
- `alerts`
- `detector_signals`
- `behavior_stats`
- `runs`

Indexes:
- `(timestamp)`
- `(agent_id, run_id, timestamp)`
- `(category, score, timestamp)`

Purpose:
- Fast local querying for CLI/API/dashboard
- Durable local state for analysis and replay

## 13. REST API (FastAPI)

Base path: `/v1`

Endpoints (MVP):
- `POST /events` (single/batch ingest)
- `GET /alerts`
- `GET /alerts/{alert_id}`
- `GET /runs`
- `GET /runs/{run_id}/events`
- `GET /runs/{run_id}/alerts`
- `GET /detectors`
- `GET /health`
- `GET /metrics` (optional Prometheus text format if enabled)

Security:
- Default: no auth for local/dev
- Optional: static API key via header

## 14. CLI

Binary: `fisheye`

Commands:
- `fisheye serve` (REST + dashboard + pipeline runtime)
- `fisheye ingest <file.jsonl>` (replay events)
- `fisheye alerts list`
- `fisheye alerts tail`
- `fisheye runs list`
- `fisheye detectors list`
- `fisheye demo attack-scenarios` (synthetic prompt injection/exfiltration/dos)

## 15. Dashboard

Simple server-rendered dashboard (FastAPI + templates, minimal JS).

Views:
- Alert feed (score, category, agent/run)
- Alert detail (evidence, event links)
- Run timeline
- Detector health/volume summary

MVP favors readability and triage speed over customization.

## 16. Configuration

Configuration sources:
- Python API config object
- Environment variables
- Optional TOML/YAML file

Config domains:
- Enabled adapters/detectors/preprocessors
- Thresholds and weights
- Storage paths
- API host/port/auth
- Queue sizes and retry policy

## 17. Sync and Async APIs

Core runtime is async-first.

Sync wrapper layer provides:
- simplified `start/stop/publish` methods
- internal event loop management for non-async callers

This keeps integration easy in synchronous applications without duplicating logic.

## 18. Error Handling and Reliability

- Invalid event schema: reject with structured error + counter increment
- Collector failure: isolate, retry (configurable), then dead-letter or drop with audit log
- SQLite backpressure/failure: degrade gracefully to JSONL-only mode if configured
- HTTP ingest overload: `429` with retry hint

## 19. Performance Targets (MVP)

Local single-node targets:
- P50 ingest-to-alert latency < 200 ms for rule detectors
- Sustained throughput: at least 1,000 events/sec on commodity developer hardware
- No single collector can stall global ingestion path

These are engineering targets, not strict SLO guarantees.

## 20. Testing Strategy

### 20.1 Unit Tests

- Schema validation
- Preprocessor correctness
- Detector rule matching/scoring
- Aggregation math
- Behavior monitor statistics
- SQLite CRUD/query logic

### 20.2 Integration Tests

- End-to-end pipeline: adapter -> preprocess -> bus -> collectors
- HTTP ingest -> detection -> alert retrieval
- Multi-agent correlation (`agent_id`, `run_id`)

### 20.3 Synthetic Attack Scenarios

- Prompt injection scenario produces alert with evidence
- Exfiltration scenario produces alert with evidence
- DoS scenario produces alert with evidence
- Confidence scores are in `[0,1]` and thresholding works

## 21. Acceptance Criteria (Phase 3 “Works”)

Phase 3 is accepted when:
- Library installs and runs on Python `3.10+`
- Single-agent and multi-agent flows function end-to-end
- Generic + LangChain + Camel + OpenAI Agents SDK adapters are present (MVP-level)
- Prompt injection, exfiltration, and DoS detectors generate alerts on synthetic cases
- Alerts include score + evidence payload + related event IDs
- JSONL logging and SQLite querying both function
- CLI, REST API, and dashboard all expose alert/runs flow
- Test suite (unit + integration + synthetic scenarios) passes

## 22. Out-of-Scope Extensions (Post-MVP)

- Online/continual ML detector training loops
- Distributed brokers and horizontal scaling primitives
- Policy-driven active response/blocking
- Enterprise authN/authZ and tenant isolation
- Advanced privacy regimes and compliance packs
