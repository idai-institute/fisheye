"""One correlation contract shared by journal projections and offline replay."""

from fisheye.schema.domain import Finding


def merge_finding(previous: Finding | None, incoming: Finding, limit=200) -> Finding:
    result = incoming.model_copy(deep=True)
    if previous is not None:
        result.first_seen = min(previous.first_seen, incoming.first_seen)
        result.last_seen = max(previous.last_seen, incoming.last_seen)
        result.occurrences = previous.occurrences + incoming.occurrences
    # Keep the current causal path even if its shared source is old.
    current_ids = list(dict.fromkeys(incoming.event_ids))
    event_ids = [key for key in previous.event_ids if key not in current_ids] + current_ids if previous else current_ids
    current_agents = list(dict.fromkeys(incoming.agent_ids))
    agents = (
        [key for key in previous.agent_ids if key not in current_agents] + current_agents
        if previous
        else current_agents
    )
    result.event_ids = event_ids[-limit:]
    result.agent_ids = sorted(agents[-limit:])
    result.evidence_truncated = (
        bool(previous and previous.evidence_truncated)
        or incoming.evidence_truncated
        or len(event_ids) > limit
        or len(agents) > limit
    )
    if previous is None:
        return result
    result.status = previous.status
    if (
        previous.status == "resolved"
        and incoming.last_seen >= previous.last_seen
        and set(current_ids) - set(previous.event_ids)
    ):
        result.status = "open"
    return result
