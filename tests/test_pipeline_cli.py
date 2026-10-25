import json

from conftest import FIX

from regf_guard import cli
from regf_guard.pipeline import load_transcripts, parse_accounts, parse_events, run_audit

ARGS = ["--calls", str(FIX / "calls.jsonl"), "--accounts", str(FIX / "accounts.json")]


def demo(with_transcripts=True):
    events = parse_events((FIX / "calls.jsonl").read_text())
    accounts = parse_accounts((FIX / "accounts.json").read_text())
    trs = load_transcripts(FIX / "transcripts") if with_transcripts else []
    return run_audit(events, accounts, trs)


def test_demo_portfolio_summary():
    s = demo()["summary"]
    assert s["calls"] == 19 and s["transcripts"] == 5
    assert s["violations"] == 9 and s["warnings"] == 2
    assert s["by_rule"]["DEBT_DISCLOSED_BEFORE_RPC"] == 3


def test_transcript_events_change_the_cadence_verdict():
    with_tr = {(f["person"], f["rule"]) for f in demo(True)["cadence_findings"]}
    without = {(f["person"], f["rule"]) for f in demo(False)["cadence_findings"]}
    # "stop calling" in Maria's call is what makes the next-day retry an opt-out violation
    assert ("p-maria", "PHONE_OPT_OUT") in with_tr - without
    # "call me back on Friday" is what makes Sam's Friday call fine
    assert ("p-sam", "CALL_WITHIN_7_DAYS_OF_CONVERSATION") in without - with_tr


def test_cli_audit_json_and_exit_code(capsys):
    code = cli.main(["audit", *ARGS, "--transcripts", str(FIX / "transcripts"), "--json"])
    assert code == 1
    assert json.loads(capsys.readouterr().out)["summary"]["violations"] == 9


def test_cli_next_call(capsys):
    cli.main(["next-call", *ARGS, "--transcripts", str(FIX / "transcripts"),
              "--person", "p-maria", "--debt", "md-3003", "--now", "2026-10-19T12:00:00-04:00"])
    assert json.loads(capsys.readouterr().out)["blocked"] == "PHONE_OPT_OUT"


def test_naive_timestamp_is_rejected():
    import pytest
    with pytest.raises(ValueError, match="UTC offset"):
        parse_events('[{"ts": "2026-10-12T10:00:00", "person": "p", "debt": "d"}]')
