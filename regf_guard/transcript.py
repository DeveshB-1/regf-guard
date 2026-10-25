"""Rule-based audit of one collection-call transcript.

Checks (US profile unless noted):

* DEBT_DISCLOSED_BEFORE_RPC: the agent mentions the debt before right-party
  contact is confirmed, i.e. to someone who may be a third party
  (12 CFR 1006.6(d)(1); RBI instructions bar the same with family and friends).
* MINI_MIRANDA_MISSING: after RPC, the agent never says it is attempting to
  collect a debt and that information will be used for that purpose (initial
  communication, 1006.18(e)(1)), or never says the call is from a debt collector
  (subsequent communications, 1006.18(e)(2)). US only.
* ARREST_OR_SEIZURE_THREAT: arrest, jail, police, garnishment or seizure
  language (1006.18(b)(3); intimidation is also barred by RBI).
* LEGAL_ACTION_MENTION: "sue", "court", "legal action". Only a violation if the
  action is not lawful or not intended (1006.18(c)(1)), so it is a warning.
* PROFANITY (1006.14(d)).
* Customer said stop calling / has a lawyer / disputes the debt / asked for a
  callback. These become cadence events (opt_out_phone, attorney_represented,
  consent) so the pre-dial guard blocks or allows the next call, and a warning
  when the agent keeps asking for payment afterwards.

Patterns cover English plus a few common Hinglish phrasings. They are a first
pass for QA review, built to be read and extended, and they will miss things an
LLM or a human reviewer would catch.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from .cadence import PROFILES

I = re.IGNORECASE

IDENTITY_Q = re.compile(
    r"\b(am i (speaking|talking) (with|to)|is this|are you|may i speak (with|to)|can i speak (with|to)|"
    r"kya (main|mai) .* se baat kar)",
    I,
)
AFFIRM = re.compile(
    r"^\W*(yes|yeah|yep|yup|speaking|this is (he|she|him|her|me)|that'?s me|it'?s me|correct|"
    r"haan|haa|ha ji|ji haan|ji|bol (raha|rahi))\b",
    I,
)
DEBT_TERMS = re.compile(
    r"\b(debt|balance|past[- ]due|overdue|outstanding|owe[sd]?|payment (is )?due|due amount|"
    r"collect(ion|ing)?|delinquent|emi|dues|bakaya|loan (amount|payment))\b",
    I,
)
MM_COLLECT = re.compile(r"attempt(ing)? to collect a debt", I)
MM_PURPOSE = re.compile(r"information (obtained |we obtain |you provide |collected )?(will|may) be used for that purpose", I)
MM_SUBSEQUENT = re.compile(r"\b(from|is) a debt collector\b|\bthis is a debt collector\b", I)
THREAT = re.compile(
    r"\b(arrest(ed)?|jail|prison|police|warrant|criminal (case|charges?)|garnish(ed|ment)?|"
    r"seiz(e|ed|ure)|repossess|giraftar|thana)\b",
    I,
)
LEGAL = re.compile(r"\b(sue|lawsuit|take you to court|legal action|court case)\b", I)
PROFANITY = re.compile(r"\b(damn|hell|shit|bastard|idiot|stupid|bloody)\b", I)
STOP_CALLING = re.compile(
    r"\b(stop calling|don'?t call( me)?|do not call|quit calling|never call|no more calls|"
    r"call mat kar(o|na)?|phone mat kar(o|na)?)\b",
    I,
)
ATTORNEY = re.compile(r"\b(my|an|our) (attorney|lawyer|advocate)\b|\brepresented by (an? )?(attorney|counsel|lawyer)\b", I)
DISPUTE = re.compile(r"\b(not my (debt|loan|account)|i dispute|don'?t owe|never (took|had|opened) (this|that|a|any))\b", I)
CALLBACK = re.compile(
    r"\bcall (me )?back (on |tomorrow|later|next|monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"in the (morning|evening|afternoon))|\byou can call me\b",
    I,
)
PAY_ASK = re.compile(r"\b(pay|payment|settle|settlement|promise to pay|amount|clear (the|your) dues)\b", I)


def _finding(rule: str, severity: str, turn: int | None, text: str, message: str, cite: str) -> dict[str, Any]:
    return {"rule": rule, "severity": severity, "turn": turn, "quote": text[:160], "message": message, "cite": cite}


def find_rpc(turns: list[dict[str, str]]) -> int | None:
    """Index of the customer turn that confirms identity, or None."""
    for i, t in enumerate(turns[:-1]):
        if t["speaker"] == "agent" and IDENTITY_Q.search(t["text"]):
            nxt = turns[i + 1]
            if nxt["speaker"] == "customer" and AFFIRM.search(nxt["text"]):
                return i + 1
    return None


def audit_transcript(tr: dict[str, Any], profile: str = "us_regf") -> dict[str, Any]:
    prof = PROFILES[profile]
    us = prof.name == "us_regf"
    turns = [{"speaker": t["speaker"].lower(), "text": t["text"]} for t in tr["turns"]]
    rpc = tr.get("rpc_verified_turn")
    if rpc is None:
        rpc = find_rpc(turns)
    findings: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    third_party_cite = "12 CFR 1006.6(d)(1)" if us else "RBI recovery-agent instructions, 12 Aug 2022"
    conduct_cite = (lambda c: c) if us else (lambda c: "RBI recovery-agent instructions, 12 Aug 2022")

    for i, t in enumerate(turns):
        if t["speaker"] != "agent":
            continue
        before_rpc = rpc is None or i < rpc
        if before_rpc and DEBT_TERMS.search(t["text"]):
            who = "before right-party contact was confirmed" if rpc is not None else "and right-party contact was never confirmed"
            findings.append(_finding("DEBT_DISCLOSED_BEFORE_RPC", "violation", i, t["text"],
                                     f"agent mentioned the debt {who}", third_party_cite))
        if THREAT.search(t["text"]):
            findings.append(_finding("ARREST_OR_SEIZURE_THREAT", "violation", i, t["text"],
                                     "arrest/seizure language; only lawful and intended actions may be mentioned",
                                     conduct_cite("12 CFR 1006.18(b)(3)")))
        elif LEGAL.search(t["text"]):
            findings.append(_finding("LEGAL_ACTION_MENTION", "warning", i, t["text"],
                                     "legal action mentioned; confirm it is lawful and actually intended",
                                     conduct_cite("12 CFR 1006.18(c)(1)")))
        if PROFANITY.search(t["text"]):
            findings.append(_finding("PROFANITY", "violation", i, t["text"], "profane or abusive word",
                                     conduct_cite("12 CFR 1006.14(d)")))

    if us and rpc is not None:
        after = " ".join(t["text"] for t in turns[rpc:] if t["speaker"] == "agent")
        if tr.get("initial_communication", True):
            missing = [n for n, rx in (("attempting to collect a debt", MM_COLLECT), ("information used for that purpose", MM_PURPOSE)) if not rx.search(after)]
            if missing:
                findings.append(_finding("MINI_MIRANDA_MISSING", "violation", None, "",
                                         "initial communication without: " + "; ".join(missing), "12 CFR 1006.18(e)(1)"))
        elif not MM_SUBSEQUENT.search(after):
            findings.append(_finding("MINI_MIRANDA_MISSING", "violation", None, "",
                                     "subsequent communication never says it is from a debt collector", "12 CFR 1006.18(e)(2)"))

    ts = tr.get("ts")
    base_ts = datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None

    def emit(kind: str, i: int, **extra: Any) -> None:
        if base_ts is None or "person" not in tr or "debt" not in tr:
            return
        events.append({"ts": (base_ts + timedelta(seconds=i + 1)).isoformat(), "person": tr["person"],
                       "debt": tr["debt"], "kind": kind, "id": f"{tr.get('call_id', 'call')}#t{i}", **extra})

    for i, t in enumerate(turns):
        if t["speaker"] != "customer":
            continue
        later_pay = next((j for j in range(i + 1, len(turns)) if turns[j]["speaker"] == "agent" and PAY_ASK.search(turns[j]["text"])), None)
        if STOP_CALLING.search(t["text"]):
            emit("opt_out_phone", i)
            msg = "customer asked to stop phone calls; future calls are blocked"
            if later_pay is not None:
                msg += f"; agent kept asking for payment at turn {later_pay}"
            findings.append(_finding("PHONE_OPT_OUT_REQUESTED", "warning" if later_pay is not None else "info", i, t["text"], msg,
                                     "12 CFR 1006.14(h)(1)" if us else "RBI recovery-agent instructions, 12 Aug 2022"))
        if ATTORNEY.search(t["text"]):
            emit("attorney_represented", i)
            msg = "customer says they have an attorney; get the attorney's name and address and stop contacting the consumer"
            if later_pay is not None:
                msg += f"; agent kept asking for payment at turn {later_pay}"
            findings.append(_finding("ATTORNEY_MENTIONED", "warning" if later_pay is not None else "info", i, t["text"], msg,
                                     "12 CFR 1006.6(b)(2)"))
        if DISPUTE.search(t["text"]):
            findings.append(_finding("DISPUTE_RAISED", "info", i, t["text"],
                                     "consumer disputes the debt; route for validation follow-up", "12 CFR 1006.34, 1006.38"))
        if CALLBACK.search(t["text"]) and not STOP_CALLING.search(t["text"]):
            emit("consent", i)
            findings.append(_finding("CALLBACK_CONSENT", "info", i, t["text"],
                                     "consumer invited a callback; treated as prior consent for up to 7 days (confirm)",
                                     "12 CFR 1006.14(b)(3)(i)"))

    sev = {"violation": 0, "warning": 0, "info": 0}
    for f in findings:
        sev[f["severity"]] += 1
    return {"call_id": tr.get("call_id"), "profile": prof.name, "rpc_turn": rpc,
            "counts": sev, "findings": findings, "events": events}
