from __future__ import annotations

import re
from urllib.parse import urlparse

from fisheye.preprocessors.base import Preprocessor
from fisheye.preprocessors.utils import walk_strings
from fisheye.schema.events import EventEnvelope

URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+")


class URLDomainExtractionPreprocessor(Preprocessor):
    async def process(self, event: EventEnvelope) -> list[EventEnvelope]:
        domains: set[str] = set()
        urls: list[str] = []

        for text in walk_strings(event.payload):
            for match in URL_PATTERN.finditer(text):
                url = match.group(0)
                urls.append(url)
                host = urlparse(url).netloc
                if host:
                    domains.add(host)

        if urls:
            event.meta.setdefault("urls", []).extend(urls)
            existing = set(event.meta.get("domains", []))
            event.meta["domains"] = sorted(existing.union(domains))
        return [event]
