"""Start `regf-guard serve` as a subprocess and call it over real MCP stdio."""

import json
import sys
from pathlib import Path

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent


async def main() -> None:
    params = StdioServerParameters(command=sys.executable, args=["-m", "regf_guard.cli", "serve"])
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        tools = [t.name for t in (await s.list_tools()).tools]
        print("tools:", ", ".join(sorted(tools)))
        res = await s.call_tool("next_allowed_call", {
            "events_json": (ROOT / "fixtures" / "calls.jsonl").read_text(),
            "person": "p-li", "debt": "sl-4004", "timezones": ["America/Denver"],
            "now": "2026-10-17T11:00:00-06:00"})
        data = res.structured_content or json.loads(res.content[0].text)
        print("p-li next call:", data["next_allowed"], data["local_times"])


anyio.run(main)
