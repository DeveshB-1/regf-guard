"""The worked examples from the CFPB's Official Interpretations (12 CFR part 1006, Supplement I),
replayed as tests. Weekdays in the commentary line up with 2026, so that year is used.
Every call is at 10:00 Eastern, inside calling hours, unless the example is about hours.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from regf_guard.cadence import Event, audit_events, next_allowed_call

ET = ZoneInfo("America/New_York")
ZONES = {"c1": ["America/New_York"]}


def at(month, day, hour=10, minute=0, tz=ET):
    return datetime(2026, month, day, hour, minute, tzinfo=tz)


def call(ts, outcome="no_answer", **kw):
    return Event(ts=ts, person="c1", debt="d1", kind="call", outcome=outcome, **kw)


def rules(findings):
    return [f.rule for f in findings if f.severity == "violation"]


def test_14b2i_1i_seven_calls_in_seven_days_is_presumed_compliant():
    # Wed Apr 1 limited-content message, six more unanswered calls Apr 2-7, next call Apr 8.
    evs = [call(at(4, 1), "voicemail")] + [call(at(4, d)) for d in range(2, 8)] + [call(at(4, 8))]
    assert rules(audit_events(evs, ZONES)) == []


def test_14b2i_1ii_consumer_initiated_conversation_starts_the_7_days():
    # Consumer calls in Thu Aug 13; collector waits until Thu Aug 20.
    evs = [Event(ts=at(8, 13), person="c1", debt="d1", kind="inbound", outcome="conversation"), call(at(8, 20))]
    assert rules(audit_events(evs, ZONES)) == []
    # Calling on Wed Aug 19 would be inside the 7-day period.
    evs[1] = call(at(8, 19))
    assert rules(audit_events(evs, ZONES)) == ["CALL_WITHIN_7_DAYS_OF_CONVERSATION"]


def test_14b2i_1iii_seven_calls_to_a_third_party_for_location_info():
    evs = [call(at(10, d), recipient_role="third_party") for d in range(6, 13)]
    assert rules(audit_events(evs, ZONES)) == []


def test_14b2ii_1i_eighth_call_in_seven_days_is_presumed_violation():
    # Apr 1 message; two calls each on Apr 2, Apr 3, Apr 6; one on Apr 7 -> 8 calls.
    evs = [call(at(4, 1), "voicemail")]
    for d in (2, 3, 6):
        evs += [call(at(4, d, 10)), call(at(4, d, 15))]
    evs.append(call(at(4, 7)))
    found = audit_events(evs, ZONES)
    assert rules(found) == ["MORE_THAN_7_IN_7_DAYS"]
    assert found[0].ts.startswith("2026-04-07")


def test_14b2ii_1ii_call_before_aug_21_after_aug_14_conversation():
    base = [call(at(8, 11)), call(at(8, 14), "conversation")]
    assert rules(audit_events(base + [call(at(8, 20))], ZONES)) == ["CALL_WITHIN_7_DAYS_OF_CONVERSATION"]
    assert rules(audit_events(base + [call(at(8, 21))], ZONES)) == []


def test_14b3i_3i_call_back_on_monday_is_prior_consent():
    # Fri Apr 3 conversation, consumer says "call back on Monday"; collector calls Mon Apr 6.
    evs = [
        call(at(4, 3), "conversation"),
        Event(ts=at(4, 3, 10, 5), person="c1", debt="d1", kind="consent"),
        call(at(4, 6)),
    ]
    assert rules(audit_events(evs, ZONES)) == []
    # Without the consent the Monday call is a presumed violation.
    assert rules(audit_events([evs[0], evs[2]], ZONES)) == ["CALL_WITHIN_7_DAYS_OF_CONVERSATION"]


def test_14b3i_3ii_consent_ends_at_the_next_conversation():
    evs = [
        call(at(4, 3), "conversation"),
        Event(ts=at(4, 3, 10, 5), person="c1", debt="d1", kind="consent"),
        call(at(4, 6), "conversation"),  # allowed under consent, and it ends the consent
        call(at(4, 8)),  # counts again, inside 7 days of the Apr 6 conversation
    ]
    found = audit_events(evs, ZONES)
    assert rules(found) == ["CALL_WITHIN_7_DAYS_OF_CONVERSATION"]
    assert found[0].ts.startswith("2026-04-08")


def test_14b3i_3iii_email_consent_covers_june_4_to_10():
    evs = [call(at(6, d)) for d in (1, 2, 3)]
    evs.append(Event(ts=at(6, 4, 9), person="c1", debt="d1", kind="consent"))
    evs += [call(at(6, 8, 9)), call(at(6, 8, 13)), call(at(6, 9, 9)), call(at(6, 9, 13)),
            call(at(6, 9, 17)), call(at(6, 10, 9)), call(at(6, 10, 13)), call(at(6, 10, 17))]
    assert rules(audit_events(evs, ZONES)) == []
    # Consent covers June 4-10 only. From June 11 calls count again, and only the June 1-3
    # calls are left in any 7-day window, so June 11 at 08:00 ET is the next slot.
    assert next_allowed_call(evs, "c1", "d1", ["America/New_York"], at(6, 10, 18))["allowed_now"] is True
    nxt = next_allowed_call(evs, "c1", "d1", ["America/New_York"], at(6, 10, 21, 30))
    assert nxt["next_allowed"].startswith("2026-06-11T12:00")  # 08:00 ET


def test_14b3ii_busy_and_not_in_service_do_not_count():
    evs = [call(at(4, 1, 9 + i), "busy") for i in range(10)]
    assert audit_events(evs, ZONES) == []


def test_14b2i_2i_six_calls_in_one_day_is_a_warning_not_a_violation():
    # Same as 1.i, but the six later calls all land on Friday Apr 3: presumed compliant,
    # and the concentration is a factor that may rebut the presumption.
    evs = [call(at(4, 1))] + [call(at(4, 3, 9 + i)) for i in range(6)]
    found = audit_events(evs, ZONES)
    assert rules(found) == []
    assert [f.rule for f in found] == ["CONCENTRATED_CALLS"]
    assert audit_events(evs[:-1], ZONES) == []  # five on one day: no warning


def test_two_calls_within_five_minutes_warns():
    found = audit_events([call(at(4, 1, 10, 0)), call(at(4, 1, 10, 3))], ZONES)
    assert [f.rule for f in found] == ["RAPID_SUCCESSION"]


def test_6b1i_2i_eastern_area_code_pacific_address():
    zones = {"c1": ["America/New_York", "America/Los_Angeles"]}
    assert rules(audit_events([call(at(4, 1, 10, 30))], zones)) == ["OUTSIDE_CALLING_HOURS"]  # 7:30 PT
    assert rules(audit_events([call(at(4, 1, 21, 15))], zones)) == ["OUTSIDE_CALLING_HOURS"]  # 21:15 ET
    assert rules(audit_events([call(at(4, 1, 11, 0))], zones)) == []
    nxt = next_allowed_call([], "c1", "d1", zones["c1"], at(4, 1, 7, 0))
    assert nxt["local_times"] == {"America/New_York": "2026-04-01 11:00", "America/Los_Angeles": "2026-04-01 08:00"}


def test_6b1i_2ii_eastern_mobile_mountain_landline():
    nxt = next_allowed_call([], "c1", "d1", ["America/New_York", "America/Denver"], at(4, 1, 6, 0))
    assert nxt["local_times"]["America/New_York"] == "2026-04-01 10:00"
    # After 9 pm Eastern (7 pm Mountain) the next slot is the following morning.
    nxt = next_allowed_call([], "c1", "d1", ["America/New_York", "America/Denver"], at(4, 1, 21, 1))
    assert nxt["local_times"]["America/New_York"] == "2026-04-02 10:00"


def test_next_allowed_after_eight_calls_waits_for_the_window_to_roll():
    evs = [call(at(4, 1)), *[call(at(4, d)) for d in range(2, 8)]]  # 7 calls Apr 1-7
    nxt = next_allowed_call(evs, "c1", "d1", ["America/New_York"], at(4, 7, 12))
    assert nxt["allowed_now"] is False
    assert nxt["next_allowed"].startswith("2026-04-08T12:00")  # 08:00 ET on Apr 8


def test_inside_window_now_is_allowed_now():
    nxt = next_allowed_call([], "c1", "d1", ["America/New_York"], at(4, 1, 12))
    assert nxt["allowed_now"] is True


def test_opt_out_blocks_calls_on_every_debt():
    evs = [
        Event(ts=at(4, 1), person="c1", debt="d1", kind="opt_out_phone"),
        Event(ts=at(4, 2), person="c1", debt="d2", kind="call"),
    ]
    assert rules(audit_events(evs, ZONES)) == ["PHONE_OPT_OUT"]
    assert next_allowed_call(evs, "c1", "d2", ["America/New_York"], at(4, 3))["blocked"] == "PHONE_OPT_OUT"


def test_india_profile_uses_8_to_7_and_no_frequency_cap():
    ist = ZoneInfo("Asia/Kolkata")
    zones = {"c1": ["Asia/Kolkata"]}
    evs = [call(datetime(2026, 4, 1, 9 + i, tzinfo=ist)) for i in range(9)]  # 09:00-17:00
    assert rules(audit_events(evs, zones, "in_rbi")) == []
    late = [call(datetime(2026, 4, 1, 19, 30, tzinfo=ist))]
    assert rules(audit_events(late, zones, "in_rbi")) == ["OUTSIDE_CALLING_HOURS"]
    assert rules(audit_events(late, zones, "us_regf")) == []
    nxt = next_allowed_call(late, "c1", "d1", ["Asia/Kolkata"], datetime(2026, 4, 1, 19, 30, tzinfo=ist), "in_rbi")
    assert nxt["local_times"]["Asia/Kolkata"] == "2026-04-02 08:00"


def test_days_are_counted_in_the_consumers_zone_not_utc():
    # 23:30 ET on Apr 7 is Apr 8 in UTC; it must still count toward Apr 1-7.
    evs = [call(at(4, 1)), *[call(at(4, d)) for d in range(2, 7)], call(at(4, 7, 20, 30)), call(at(4, 7, 20, 50))]
    found = audit_events(evs, ZONES)
    assert "MORE_THAN_7_IN_7_DAYS" in rules(found)
    assert datetime.fromisoformat(found[-1].ts).astimezone(ZoneInfo("UTC")).day == 8
