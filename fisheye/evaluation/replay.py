"""Offline replay never invokes agent tools or model providers."""
from __future__ import annotations

import hashlib
import json
import time
from collections import defaultdict
from pathlib import Path
from fisheye.analysis import AnalysisProcessor
from fisheye.schema.events import EventEnvelope
from fisheye.privacy import capture_event


def load_events(path):
    with Path(path).open() as stream:
        for line_number,line in enumerate(stream,1):
            if not line.strip():
                continue
            try:
                data=json.loads(line)
                yield EventEnvelope.model_validate(data.get('event',data))
            except Exception as exc:
                raise ValueError(f'Invalid event on line {line_number}: {type(exc).__name__}') from exc


async def replay(events,processor=None):
    processor=processor or AnalysisProcessor()
    states,findings,seen={},{},{}
    count=0
    latencies=[]
    errors=[]
    for raw in events:
        event=raw if isinstance(raw,EventEnvelope) else EventEnvelope.model_validate(raw)
        # Journal captures contain already-derived local evidence. They are trusted
        # only for offline evaluation; remote producers never get this privilege.
        if '_analysis' not in event.meta:
            event=capture_event(event)
        fingerprint=hashlib.sha256(json.dumps(event.model_dump(mode='json',exclude={'observed_at'}),sort_keys=True).encode()).hexdigest()
        if event.event_id in seen:
            if seen[event.event_id]!=fingerprint:
                raise ValueError('Conflicting duplicate event ID during replay')
            continue
        seen[event.event_id]=fingerprint
        started=time.perf_counter()
        result=await processor.analyze(event,states.get(event.scope))
        latencies.append((time.perf_counter()-started)*1000)
        states[event.scope]=result.state
        for finding in result.findings:
            data=finding.model_dump(mode='json')
            if finding.finding_id in findings:
                previous=findings[finding.finding_id]
                data['event_ids']=sorted(set(data['event_ids']+previous['event_ids']))
                data['first_seen']=previous['first_seen']
                data['occurrences']=previous['occurrences']+1
            findings[finding.finding_id]=data
        errors.extend(result.errors)
        count+=1
    latencies.sort()
    quantile=lambda q:latencies[min(int((len(latencies)-1)*q),len(latencies)-1)] if latencies else 0
    return dict(events=count,workflows=len(states),findings=list(findings.values()),errors=errors,
                coverage=processor.coverage,analysis_latency_ms=dict(p50=quantile(.5),p95=quantile(.95),p99=quantile(.99)))


async def evaluate(scenarios,processor_factory=AnalysisProcessor):
    rows=[]
    totals=defaultdict(lambda:dict(tp=0,fp=0,fn=0))
    benign=benign_false=0
    for scenario in scenarios:
        report=await replay(scenario['events'],processor_factory())
        expected=set(scenario.get('expected_categories',[]))
        # A scenario may label selected categories; others are reported but unscored.
        assessed=set(scenario.get('assessed_categories',expected))
        actual={f['category'] for f in report['findings']} & assessed
        for category in assessed:
            totals[category]['tp']+=int(category in actual and category in expected)
            totals[category]['fp']+=int(category in actual and category not in expected)
            totals[category]['fn']+=int(category not in actual and category in expected)
        if not expected:
            benign+=1
            benign_false+=len(actual)
        rows.append(dict(name=scenario['name'],split=scenario.get('split','test'),expected=sorted(expected),
                         actual=sorted(actual),passed=actual==expected,findings=report['findings'],errors=report['errors']))
    metrics={key:dict(value,precision=value['tp']/(value['tp']+value['fp']) if value['tp']+value['fp'] else None,
                       recall=value['tp']/(value['tp']+value['fn']) if value['tp']+value['fn'] else None)
             for key,value in totals.items()}
    return dict(scenarios=len(rows),passed=sum(r['passed'] for r in rows),metrics=metrics,
                benign_workflows=benign,false_findings_per_100_benign=100*benign_false/benign if benign else None,results=rows)


def compare(before,after):
    key=lambda f:(f['application_id'],f['workflow_id'],f['category'],tuple(sorted(f['event_ids'])))
    old={key(f):f for f in before['findings']}
    new={key(f):f for f in after['findings']}
    return dict(added=[new[k] for k in new.keys()-old.keys()],removed=[old[k] for k in old.keys()-new.keys()],
                changed=[dict(before=old[k],after=new[k]) for k in new.keys() & old.keys() if old[k]['score']!=new[k]['score']])
