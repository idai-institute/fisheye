from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

RouteMode = Literal["raw", "redacted", "feature_only"]


@dataclass(slots=True)
class Route:
    collector_name: str
    mode: RouteMode = "raw"
    event_pattern: str = "*"


class Router:
    def __init__(self) -> None:
        self._routes: dict[int, Route] = {}

    def add_route(self, subscription_id: int, route: Route) -> None:
        self._routes[subscription_id] = route

    def remove_route(self, subscription_id: int) -> None:
        self._routes.pop(subscription_id, None)

    def get_route(self, subscription_id: int) -> Route | None:
        return self._routes.get(subscription_id)

    def active_modes(self) -> set[RouteMode]:
        return {route.mode for route in self._routes.values()}

    def list_routes(self) -> dict[int, Route]:
        return dict(self._routes)
