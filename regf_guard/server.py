"""MCP server: a pre-dial guard and auditor an AI collections agent can call.

Read-only by design: no tool dials, sends or writes. The agent asks
`next_allowed_call` before it places a call, and QA runs `audit_call_log` /
`audit_transcript` after the fact.

Run:  regf-guard serve
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from mcp.server.mcpserver import MCPServer

from . import cadence
from .pipeline import parse_accounts, parse_events, run_audit
from .transcript import audit_transcript as _audit_transcript

mcp = MCPServer(
    name="regf-guard",
    instructions=(
        "Collections compliance checks. Call next_allowed_call before placing a collection call: it "
        "returns whether a call now keeps the Reg F 7-in-7 and 7-days-after-conversation presumptions "
        "and the 8am-9pm window in every time zone on file. audit_call_log replays a call log; "
        "audit_transcript checks one bot call for third-party disclosure, mini-Miranda, threats and "
        "opt-outs. Profiles: us_regf (default) or in_rbi (8am-7pm IST, no frequency cap)."
    ),
)


@mcp.tool()
def next_allowed_call(
    events_json: str,
    person: str,
    debt: str,
    timezones: list[str],
    now: str,
    profile: str = "us_regf",
) -> dict[str, Any]:
    """Earliest time at or after `now` (ISO with offset) when one more call to `person` about `debt` stays compliant.

    events_json: the call history as a JSON list (or JSONL) of events with ts, person, debt, kind, outcome.
    timezones: every IANA zone the consumer might be in (area code, address, ...); the first is used for calendar days.
    """
    return cadence.next_allowed_call(parse_events(events_json), person, debt, timezones,
                                     datetime.fromisoformat(now), profile)


@mcp.tool()
def audit_call_log(events_json: str, accounts_json: str, profile: str = "us_regf") -> dict[str, Any]:
    """Replay a call log and list presumed violations and warnings with the regulation cited.

    accounts_json: JSON object mapping person -> list of IANA time zones.
    """
    return run_audit(parse_events(events_json), parse_accounts(accounts_json), [], profile)


@mcp.tool()
def audit_transcript(transcript: dict[str, Any], profile: str = "us_regf") -> dict[str, Any]:
    """Check one call transcript ({turns: [{speaker: agent|customer, text}], initial_communication, ...}).

    Returns findings plus derived cadence events (opt-out, attorney, callback consent) to append to the call log.
    """
    return _audit_transcript(transcript, profile)


@mcp.tool()
def list_rules() -> dict[str, Any]:
    """The calling-hour profiles and rule ids this server checks."""
    return {
        "profiles": {k: {"start": f"{p.start:%H:%M}", "end": f"{p.end:%H:%M}", "frequency_caps": p.frequency,
                         "time_cite": p.time_cite} for k, p in cadence.PROFILES.items()},
        "call_log_rules": ["MORE_THAN_7_IN_7_DAYS", "CALL_WITHIN_7_DAYS_OF_CONVERSATION", "OUTSIDE_CALLING_HOURS",
                           "PHONE_OPT_OUT", "ATTORNEY_REPRESENTED", "CEASE_NOTICE", "RAPID_SUCCESSION",
                           "CONCENTRATED_CALLS"],
        "transcript_rules": ["DEBT_DISCLOSED_BEFORE_RPC", "MINI_MIRANDA_MISSING", "ARREST_OR_SEIZURE_THREAT",
                             "LEGAL_ACTION_MENTION", "PROFANITY", "PHONE_OPT_OUT_REQUESTED", "ATTORNEY_MENTIONED",
                             "DISPUTE_RAISED", "CALLBACK_CONSENT"],
    }


if __name__ == "__main__":
    mcp.run("stdio")
