"""Schedule arithmetic: pure, deterministic, daylight-saving aware."""

from datetime import datetime, timedelta, timezone

import pytest

from aei_workflow.schedule import MAX_OCCURRENCES, next_occurrence, occurrences
from aei_workflow.schema import SchemaError
from aei_workflow.workflow import new_workflow, validate_schedule, validate_timezone

U = timezone.utc


def dt(*a):
    return datetime(*a, tzinfo=U)


def test_manual_has_no_occurrences():
    assert occurrences({"kind": "manual"}, "UTC", dt(2026, 1, 1), dt(2026, 2, 1)) == ([], False)
    assert next_occurrence({"kind": "manual"}, "UTC", dt(2026, 1, 1)) is None


def test_interval_counts_from_the_anchor_and_excludes_the_start_includes_the_end():
    a = dt(2026, 9, 26, 0, 0)
    got, trunc = occurrences({"kind": "interval", "every_minutes": 60}, "UTC", a, a + timedelta(hours=3), anchor=a)
    assert got == [a + timedelta(hours=h) for h in (1, 2, 3)] and not trunc
    # a window that starts mid-interval
    got, _ = occurrences({"kind": "interval", "every_minutes": 60}, "UTC", a + timedelta(minutes=90), a + timedelta(hours=4), anchor=a)
    assert got == [a + timedelta(hours=2), a + timedelta(hours=3), a + timedelta(hours=4)]


def test_daily_follows_the_local_wall_clock_across_daylight_saving():
    s = {"kind": "daily", "at": "02:00"}
    got, _ = occurrences(s, "America/Toronto", dt(2026, 3, 6, 12), dt(2026, 3, 11, 12))
    assert [t.hour for t in got] == [7, 7, 6, 6, 6]          # 02:00 EST = 07:00 UTC until Mar 8, then EDT = 06:00 UTC


def test_a_time_inside_the_spring_forward_gap_happens_once_after_the_gap():
    got, _ = occurrences({"kind": "daily", "at": "02:30"}, "America/Toronto", dt(2026, 3, 7, 12), dt(2026, 3, 9, 12))
    assert got == [dt(2026, 3, 8, 7, 30), dt(2026, 3, 9, 6, 30)]     # Mar 8 02:30 does not exist -> 03:30 EDT, exactly once


def test_a_time_repeated_on_the_fall_back_day_happens_once_at_its_first_occurrence():
    got, _ = occurrences({"kind": "daily", "at": "01:30"}, "America/Toronto", dt(2026, 10, 31, 12), dt(2026, 11, 2, 12))
    # Nov 1 has two 01:30s (EDT 05:30 UTC and EST 06:30 UTC): exactly one happens, the first. Nov 2 is an ordinary EST day.
    assert got == [dt(2026, 11, 1, 5, 30), dt(2026, 11, 2, 6, 30)]


def test_weekly_only_on_the_named_days_in_the_named_zone():
    s = {"kind": "weekly", "days": ["mon", "thu"], "at": "23:30"}
    got, _ = occurrences(s, "America/Toronto", dt(2026, 9, 27), dt(2026, 10, 7))       # Sun Sep 27 .. Wed Oct 7
    local = [t.astimezone(__import__("zoneinfo").ZoneInfo("America/Toronto")) for t in got]
    assert [(l.strftime("%a"), l.hour, l.minute) for l in local] == [("Mon", 23, 30), ("Thu", 23, 30), ("Mon", 23, 30)]


def test_far_too_many_occurrences_returns_the_latest_and_says_so():
    a = dt(2026, 1, 1)
    got, trunc = occurrences({"kind": "interval", "every_minutes": 5}, "UTC", a, a + timedelta(days=60), anchor=a)
    assert trunc and len(got) == MAX_OCCURRENCES and got[-1] == a + timedelta(days=60)


def test_next_occurrence():
    assert next_occurrence({"kind": "daily", "at": "02:00"}, "UTC", dt(2026, 9, 26, 3)) == dt(2026, 9, 27, 2)
    assert next_occurrence({"kind": "interval", "every_minutes": 30}, "UTC", dt(2026, 9, 26, 3, 10), anchor=dt(2026, 9, 26, 0)) == dt(2026, 9, 26, 3, 30)


@pytest.mark.parametrize("bad", [
    {"kind": "cron", "expr": "* * * * *"}, {"kind": "interval"}, {"kind": "interval", "every_minutes": 1}, {"kind": "interval", "every_minutes": 99999},
    {"kind": "daily"}, {"kind": "daily", "at": "25:00"}, {"kind": "daily", "at": "2:00"}, {"kind": "weekly", "at": "02:00", "days": []},
    {"kind": "weekly", "at": "02:00", "days": ["mon", "mon"]}, {"kind": "weekly", "at": "02:00", "days": ["funday"]},
    {"kind": "daily", "at": "02:00", "command": "rm -rf /"}, {"kind": "daily", "at": "02:00", "grace_minutes": -1}, "daily", None,
])
def test_invalid_schedules_are_refused_with_a_reason(bad):
    with pytest.raises(SchemaError):
        validate_schedule(bad)


def test_time_zone_validation():
    validate_timezone("UTC"), validate_timezone("America/Toronto")
    for bad in ("", "Mars/Olympus", None, 5, "../../etc/passwd"):
        with pytest.raises(SchemaError):
            validate_timezone(bad)


def test_new_workflow_carries_the_optional_fields_and_old_documents_without_them_still_validate():
    wf = new_workflow("x", schedule={"kind": "daily", "at": "02:00"}, tz="America/Toronto", retain_hours=24, workflow_version=3)
    assert wf["workflow_version"] == 3 and wf["output"]["retain_hours"] == 24 and wf["timezone"] == "America/Toronto"
    old = {k: v for k, v in wf.items() if k not in ("schedule", "timezone", "workflow_version")}
    old["output"] = {"retain_runs": None}
    from aei_workflow.workflow import validate_workflow
    validate_workflow(old)
    for change in ({"workflow_version": 0}, {"output": {"retain_runs": None, "retain_hours": 0}}, {"output": {"retain_runs": None, "write_evidence": "yes"}}):
        with pytest.raises(SchemaError):
            validate_workflow({**wf, **change})
