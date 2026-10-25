"""regf-guard command line.

  regf-guard audit --calls calls.jsonl --accounts accounts.json [--transcripts DIR] [--profile us_regf|in_rbi] [--json]
  regf-guard next-call --calls calls.jsonl --accounts accounts.json --person P --debt D [--now ISO] [--transcripts DIR]
  regf-guard serve        # MCP server on stdio
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .cadence import PROFILES, Event, next_allowed_call
from .pipeline import load_transcripts, parse_accounts, parse_events, run_audit
from .transcript import audit_transcript


def _load(args) -> tuple[list[Event], dict[str, list[str]], list[dict]]:
    events = parse_events(Path(args.calls).read_text(encoding="utf-8"))
    accounts = parse_accounts(Path(args.accounts).read_text(encoding="utf-8"))
    transcripts = load_transcripts(Path(args.transcripts)) if args.transcripts else []
    return events, accounts, transcripts


def _print_report(rep: dict) -> None:
    s = rep["summary"]
    print(f"profile {rep['profile']}: {s['calls']} calls, {s['transcripts']} transcripts, "
          f"{s['violations']} presumed violations, {s['warnings']} warnings")
    print()
    print("Call log")
    for f in rep["cadence_findings"]:
        print(f"  [{f['severity'][:4].upper()}] {f['ts'][:16]}  {f['person']}/{f['debt']}  {f['rule']}")
        print(f"         {f['message']}  ({f['cite']})")
    if not rep["cadence_findings"]:
        print("  clean")
    for t in rep["transcripts"]:
        print()
        print(f"Transcript {t['call_id']} (RPC at turn {t['rpc_turn']})")
        for f in t["findings"]:
            turn = "" if f["turn"] is None else f"t{f['turn']} "
            print(f"  [{f['severity'][:4].upper()}] {turn}{f['rule']}: {f['message']}  ({f['cite']})")
            if f["quote"]:
                print(f"         \"{f['quote']}\"")
        if not t["findings"]:
            print("  clean")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="regf-guard", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--calls", required=True, help="call log, JSON list or JSONL of events")
        sp.add_argument("--accounts", required=True, help="JSON map person -> time zones")
        sp.add_argument("--transcripts", help="transcript JSON file or directory")
        sp.add_argument("--profile", default="us_regf", choices=sorted(PROFILES))

    a = sub.add_parser("audit", help="audit a call log and transcripts")
    common(a)
    a.add_argument("--json", action="store_true")

    n = sub.add_parser("next-call", help="earliest compliant time for the next call")
    common(n)
    n.add_argument("--person", required=True)
    n.add_argument("--debt", required=True)
    n.add_argument("--now", help="ISO timestamp with offset (default: now)")

    t = sub.add_parser("transcript", help="audit one transcript file")
    t.add_argument("file")
    t.add_argument("--profile", default="us_regf", choices=sorted(PROFILES))

    sub.add_parser("serve", help="run the MCP server on stdio")
    args = p.parse_args(argv)

    if args.cmd == "serve":
        from .server import mcp
        mcp.run("stdio")
        return 0
    if args.cmd == "transcript":
        tr = json.loads(Path(args.file).read_text(encoding="utf-8"))
        json.dump(audit_transcript(tr, args.profile), sys.stdout, indent=2, ensure_ascii=False)
        print()
        return 0

    events, accounts, transcripts = _load(args)
    if args.cmd == "audit":
        rep = run_audit(events, accounts, transcripts, args.profile)
        if args.json:
            json.dump(rep, sys.stdout, indent=2, ensure_ascii=False)
            print()
        else:
            _print_report(rep)
        return 1 if rep["summary"]["violations"] else 0

    derived = [Event.from_dict(e) for t in transcripts for e in audit_transcript(t, args.profile)["events"]]
    now = datetime.fromisoformat(args.now) if args.now else datetime.now(timezone.utc)
    res = next_allowed_call(events + derived, args.person, args.debt, accounts[args.person], now, args.profile)
    json.dump(res, sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
