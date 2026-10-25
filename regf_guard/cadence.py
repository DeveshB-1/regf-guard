"""Call-cadence rules for collection calls.

US profile (`us_regf`) encodes 12 CFR part 1006 (Regulation F):

* 1006.14(b)(2)(i)(A): no more than 7 calls to a person about a particular debt
  within 7 consecutive days.
* 1006.14(b)(2)(i)(B): no call within 7 consecutive days after a telephone
  conversation about that debt (the conversation date is day 1).
* 1006.14(b)(3): unconnected calls, calls under prior consent (up to 7 days,
  consent date is day 1, expires on revocation or the next conversation) and
  calls to the persons in 1006.6(d)(1)(ii)-(vi) do not count.
* 1006.6(b)(1)(i): no call before 8:00 a.m. or after 9:00 p.m. local time; with
  conflicting location data the call must be convenient in every location.
* 1006.14(h), 1006.6(b)(2), 1006.6(c): no further calls after a phone opt-out,
  attorney representation, or a cease-communication notice.
* Comment 14(b)(2)(i)-2.i: two calls within 5 minutes, or a day as concentrated
  as the commentary's example (6 calls on one day), keep the presumption but can
  rebut it. Reported as warnings.

India profile (`in_rbi`) encodes the 8:00 a.m. to 7:00 p.m. calling window from
RBI's 12 Aug 2022 instructions on recovery agents. RBI sets no 7-in-7 count, so
the frequency rules are off.

Calendar days are counted in the first time zone listed for the person.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo

NOT_CONNECTED = {"busy", "not_in_service", "failed", "no_connect"}
# 1006.6(d)(1)(ii)-(vi): calls to these people never count toward 7-in-7.
EXEMPT_ROLES = {
    "consumer_attorney",
    "consumer_reporting_agency",
    "creditor",
    "creditor_attorney",
    "collector_attorney",
}
BLOCKING_KINDS = {
    "opt_out_phone": ("PHONE_OPT_OUT", "12 CFR 1006.14(h)(1)", "person asked not to be called by phone"),
    "attorney_represented": ("ATTORNEY_REPRESENTED", "12 CFR 1006.6(b)(2)", "consumer is represented by an attorney"),
    "cease": ("CEASE_NOTICE", "12 CFR 1006.6(c)(1)", "consumer sent a cease-communication or refusal-to-pay notice"),
}
CONCENTRATED_PER_DAY = 6  # comment 14(b)(2)(i)-2.i example: six calls on Friday, April 3
EVENT_KINDS = {"call", "inbound", "consent", "consent_revoked", *BLOCKING_KINDS}


@dataclass(frozen=True)
class Profile:
    name: str
    start: time
    end: time
    frequency: bool
    time_cite: str


PROFILES = {
    "us_regf": Profile("us_regf", time(8, 0), time(21, 0), True, "12 CFR 1006.6(b)(1)(i)"),
    "in_rbi": Profile("in_rbi", time(8, 0), time(19, 0), False, "RBI recovery-agent instructions, 12 Aug 2022"),
}


@dataclass
class Event:
    ts: datetime
    person: str
    debt: str
    kind: str = "call"
    outcome: str = "no_answer"
    recipient_role: str = "consumer"
    number: str = ""
    consent_days: int = 7
    id: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Event":
        ts = datetime.fromisoformat(str(d["ts"]).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            raise ValueError(f"event {d.get('id', '?')}: ts must carry a UTC offset, got {d['ts']!r}")
        kind = d.get("kind", "call")
        if kind not in EVENT_KINDS:
            raise ValueError(f"event {d.get('id', '?')}: unknown kind {kind!r}")
        return cls(
            ts=ts,
            person=str(d["person"]),
            debt=str(d["debt"]),
            kind=kind,
            outcome=d.get("outcome", "no_answer"),
            recipient_role=d.get("recipient_role", "consumer"),
            number=str(d.get("number", "")),
            consent_days=int(d.get("consent_days", 7)),
            id=str(d.get("id", "")),
        )


@dataclass
class Finding:
    rule: str
    severity: str  # "violation" (presumed violation) or "warning"
    person: str
    debt: str
    ts: str
    event_id: str
    message: str
    cite: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _State:
    tz: ZoneInfo
    counted: list[tuple[datetime, str]] = field(default_factory=list)  # (ts, number)
    last_conversation: date | None = None
    consent_start: date | None = None
    consent_end: date | None = None
    consent_ts: datetime | None = None
    blocks: dict[str, Event] = field(default_factory=dict)
    warned_days: set[date] = field(default_factory=set)

    def local_date(self, ts: datetime) -> date:
        return ts.astimezone(self.tz).date()

    def consent_covers(self, d: date) -> bool:
        return self.consent_start is not None and self.consent_start <= d <= self.consent_end  # type: ignore[operator]

    def drop_consent(self) -> None:
        self.consent_start = self.consent_end = self.consent_ts = None

    def counted_in_window(self, d: date) -> int:
        lo = d - timedelta(days=6)
        return sum(1 for ts, _ in self.counted if lo <= self.local_date(ts) <= d)


def _zones(names: Iterable[str]) -> list[ZoneInfo]:
    zones = [ZoneInfo(n) for n in names]
    if not zones:
        raise ValueError("at least one time zone is required")
    return zones


def outside_hours(ts: datetime, zones: list[ZoneInfo], profile: Profile) -> list[str]:
    """Return the zones where `ts` falls outside the profile's calling window."""
    bad = []
    for z in zones:
        local = ts.astimezone(z)
        t = local.time()
        if t < profile.start or t > profile.end:
            bad.append(f"{z.key} {local:%H:%M}")
    return bad


def _apply(state: _State, ev: Event, zones: list[ZoneInfo], profile: Profile, out: list[Finding] | None) -> None:
    """Advance per-(person, debt) state by one event, recording findings if `out` is given."""

    def flag(rule: str, severity: str, message: str, cite: str) -> None:
        if out is not None:
            out.append(Finding(rule, severity, ev.person, ev.debt, ev.ts.isoformat(), ev.id, message, cite))

    d = state.local_date(ev.ts)

    if ev.kind == "consent":
        state.consent_start = d
        state.consent_end = d + timedelta(days=max(1, min(ev.consent_days, 7)) - 1)
        state.consent_ts = ev.ts
        return
    if ev.kind == "consent_revoked":
        state.drop_consent()
        return
    if ev.kind in BLOCKING_KINDS:
        state.blocks.setdefault(ev.kind, ev)
        return

    conversation = ev.outcome == "conversation"
    if ev.kind == "inbound":
        if conversation:
            state.last_conversation = d
            if state.consent_ts is not None and ev.ts > state.consent_ts:
                state.drop_consent()
        return

    # ev.kind == "call"
    to_consumer = ev.recipient_role == "consumer"
    if to_consumer:
        for kind, prior in state.blocks.items():
            rule, cite, why = BLOCKING_KINDS[kind]
            flag(rule, "violation", f"call placed after {why} on {prior.ts.isoformat()}", cite)
        bad = outside_hours(ev.ts, zones, profile)
        if bad:
            window = f"{profile.start:%H:%M}-{profile.end:%H:%M}"
            flag("OUTSIDE_CALLING_HOURS", "violation", f"outside {window} local time in: {', '.join(bad)}", profile.time_cite)

    if profile.frequency:
        excluded = (
            ev.outcome in NOT_CONNECTED
            or ev.recipient_role in EXEMPT_ROLES
            or state.consent_covers(d)
        )
        if not excluded:
            if state.last_conversation is not None and d <= state.last_conversation + timedelta(days=6):
                flag(
                    "CALL_WITHIN_7_DAYS_OF_CONVERSATION",
                    "violation",
                    f"telephone conversation on {state.last_conversation}; next counted call allowed from "
                    f"{state.last_conversation + timedelta(days=7)}",
                    "12 CFR 1006.14(b)(2)(i)(B)",
                )
            state.counted.append((ev.ts, ev.number))
            n = state.counted_in_window(d)
            if n > 7:
                flag(
                    "MORE_THAN_7_IN_7_DAYS",
                    "violation",
                    f"call #{n} in the 7 days {d - timedelta(days=6)} to {d}",
                    "12 CFR 1006.14(b)(2)(i)(A)",
                )
            _concentration(state, ev, d, flag)

    if conversation:
        state.last_conversation = d
        if state.consent_ts is not None and ev.ts > state.consent_ts:
            state.drop_consent()


def _concentration(state: _State, ev: Event, d: date, flag) -> None:
    key = ev.number or ev.person
    for ts, num in state.counted[:-1]:
        if (num or ev.person) == key and timedelta(0) <= ev.ts - ts <= timedelta(minutes=5):
            flag(
                "RAPID_SUCCESSION",
                "warning",
                f"second counted call to the same number within 5 minutes (previous {ts.isoformat()})",
                "12 CFR 1006 Supp. I, comment 14(b)(2)(i)-2.i",
            )
            break
    same_day = sum(1 for ts, _ in state.counted if state.local_date(ts) == d)
    if same_day >= CONCENTRATED_PER_DAY and d not in state.warned_days:
        state.warned_days.add(d)
        flag(
            "CONCENTRATED_CALLS",
            "warning",
            f"{same_day} counted calls on {d}",
            "12 CFR 1006 Supp. I, comment 14(b)(2)(i)-2.i",
        )


def _group(events: Iterable[Event]) -> dict[tuple[str, str], list[Event]]:
    groups: dict[tuple[str, str], list[Event]] = {}
    for ev in events:
        groups.setdefault((ev.person, ev.debt), []).append(ev)
    for evs in groups.values():
        evs.sort(key=lambda e: e.ts)
    return groups


def _person_blocks(events: Iterable[Event]) -> dict[str, list[Event]]:
    """Opt-outs and cease notices apply to every debt the person has with the collector."""
    out: dict[str, list[Event]] = {}
    for ev in events:
        if ev.kind in ("opt_out_phone", "cease"):
            out.setdefault(ev.person, []).append(ev)
    return out


def audit_events(
    events: Iterable[Event],
    timezones: dict[str, list[str]],
    profile: str = "us_regf",
) -> list[Finding]:
    """Replay a call log and return every presumed violation and warning."""
    prof = PROFILES[profile]
    events = list(events)
    blocks = _person_blocks(events)
    findings: list[Finding] = []
    for (person, debt), evs in _group(events).items():
        if person not in timezones:
            raise ValueError(f"no time zones on file for person {person!r}")
        zones = _zones(timezones[person])
        extra = [
            Event(ts=b.ts, person=person, debt=debt, kind=b.kind, id=b.id)
            for b in blocks.get(person, [])
            if b.debt != debt
        ]
        state = _State(tz=zones[0])
        for ev in sorted(evs + extra, key=lambda e: e.ts):
            _apply(state, ev, zones, prof, findings)
    findings.sort(key=lambda f: (f.ts, f.rule))
    return findings


def _day_windows(d: date, zones: list[ZoneInfo], profile: Profile) -> list[tuple[datetime, datetime]]:
    """UTC intervals inside primary-zone date `d` when every zone is within calling hours."""
    primary = zones[0]
    day_lo = datetime.combine(d, time(0, 0), primary).astimezone(timezone.utc)
    day_hi = datetime.combine(d + timedelta(days=1), time(0, 0), primary).astimezone(timezone.utc)
    allowed = [(day_lo, day_hi)]
    for z in zones:
        zone_iv = []
        for k in (-1, 0, 1):
            ld = d + timedelta(days=k)
            zone_iv.append(
                (
                    datetime.combine(ld, profile.start, z).astimezone(timezone.utc),
                    datetime.combine(ld, profile.end, z).astimezone(timezone.utc),
                )
            )
        allowed = [
            (max(a0, b0), min(a1, b1))
            for a0, a1 in allowed
            for b0, b1 in zone_iv
            if max(a0, b0) < min(a1, b1)
        ]
    return sorted(allowed)


def next_allowed_call(
    events: Iterable[Event],
    person: str,
    debt: str,
    timezones: list[str],
    now: datetime,
    profile: str = "us_regf",
    horizon_days: int = 30,
) -> dict[str, Any]:
    """Earliest moment at or after `now` when one more call keeps every presumption of compliance.

    The call is assumed to connect and be placed to the consumer (the conservative case).
    """
    prof = PROFILES[profile]
    zones = _zones(timezones)
    events = list(events)
    state = _State(tz=zones[0])
    blocks = _person_blocks(events)
    history = [e for e in events if e.person == person and e.debt == debt and e.ts <= now]
    history += [
        Event(ts=b.ts, person=person, debt=debt, kind=b.kind, id=b.id)
        for b in blocks.get(person, [])
        if b.debt != debt and b.ts <= now
    ]
    for ev in sorted(history, key=lambda e: e.ts):
        _apply(state, ev, zones, prof, None)

    base = {"person": person, "debt": debt, "now": now.isoformat(), "profile": prof.name}
    if state.blocks:
        kind, ev = next(iter(state.blocks.items()))
        rule, cite, why = BLOCKING_KINDS[kind]
        return {**base, "allowed_now": False, "next_allowed": None, "blocked": rule,
                "reason": f"{why} on {ev.ts.isoformat()}; no further calls", "cite": cite}

    reasons: list[str] = []
    start_day = state.local_date(now)
    for k in range(horizon_days + 1):
        d = start_day + timedelta(days=k)
        if prof.frequency and not state.consent_covers(d):
            if state.last_conversation is not None and d <= state.last_conversation + timedelta(days=6):
                reasons.append(f"{d}: within 7 days of the {state.last_conversation} conversation (1006.14(b)(2)(i)(B))")
                continue
            if state.counted_in_window(d) + 1 > 7:
                reasons.append(f"{d}: would be call #{state.counted_in_window(d) + 1} in 7 days (1006.14(b)(2)(i)(A))")
                continue
        for lo, hi in _day_windows(d, zones, prof):
            if hi <= now:
                continue
            at = max(lo, now.astimezone(timezone.utc))
            local = {z.key: at.astimezone(z).strftime("%Y-%m-%d %H:%M") for z in zones}
            return {**base, "allowed_now": at <= now.astimezone(timezone.utc), "next_allowed": at.isoformat(),
                    "local_times": local, "skipped": reasons[:10], "blocked": None}
        if k == 0:
            reasons.append(f"{d}: calling hours already over or no hour convenient in every zone")
    return {**base, "allowed_now": False, "next_allowed": None, "blocked": "NO_WINDOW",
            "reason": f"no compliant slot in {horizon_days} days", "skipped": reasons[:10]}
