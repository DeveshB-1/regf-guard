import json

from conftest import FIX

from regf_guard.transcript import audit_transcript, find_rpc


def load(name):
    return json.loads((FIX / "transcripts" / name).read_text(encoding="utf-8"))


def rules(res, severity=None):
    return [f["rule"] for f in res["findings"] if severity is None or f["severity"] == severity]


def test_spouse_answers_and_bot_discloses_the_debt():
    res = audit_transcript(load("raj-r1-spouse.json"))
    assert res["rpc_turn"] is None
    assert rules(res) == ["DEBT_DISCLOSED_BEFORE_RPC", "DEBT_DISCLOSED_BEFORE_RPC"]
    assert [f["turn"] for f in res["findings"]] == [0, 2]


def test_clean_call_with_mini_miranda():
    res = audit_transcript(load("raj-r2.json"))
    assert res["rpc_turn"] == 1
    assert res["findings"] == []


def test_voicemail_with_balance_and_garnishment_threat():
    res = audit_transcript(load("anna-a2-voicemail.json"))
    assert rules(res, "violation") == ["DEBT_DISCLOSED_BEFORE_RPC", "ARREST_OR_SEIZURE_THREAT"]


def test_stop_calling_emits_opt_out_event_and_flags_the_payment_push():
    res = audit_transcript(load("maria-m1.json"))
    assert rules(res) == ["PHONE_OPT_OUT_REQUESTED"]
    assert res["findings"][0]["severity"] == "warning"
    assert [e["kind"] for e in res["events"]] == ["opt_out_phone"]
    assert res["events"][0]["person"] == "p-maria"


def test_callback_request_becomes_consent():
    res = audit_transcript(load("sam-s1.json"))
    assert rules(res) == ["CALLBACK_CONSENT"]
    assert [e["kind"] for e in res["events"]] == ["consent"]


def test_missing_mini_miranda_initial_and_subsequent():
    turns = [
        {"speaker": "agent", "text": "Am I speaking with Jo?"},
        {"speaker": "customer", "text": "Yes"},
        {"speaker": "agent", "text": "Your balance is $50, can you pay today?"},
    ]
    assert rules(audit_transcript({"turns": turns})) == ["MINI_MIRANDA_MISSING"]
    res = audit_transcript({"turns": turns, "initial_communication": False})
    assert res["findings"][0]["cite"] == "12 CFR 1006.18(e)(2)"
    turns[2]["text"] = "This call is from a debt collector. Your balance is $50."
    assert audit_transcript({"turns": turns, "initial_communication": False})["findings"] == []


def test_platform_supplied_rpc_turn_wins():
    turns = [
        {"speaker": "agent", "text": "Hello, Jo Smith?"},
        {"speaker": "customer", "text": "Mm."},
        {"speaker": "agent", "text": "This is a debt collector. This is an attempt to collect a debt and any "
                                     "information obtained will be used for that purpose."},
    ]
    assert find_rpc(turns) is None
    assert audit_transcript({"turns": turns, "rpc_verified_turn": 1})["findings"] == []


def test_attorney_and_dispute_and_hinglish_stop():
    turns = [
        {"speaker": "agent", "text": "Kya main Ravi ji se baat kar raha hoon?"},
        {"speaker": "customer", "text": "Haan, bolo."},
        {"speaker": "agent", "text": "Aapka EMI overdue hai."},
        {"speaker": "customer", "text": "Ye mera loan nahi hai, not my loan. Aur call mat karo."},
        {"speaker": "customer", "text": "Talk to my lawyer."},
    ]
    res = audit_transcript({"turns": turns, "ts": "2026-10-12T11:00:00+05:30", "person": "p", "debt": "d"}, "in_rbi")
    assert res["rpc_turn"] == 1
    assert set(rules(res)) == {"PHONE_OPT_OUT_REQUESTED", "DISPUTE_RAISED", "ATTORNEY_MENTIONED"}
    assert {e["kind"] for e in res["events"]} == {"opt_out_phone", "attorney_represented"}
    assert "MINI_MIRANDA_MISSING" not in rules(res)  # US-only rule


def test_police_threat_in_india_profile():
    turns = [{"speaker": "agent", "text": "Agar payment nahi kiya to police aayegi."}]
    res = audit_transcript({"turns": turns}, "in_rbi")
    assert "ARREST_OR_SEIZURE_THREAT" in rules(res)
