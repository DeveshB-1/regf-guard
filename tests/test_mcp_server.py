import json

import anyio
from conftest import FIX
from mcp import Client

from regf_guard import server


def _data(res):
    assert not res.is_error, res.content
    return res.structured_content or json.loads(res.content[0].text)


def call(name, args):
    async def go():
        async with Client(server.mcp) as c:
            return _data(await c.call_tool(name, args))
    return anyio.run(go)


def test_tools_listed_and_read_only():
    async def go():
        async with Client(server.mcp) as c:
            return {t.name for t in (await c.list_tools()).tools}
    names = anyio.run(go)
    assert names == {"next_allowed_call", "audit_call_log", "audit_transcript", "list_rules"}


def test_pre_dial_check_over_mcp():
    events = (FIX / "calls.jsonl").read_text()
    res = call("next_allowed_call", {"events_json": events, "person": "p-anna", "debt": "cc-1001",
                                     "timezones": ["America/Los_Angeles", "America/New_York"],
                                     "now": "2026-10-19T09:00:00-04:00"})
    assert res["allowed_now"] is False
    assert res["local_times"]["America/Los_Angeles"] == "2026-10-19 08:00"


def test_audit_call_log_over_mcp():
    res = call("audit_call_log", {"events_json": (FIX / "calls.jsonl").read_text(),
                                  "accounts_json": (FIX / "accounts.json").read_text()})
    rules = {f["rule"] for f in res["cadence_findings"]}
    assert {"MORE_THAN_7_IN_7_DAYS", "OUTSIDE_CALLING_HOURS"} <= rules


def test_audit_transcript_over_mcp():
    tr = json.loads((FIX / "transcripts" / "raj-r1-spouse.json").read_text())
    res = call("audit_transcript", {"transcript": tr})
    assert res["counts"]["violation"] == 2
