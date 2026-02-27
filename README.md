# fisheye

`fisheye` is an anomaly detection library for tool-calling agent systems.

It supports:
- Specific detectors: prompt injection, data exfiltration, and DoS
- Behavioral monitoring with online statistics
- Single-agent and multi-agent event flows
- REST API, CLI, and a simple dashboard

## Requirements

- Python 3.10+

## Install

```bash
cd dgs-final
python -m pip install -e .[dev]
```

## Run Tests

```bash
cd dgs-final
pytest -q
```

## CLI Usage

Show CLI help:

```bash
cd dgs-final
python -m fisheye.cli.main --help
```

Run API + dashboard:

```bash
cd dgs-final
python -m fisheye.cli.main serve --host 127.0.0.1 --port 8000
```

Generate synthetic attack scenarios:

```bash
cd dgs-final
python -m fisheye.cli.main demo attack-scenarios
```

List stored alerts from SQLite:

```bash
cd dgs-final
python -m fisheye.cli.main alerts list
```

## REST Endpoints

Base path: `/v1`

- `POST /v1/events`
- `GET /v1/alerts`
- `GET /v1/alerts/{alert_id}`
- `GET /v1/runs`
- `GET /v1/runs/{run_id}/events`
- `GET /v1/runs/{run_id}/alerts`
- `GET /v1/detectors`
- `GET /v1/health`
- `GET /v1/metrics`

Dashboard:
- `GET /`
- `GET /dashboard`

## Project Layout

- `fisheye/schema`: event and alert models
- `fisheye/preprocessors`: redaction, hashing, features, embeddings
- `fisheye/detectors`: rule detectors + score aggregation
- `fisheye/behavior`: behavioral anomaly monitoring
- `fisheye/collectors`: SQLite, JSONL, HTTP ingest service
- `fisheye/adapters`: generic, LangChain, Camel, OpenAI Agents SDK helpers
- `fisheye/api`: FastAPI app and routes
- `fisheye/cli`: command line entrypoints
- `fisheye/tests`: unit and integration tests
