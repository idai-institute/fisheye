# Fisheye Instant

A local web control room that combines anomaly signals into a single 0–100 score and runs your configured countermeasures.

## Start

```bash
python -m pip install -e '.[instant]'
fisheye-instant --demo
```

Open http://127.0.0.1:8000. Click **Run sample workflow** to see a researcher pass suspicious document instructions to a writer. The actual detectors and graph monitors process the sample. A warning rule at 60 is enabled by default. Demo emails are recorded as previews; no SMTP server is contacted for the `demo` environment. A registered demo shutdown hook stops further sample runs until the app restarts.

Without `--demo`, the page begins with connection instructions. Send events to `POST /v1/events`, use a Fisheye adapter, or embed the app with your existing runtime. Data goes to `instant-data/` by default. Use `--data-dir`, `--config`, `--host`, or `--port` to change the setup. The existing investigation dashboard remains at `/dashboard`, and API reference at `/docs`.

## Understand the score

Each workflow/environment has its own rolling five-minute window, based on local analysis time. Every detector signal contributes even when it is below the library's alert threshold. Behavior measurements (including measurements below alert thresholds) and graph finding categories also contribute; findings that merely repeat detector alerts are excluded. Repeated observations in one channel contribute only its highest score. The combination is:

`100 × (1 − product(1 − channel_peak))`

For example, independent channel scores of 0.6 and 0.5 produce 80. This is a heuristic, not a calibrated probability: correlated channels can reinforce one another. The UI shows every contribution and its captured evidence. Scores, history points, and response decisions are committed with the analysis transaction, so a failed projection rolls back the whole batch.

No recent data and incomplete detector coverage are shown separately from the number. A low score does not establish safety, particularly when checks are unavailable. The overview selects the highest-scoring recently observed workflow by default; it does not combine unrelated workflows into one shared risk value. Workflow selection shows a maximum of 200 recently updated workflows; chart history shows the latest 120 event samples within one day. New score projections begin when Instant is attached; existing processed events are not retroactively assigned new responses.

## Configure responses

In **Response rules**, add a name, threshold, and action. **Raise a warning** adds an entry to the activity feed. **Send an email** delivers to the rule's recipients through the SMTP settings. **Shut down an agent** invokes the host hook for the selected workflow/environment; it never runs a shell command or tries to kill an arbitrary process.

Each rule fires once when the threshold is reached. It rearms after the score falls below `threshold − rearm margin`; the cooldown must also have elapsed before another response. Quiet windows expire and allow a later incident to rearm. Changing, disabling, or deleting a rule cancels its queued actions; an action already claimed by the worker may finish. Warning/critical display bands (60/85) are independent of response thresholds.

Email requires a host, port, sender, and TLS mode; add username/password if the SMTP service requires authentication. The password is stored separately from SQLite in an owner-readable file beside the database, is excluded from Git, and is never returned by the settings API. Back it up separately when moving the app. Email content includes the workflow, rule and score, without raw agent messages or captured evidence.

Actions are claimed transactionally. A process interruption can leave an external outcome uncertain, so interrupted claims become **Check outcome** and are not automatically retried. Failed or blocked actions remain visible. Email has no exactly-once guarantee; provider-side idempotency and external reconciliation are host responsibilities. Shutdown callbacks receive an `idempotency_key` for the same reason. Demo emails show **Demo preview**.

## Connect a shutdown hook

```python
from fisheye import build_default_runtime
from fisheye_instant.app import create_app

runtime = build_default_runtime()

async def stop_agent(workflow_id, environment, idempotency_key):
    # Replace with your host's actual cancellation/termination operation.
    await my_agent.stop()

app = create_app(
    runtime=runtime,
    stop_handlers={("local", "my-workflow"): stop_agent},
)
```

Run this ASGI app with one server worker. Only registered workflow/environment pairs can be selected for shutdown. A hook should return after the host has requested or completed the stop; Fisheye cannot independently verify a provider's shutdown. The runnable [host example](../examples/instant_host.py) uses a cooperative stop signal. In-process hosts can cancel owned tasks; remote agents need a callback that reaches their host's control endpoint. Observation alone does not grant execution control.

## Access and operations

Loopback is the default. Remote CLI binding requires both `FISHEYE__API__API_KEY` and `FISHEYE__API__REVIEW_API_KEY`; put remote access behind TLS. The page accepts the API credential through HTTP Basic. Enter the separate reviewer key through **Access key** to change settings or run a sample. It is kept only in the tab's memory. Changes require JSON and reject cross-site browser origins. All Instant data is scoped to the runtime's configured application.

`GET /instant/api/snapshot` returns score contributions, coverage, history, and the latest 50 action records. `GET/PUT /instant/api/settings` reads or changes rules/email settings; writes require the last returned `revision`, and conflicting edits return 409. Secrets are omitted from reads. `POST /instant/api/demo` is available only in demo mode.

Keep one runtime/service owner per database. SQL analysis and file writes settle before cancellation releases ownership. The worker reports its last error in snapshot health and retries its polling loop after failures; it does not resend failed external actions. Signal inputs retain five minutes and chart history retains one day when new events arrive. Action history remains for inspection and has no automatic retention limit. Back up SQLite using the [operations guide](operations.md), monitor disk use, and protect the secret file with host permissions.

## Browser verification

The [browser smoke check](../tools/instant_browser_smoke.py) starts a temporary server, exercises the demo and shutdown rule editor, verifies persistence after reload, and captures desktop/mobile screenshots. Run it in an environment that permits local networking and Chromium processes:

```bash
python -m pip install -e '.[dev,instant]' playwright
python -m playwright install chromium
python tools/instant_browser_smoke.py
```

The CI workflow includes this check and uploads its screenshots. Local release validation distinguishes API/package checks from browser checks that require those environment capabilities.
