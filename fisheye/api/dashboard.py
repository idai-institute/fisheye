from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
_ENV = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(enabled_extensions=("html", "xml"), default_for_string=True),
)


def event_anchor(event_id):
    return "event-" + hashlib.sha256(str(event_id).encode()).hexdigest()[:24]


_ENV.filters["event_anchor"] = event_anchor


def render_dashboard(alerts: list[dict[str, Any]], runs: list[dict[str, Any]], workflows=None, reviews=None) -> str:
    template = _ENV.get_template("dashboard.html")
    return template.render(alerts=alerts, runs=runs, workflows=workflows or [], reviews=reviews or [])


def render_investigation(workflow_id, graph, findings):
    display = dict(graph, nodes=[dict(node, anchor=event_anchor(node["id"])) for node in graph["nodes"]])
    return _ENV.get_template("investigation.html").render(
        workflow_id=workflow_id,
        graph=display,
        findings=findings,
        visible_event_ids={node["id"] for node in graph["nodes"]},
    )
