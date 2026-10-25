"""Glue: load a call log, account time zones and transcripts; audit them together.

Transcript findings feed the cadence audit: a "stop calling" in Tuesday's bot
call becomes an opt-out event, so Wednesday's dialer retry is flagged.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from .cadence import Event, audit_events
from .transcript import audit_transcript


def parse_events(data: str | list[dict[str, Any]]) -> list[Event]:
    if isinstance(data, str):
        text = data.strip()
        if text.startswith("["):
            rows = json.loads(text)
        else:
            rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        rows = data
    return [Event.from_dict(r) for r in rows]


def parse_accounts(data: str | dict[str, Any]) -> dict[str, list[str]]:
    raw = json.loads(data) if isinstance(data, str) else data
    out = {}
    for person, v in raw.items():
        zones = v["timezones"] if isinstance(v, dict) else v
        out[person] = [zones] if isinstance(zones, str) else list(zones)
    return out


def load_transcripts(path: Path) -> list[dict[str, Any]]:
    files = sorted(path.glob("*.json")) if path.is_dir() else [path]
    return [json.loads(f.read_text(encoding="utf-8")) for f in files]


def run_audit(
    events: list[Event],
    accounts: dict[str, list[str]],
    transcripts: list[dict[str, Any]] | None = None,
    profile: str = "us_regf",
) -> dict[str, Any]:
    tr_results = [audit_transcript(t, profile) for t in (transcripts or [])]
    derived = [Event.from_dict(e) for r in tr_results for e in r["events"]]
    cadence = audit_events(events + derived, accounts, profile)
    by_rule = Counter(f.rule for f in cadence)
    for r in tr_results:
        for f in r["findings"]:
            if f["severity"] != "info":
                by_rule[f["rule"]] += 1
    violations = sum(1 for f in cadence if f.severity == "violation") + sum(r["counts"]["violation"] for r in tr_results)
    warnings = sum(1 for f in cadence if f.severity == "warning") + sum(r["counts"]["warning"] for r in tr_results)
    return {
        "profile": profile,
        "summary": {
            "calls": sum(1 for e in events if e.kind == "call"),
            "transcripts": len(tr_results),
            "derived_events": len(derived),
            "violations": violations,
            "warnings": warnings,
            "by_rule": dict(sorted(by_rule.items())),
        },
        "cadence_findings": [f.to_dict() for f in cadence],
        "transcripts": tr_results,
    }
