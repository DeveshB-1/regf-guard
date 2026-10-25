# regf-guard

A pre-dial guard and call auditor for AI debt-collection agents. It answers two questions:

1. **Before the dialer fires:** can this bot call this consumer about this debt right now and keep every Regulation F presumption of compliance? If not, when is the next compliant minute?
2. **After the call:** did the call log or the bot's transcript break a rule a QA analyst would flag?

It ships as a Python library, a CLI, and an MCP server, so an LLM collections agent can call `next_allowed_call` as a tool before it places a call.

## Why I built this for Skit.ai

Skit.ai runs voice agents for US collections, and Reg F is the rulebook every one of those calls lives under. Two things on Skit's own pages pointed me here:

- Skit's Quality Analyst opening (Keka job 164257) asks people to audit "both live agent calls and AI-powered bot calls" for "FDCPA, CFPB guidelines, UDAAP, TCPA", and to know "mini-Miranda, right-party contact, validation notices, cease & desist".
- skit.ai describes its Compliance product as "Built-in controls for consent, cadence, and regulatory guardrails".

regf-guard is an independent, open second check on exactly those controls. The 7-in-7, prior-consent and time-zone rules are tested against the CFPB's own worked examples from the Official Interpretations, so anyone can read a test and see which paragraph it encodes.

It also has an `in_rbi` profile (8:00 a.m. to 7:00 p.m. IST calling window from RBI's 12 Aug 2022 recovery-agent instructions) for Skit's India collections deployments.

Skit doesn't publish a public API, so nothing here calls Skit. The input is a plain JSONL call log plus transcript JSON, which any dialer or bot platform can export.

## What it checks

Call log (`regf_guard/cadence.py`):

| Rule | Source |
|---|---|
| `MORE_THAN_7_IN_7_DAYS`: 8th counted call to a person about a debt in 7 consecutive days | 12 CFR 1006.14(b)(2)(i)(A) |
| `CALL_WITHIN_7_DAYS_OF_CONVERSATION`: a counted call within 7 days of a phone conversation (conversation day is day 1, inbound calls included) | 1006.14(b)(2)(i)(B) |
| Exclusions: busy / not in service, calls to the consumer's attorney, creditor and others in 1006.6(d)(1)(ii)-(vi), and calls under prior consent (max 7 days, ends at the next conversation or on revocation) | 1006.14(b)(3), comment 14(b)(3)(i)-2 |
| `OUTSIDE_CALLING_HOURS`: before 8:00 a.m. or after 9:00 p.m. in **any** zone the consumer might be in (area code vs address) | 1006.6(b)(1)(i), comment 6(b)(1)(i)-2 |
| `PHONE_OPT_OUT`, `CEASE_NOTICE` (all debts for that person), `ATTORNEY_REPRESENTED` (that debt) | 1006.14(h), 1006.6(c), 1006.6(b)(2) |
| `RAPID_SUCCESSION` (2 calls in 5 minutes), `CONCENTRATED_CALLS` (6+ on one day): warnings, since they can rebut the presumption | comment 14(b)(2)(i)-2.i |

Transcript (`regf_guard/transcript.py`):

| Rule | Source |
|---|---|
| `DEBT_DISCLOSED_BEFORE_RPC`: the bot mentions the debt before right-party contact is confirmed (spouse, voicemail) | 1006.6(d)(1) |
| `MINI_MIRANDA_MISSING`: initial call without "attempting to collect a debt ... used for that purpose", or later call without "from a debt collector" | 1006.18(e)(1), (e)(2) |
| `ARREST_OR_SEIZURE_THREAT` (arrest, jail, police, garnishment, seizure) / `LEGAL_ACTION_MENTION` (warning) | 1006.18(b)(3), 1006.18(c)(1) |
| `PROFANITY` | 1006.14(d) |
| "Stop calling", "talk to my lawyer", "call me back Friday", "not my debt" | turned into `opt_out_phone`, `attorney_represented`, `consent` events, or a `DISPUTE_RAISED` note |

The last row is the useful link: a "stop calling me" in Monday's bot call blocks Tuesday's dialer retry, and a "call me back on Friday" makes Friday's call fine when the 7-day rule would otherwise flag it.

## Quickstart

```bash
uv sync
uv run pytest -q
uv run regf-guard audit --calls fixtures/calls.jsonl --accounts fixtures/accounts.json --transcripts fixtures/transcripts
uv run regf-guard next-call --calls fixtures/calls.jsonl --accounts fixtures/accounts.json \
    --transcripts fixtures/transcripts --person p-li --debt sl-4004 --now 2026-10-17T11:00:00-06:00
```

MCP (Claude Desktop / Claude Code / Cursor):

```json
{"mcpServers": {"regf-guard": {"command": "uv", "args": ["--directory", "/path/to/regf-guard", "run", "regf-guard", "serve"]}}}
```

Tools: `next_allowed_call`, `audit_call_log`, `audit_transcript`, `list_rules`. None of them dial, send or write anything.

### Input format

One event per line. `ts` must carry a UTC offset.

```json
{"id": "r2", "ts": "2026-10-14T18:30:00-05:00", "person": "p-raj", "debt": "al-2002", "kind": "call", "outcome": "conversation"}
```

- `kind`: `call` (default), `inbound`, `consent`, `consent_revoked`, `opt_out_phone`, `attorney_represented`, `cease`
- `outcome`: `no_answer`, `voicemail`, `conversation`, `third_party_conversation` (connected, counted, but someone else answered), `busy`, `not_in_service`, `failed`
- `recipient_role`: `consumer` (default), `third_party`, `consumer_attorney`, `creditor`, ...

Accounts map each person to every IANA zone they might be in. The first zone is used to count calendar days.

```json
{"p-anna": {"timezones": ["America/Los_Angeles", "America/New_York"]}}
```

## Demo run

The demo portfolio in `fixtures/` is synthetic: 5 consumers, 19 dialer events and 5 bot transcripts over one week in October 2026. Output pasted from an actual run:

```
$ uv run regf-guard audit --calls fixtures/calls.jsonl --accounts fixtures/accounts.json --transcripts fixtures/transcripts
profile us_regf: 19 calls, 5 transcripts, 9 presumed violations, 2 warnings

Call log
  [WARN] 2026-10-12T09:03  p-li/sl-4004  RAPID_SUCCESSION
         second counted call to the same number within 5 minutes (previous 2026-10-12T09:00:00-06:00)  (12 CFR 1006 Supp. I, comment 14(b)(2)(i)-2.i)
  [VIOL] 2026-10-12T09:30  p-anna/cc-1001  OUTSIDE_CALLING_HOURS
         outside 08:00-21:00 local time in: America/Los_Angeles 06:30  (12 CFR 1006.6(b)(1)(i))
  [VIOL] 2026-10-13T10:00  p-maria/md-3003  CALL_WITHIN_7_DAYS_OF_CONVERSATION
         telephone conversation on 2026-10-12; next counted call allowed from 2026-10-19  (12 CFR 1006.14(b)(2)(i)(B))
  [VIOL] 2026-10-13T10:00  p-maria/md-3003  PHONE_OPT_OUT
         call placed after person asked not to be called by phone on 2026-10-12T14:00:04-04:00  (12 CFR 1006.14(h)(1))
  [VIOL] 2026-10-15T11:00  p-raj/al-2002  CALL_WITHIN_7_DAYS_OF_CONVERSATION
         telephone conversation on 2026-10-14; next counted call allowed from 2026-10-21  (12 CFR 1006.14(b)(2)(i)(B))
  [VIOL] 2026-10-17T10:00  p-li/sl-4004  MORE_THAN_7_IN_7_DAYS
         call #8 in the 7 days 2026-10-11 to 2026-10-17  (12 CFR 1006.14(b)(2)(i)(A))

Transcript a2 (RPC at turn None)
  [VIOL] t0 DEBT_DISCLOSED_BEFORE_RPC: agent mentioned the debt and right-party contact was never confirmed  (12 CFR 1006.6(d)(1))
         "Hi Anna, this is Northstar Recovery. If you don't pay your past due balance of $2,300 by Friday, we will move this to legal and you could face wage garnishment."
  [VIOL] t0 ARREST_OR_SEIZURE_THREAT: arrest/seizure language; only lawful and intended actions may be mentioned  (12 CFR 1006.18(b)(3))
         "Hi Anna, this is Northstar Recovery. If you don't pay your past due balance of $2,300 by Friday, we will move this to legal and you could face wage garnishment."

Transcript m1 (RPC at turn 1)
  [WARN] t3 PHONE_OPT_OUT_REQUESTED: customer asked to stop phone calls; future calls are blocked; agent kept asking for payment at turn 4  (12 CFR 1006.14(h)(1))
         "Please stop calling me. I'll pay it online."

Transcript r1 (RPC at turn None)
  [VIOL] t0 DEBT_DISCLOSED_BEFORE_RPC: agent mentioned the debt and right-party contact was never confirmed  (12 CFR 1006.6(d)(1))
         "Hi, this is Ava, a virtual assistant calling from Northstar about the overdue payment on Raj Patel's auto loan. Am I speaking with Raj?"
  [VIOL] t2 DEBT_DISCLOSED_BEFORE_RPC: agent mentioned the debt and right-party contact was never confirmed  (12 CFR 1006.6(d)(1))
         "No problem. Please tell him his balance of $1,240 is past due and he should call us back."

Transcript r2 (RPC at turn 1)
  clean

Transcript s1 (RPC at turn 1)
  [INFO] t3 CALLBACK_CONSENT: consumer invited a callback; treated as prior consent for up to 7 days (confirm)  (12 CFR 1006.14(b)(3)(i))
         "Can you call me back on Friday? I'll have my paycheck by then."
```

Things worth noticing:

- p-anna's 9:30 a.m. call is fine in New York (her 212 mobile) and 6:30 a.m. at her California address. The rule says the call has to be convenient in both.
- The Raj spouse call never got right-party contact, and the bot named the auto loan in its first sentence.
- Without transcripts, Sam's Friday call is flagged as within 7 days of Tuesday's conversation. With them, "call me back on Friday" counts as prior consent and the flag goes away. Maria's case goes the other way: her "stop calling me" is what turns Tuesday's retry into an opt-out violation.
- p-li's 7th call (Oct 15 12:00) was busy, so it doesn't count; the 8th counted call lands on Oct 17.

Pre-dial guard for the same consumer, asked right after that 8th call:

```
$ uv run regf-guard next-call --calls fixtures/calls.jsonl --accounts fixtures/accounts.json \
    --transcripts fixtures/transcripts --person p-li --debt sl-4004 --now 2026-10-17T11:00:00-06:00
{
  "person": "p-li",
  "debt": "sl-4004",
  "now": "2026-10-17T11:00:00-06:00",
  "profile": "us_regf",
  "allowed_now": false,
  "next_allowed": "2026-10-19T14:00:00+00:00",
  "local_times": {
    "America/Denver": "2026-10-19 08:00"
  },
  "skipped": [
    "2026-10-17: would be call #9 in 7 days (1006.14(b)(2)(i)(A))",
    "2026-10-18: would be call #9 in 7 days (1006.14(b)(2)(i)(A))"
  ],
  "blocked": null
}
```

The same call over real MCP stdio (`uv run python scripts/stdio_smoke.py`, which starts `regf-guard serve` as a subprocess):

```
tools: audit_call_log, audit_transcript, list_rules, next_allowed_call
p-li next call: 2026-10-19T14:00:00+00:00 {'America/Denver': '2026-10-19 08:00'}
```

## Tests

```
$ uv run pytest -q
....................................                                     [100%]
36 passed in 0.51s
```

`tests/test_cfpb_examples.py` replays the CFPB Official Interpretation examples: comments 14(b)(2)(i)-1.i to .iii, 14(b)(2)(ii)-1.i and .ii, 14(b)(3)(i)-3.i to .iii, 14(b)(2)(i)-2.i, and both time-zone examples in 6(b)(1)(i)-2. The commentary gives weekdays without a year; they match 2026, so the tests use 2026 dates. Rule text was read from the eCFR (title 12, part 1006, current as of 2026-10-22).

## Limits (read before trusting it)

- **Not legal advice.** A presumption is rebuttable both ways; this flags the presumption, a compliance person decides.
- Transcript checks are regex patterns (English plus a few Hinglish phrasings). They catch the plain cases in the demo and will miss paraphrases. The obvious next step is an LLM judge for the ambiguous turns, with these rules as the cheap first pass.
- Federal Reg F only. No state rules (some states cap calls tighter than 7-in-7), no TCPA consent or autodialer rules, no email/SMS cadence.
- Calendar days are counted in the first listed zone. Prior consent detected from a transcript is marked "confirm", since a callback request is a judgment call.
- The demo data is synthetic. No Skit system, API or recording was used.
