"""Transactional score projections and a durable countermeasure queue."""

from __future__ import annotations

import hashlib
import json
import time
from uuid import uuid4

from fisheye_instant.models import MailSettings, Rule, anomaly_score


class Conflict(ValueError):
    pass


class InstantStore:
    window_seconds = 300

    def __init__(self, journal, application_id="default", clock=time.time):
        self.journal, self.application_id, self.clock = journal, application_id, clock
        with journal._lock, journal._conn:
            journal._conn.executescript("""
                CREATE TABLE IF NOT EXISTS instant_settings (
                    application TEXT PRIMARY KEY, revision INTEGER NOT NULL, data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS instant_workflows (
                    scope TEXT PRIMARY KEY, updated REAL NOT NULL, coverage TEXT NOT NULL,
                    last_event TEXT NOT NULL, sequence INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS instant_signals (
                    sequence INTEGER NOT NULL, channel TEXT NOT NULL, scope TEXT NOT NULL,
                    observed REAL NOT NULL, score REAL NOT NULL, detail TEXT NOT NULL,
                    PRIMARY KEY(sequence,channel)
                );
                CREATE INDEX IF NOT EXISTS instant_recent ON instant_signals(scope,observed);
                CREATE TABLE IF NOT EXISTS instant_history (
                    sequence INTEGER PRIMARY KEY, scope TEXT NOT NULL, observed REAL NOT NULL, score REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS instant_history_scope ON instant_history(scope,observed);
                CREATE TABLE IF NOT EXISTS instant_rule_state (
                    scope TEXT NOT NULL, rule_id TEXT NOT NULL, armed INTEGER NOT NULL, last_fired REAL NOT NULL,
                    PRIMARY KEY(scope,rule_id)
                );
                CREATE TABLE IF NOT EXISTS instant_actions (
                    id TEXT PRIMARY KEY, application TEXT NOT NULL, scope TEXT NOT NULL,
                    rule_id TEXT NOT NULL, rule_version TEXT NOT NULL, rule_json TEXT NOT NULL,
                    score REAL NOT NULL, created REAL NOT NULL, status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0,
                    error_type TEXT, completed REAL, event_id TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS instant_action_queue ON instant_actions(application,status,next_attempt);
            """)
            default = dict(
                rules=[Rule(id="early-warning", name="Raise a warning", threshold=60).model_dump(mode="json")],
                mail=MailSettings().model_dump(),
            )
            journal._conn.execute(
                "INSERT OR IGNORE INTO instant_settings VALUES(?,1,?)", (application_id, json.dumps(default))
            )
        journal.analysis_projections.append(self.project)

    @staticmethod
    def rule_version(rule):
        return hashlib.sha256(json.dumps(rule, sort_keys=True).encode()).hexdigest()

    def settings(self):
        with self.journal._lock:
            row = self.journal._conn.execute(
                "SELECT * FROM instant_settings WHERE application=?", (self.application_id,)
            ).fetchone()
            return dict(json.loads(row["data"]), revision=row["revision"])

    def save_settings(self, rules, mail, revision):
        if len(rules) > 30 or len({r.id for r in rules}) != len(rules):
            raise ValueError("Use at most 30 rules with unique IDs")
        data = dict(rules=[r.model_dump(mode="json") for r in rules], mail=mail.model_dump())
        with self.journal._lock, self.journal._conn:
            changed = self.journal._conn.execute(
                "UPDATE instant_settings SET revision=revision+1,data=? WHERE application=? AND revision=?",
                (json.dumps(data), self.application_id, revision),
            ).rowcount
            if not changed:
                raise Conflict("Settings changed in another tab. Reload before saving.")
        return dict(data, revision=revision + 1)

    def _channels(self, scope, now):
        rows = self.journal._conn.execute(
            "SELECT channel,score,detail FROM instant_signals WHERE scope=? AND observed>? ORDER BY sequence",
            (scope, now - self.window_seconds),
        )
        return [dict(json.loads(row["detail"]), channel=row["channel"], score=row["score"]) for row in rows]

    def project(self, journal, sequence, event, state, alerts, findings, signals, errors):
        if event.application_id != self.application_id:
            return
        now = self.clock()
        conn = journal._conn
        prior_score, _ = anomaly_score(self._channels(event.scope, now))
        observations = {}
        for signal in signals:
            observations["detector:" + signal.detector_id] = dict(
                score=signal.score,
                category=signal.category,
                label=signal.detector_id.replace("_", " "),
                agent_id=event.agent_id,
                event_id=event.event_id,
                evidence=signal.evidence,
            )
        for alert in alerts:
            if alert.category == "behavioral" and not any(
                signal.detector_id.startswith("behavior.") for signal in signals
            ):
                key = "behavior:" + ":".join(sorted(alert.sources))
                if key not in observations or alert.score > observations[key]["score"]:
                    observations[key] = dict(
                        score=alert.score,
                        category=alert.category,
                        label="Behavior change",
                        agent_id=event.agent_id,
                        event_id=event.event_id,
                        evidence=alert.evidence,
                    )
        for finding in findings:
            # Detector-backed findings repeat already counted signals. Graph
            # findings carry structural evidence rather than an alert's signals.
            if "signals" in finding.evidence or finding.category == "behavioral":
                continue
            key = "graph:" + finding.category
            if key not in observations or finding.score > observations[key]["score"]:
                observations[key] = dict(
                    score=finding.score,
                    category=finding.category,
                    label=finding.title,
                    agent_id=event.agent_id,
                    event_id=event.event_id,
                    evidence=finding.evidence,
                )
        for channel, detail in observations.items():
            conn.execute(
                "INSERT OR IGNORE INTO instant_signals VALUES(?,?,?,?,?,?)",
                (sequence, channel, event.scope, now, detail["score"], journal._json(detail)),
            )
        coverage = dict(state.get("coverage", {}))
        coverage.update({plugin: "error" for plugin, error in errors})
        conn.execute(
            "INSERT OR REPLACE INTO instant_workflows VALUES(?,?,?,?,?)",
            (event.scope, now, json.dumps(coverage), event.event_id, sequence),
        )
        score, channels = anomaly_score(self._channels(event.scope, now))
        conn.execute("INSERT OR IGNORE INTO instant_history VALUES(?,?,?,?)", (sequence, event.scope, now, score))
        rules = self.settings()["rules"]
        for rule in rules:
            if (
                not rule["enabled"]
                or rule["workflow_id"] not in (None, event.workflow)
                or rule["environment"] not in (None, event.environment)
            ):
                continue
            version = self.rule_version(rule)
            state_key = rule["id"] + ":" + version[:16]
            row = conn.execute(
                "SELECT * FROM instant_rule_state WHERE scope=? AND rule_id=?", (event.scope, state_key)
            ).fetchone()
            armed, fired = (row["armed"], row["last_fired"]) if row else (True, 0)
            if min(score, prior_score) < rule["threshold"] - rule["hysteresis"]:
                armed = True
            if armed and score >= rule["threshold"] and (not fired or now - fired >= rule["cooldown_seconds"]):
                action_id = uuid4().hex
                conn.execute(
                    "INSERT INTO instant_actions(id,application,scope,rule_id,rule_version,rule_json,score,created,status,event_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        action_id,
                        self.application_id,
                        event.scope,
                        rule["id"],
                        version,
                        json.dumps(rule),
                        score,
                        now,
                        "pending",
                        event.event_id,
                    ),
                )
                armed, fired = False, now
            conn.execute(
                "INSERT OR REPLACE INTO instant_rule_state VALUES(?,?,?,?)", (event.scope, state_key, int(armed), fired)
            )
        # Only recent score inputs and one day of chart history are needed.
        conn.execute("DELETE FROM instant_signals WHERE observed<=?", (now - self.window_seconds,))
        conn.execute("DELETE FROM instant_history WHERE observed<?", (now - 86400,))

    def snapshot(self, workflow_id=None, environment="local"):
        now = self.clock()
        with self.journal._lock:
            rows = self.journal._conn.execute(
                "SELECT * FROM instant_workflows WHERE json_extract(scope,'$[0]')=? ORDER BY updated DESC,scope LIMIT 200",
                (self.application_id,),
            ).fetchall()
            workflows = []
            for row in rows:
                app, env, workflow = json.loads(row["scope"])
                score, channels = anomaly_score(self._channels(row["scope"], now))
                coverage = json.loads(row["coverage"])
                stale = now - row["updated"] > self.window_seconds
                incomplete = not coverage or any(v not in {"evaluated", "not_applicable"} for v in coverage.values())
                workflows.append(
                    dict(
                        workflow_id=workflow,
                        environment=env,
                        scope=row["scope"],
                        score=score,
                        updated=row["updated"],
                        channels=channels,
                        coverage=coverage,
                        status="stale"
                        if stale
                        else "limited"
                        if incomplete
                        else "critical"
                        if score >= 85
                        else "warning"
                        if score >= 60
                        else "healthy",
                    )
                )
            selected = next(
                (w for w in workflows if w["workflow_id"] == workflow_id and w["environment"] == environment), None
            )
            if workflow_id is None and workflows:
                selected = max(workflows, key=lambda w: (w["score"], w["updated"]))
            history = []
            if selected:
                history = [
                    dict(r)
                    for r in self.journal._conn.execute(
                        "SELECT observed,score FROM (SELECT * FROM instant_history WHERE scope=? ORDER BY sequence DESC LIMIT 120) ORDER BY sequence",
                        (selected["scope"],),
                    )
                ]
            actions = [
                dict(r)
                for r in self.journal._conn.execute(
                    "SELECT * FROM instant_actions WHERE application=? ORDER BY created DESC,id DESC LIMIT 50",
                    (self.application_id,),
                )
            ]
            for action in actions:
                action["rule"] = json.loads(action.pop("rule_json"))
                action["workflow_id"] = json.loads(action["scope"])[2]
            return dict(
                workflows=workflows,
                selected=selected,
                history=history,
                actions=actions,
                window_seconds=self.window_seconds,
                now=now,
            )

    def claim(self):
        with self.journal._lock, self.journal._conn:
            conn = self.journal._conn
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM instant_actions WHERE application=? AND status='pending' AND next_attempt<=? ORDER BY created,id LIMIT 1",
                (self.application_id, self.clock()),
            ).fetchone()
            if row is None:
                return None
            active = {r["id"]: r for r in self.settings()["rules"] if r["enabled"]}
            rule = active.get(row["rule_id"])
            if rule is None or self.rule_version(rule) != row["rule_version"]:
                conn.execute(
                    "UPDATE instant_actions SET status='cancelled',completed=? WHERE id=?", (self.clock(), row["id"])
                )
                return None
            conn.execute("UPDATE instant_actions SET status='executing',attempts=attempts+1 WHERE id=?", (row["id"],))
            return dict(row, rule=json.loads(row["rule_json"]), attempts=row["attempts"] + 1)

    def finish(self, action_id, status, error_type=None):
        with self.journal._lock, self.journal._conn:
            self.journal._conn.execute(
                "UPDATE instant_actions SET status=?,error_type=?,completed=? WHERE id=? AND application=? AND status='executing'",
                (status, error_type, self.clock(), action_id, self.application_id),
            )

    def recover(self):
        with self.journal._lock, self.journal._conn:
            self.journal._conn.execute(
                "UPDATE instant_actions SET status='unknown',error_type='Interrupted',completed=? WHERE application=? AND status='executing'",
                (self.clock(), self.application_id),
            )
