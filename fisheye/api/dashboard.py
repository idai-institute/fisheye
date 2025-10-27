from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
_ENV = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(enabled_extensions=("html", "xml"), default_for_string=True),
)


def render_dashboard(alerts: list[dict[str, Any]], runs: list[dict[str, Any]]) -> str:
    template = _ENV.get_template("dashboard.html")
    return template.render(alerts=alerts, runs=runs)
