"""The times of a schedule (taskloom.schedule) for cron lines you type yourself, at the edges of a minute and around a
clock change; the words the editor shows for a schedule; and how a mistake in a schedule is told.

The editor builds the common schedules, and tests/test_scheduler_promises.py checks those over a whole year. A Custom
cron line, a flow file written by hand or a flow-level `triggers:` list can hold anything else: these tests cover that.
They call the schedule code directly, in this process, so the whole file takes about a second (mutation testing runs
it once for each bug it plants)."""

import datetime as dt
import os
import re
import time

import pytest
import yaml

from taskloom.schedule import Every, Trigger, clock_moment, cron_plan, describe_cron, plan_cron

UTC = dt.timezone.utc
MONDAY = dt.datetime(2026, 1, 5)  # 2026-01-01 is a Thursday, 2026-02-01 a Sunday


def times(cron: str, after: dt.datetime, n: int) -> list[dt.datetime]:
    """The first n times of the cron line `cron` after `after`, each asked after the one before, as the scheduler
    service and the editor ask."""
    trigger, found = Trigger({"schedule": {"cron": cron}}), []
    while len(found) < n and (after := trigger.next_after(after)) is not None:
        found.append(after)
    return found


def refused(data) -> str:
    """The message a trigger is refused with. A ValueError is the mistake the scheduler service, `taskloom validate`
    and the editor catch and show you; any other error would stop them."""
    with pytest.raises(ValueError) as e:
        Trigger(data)
    return str(e.value)


# --- SCH-5: its time, for cron lines the editor does not build ------------------------------------------------------

@pytest.mark.invariant("SCH-5")
def test_weekday_7_is_sunday_as_0_is():
    """Cron counts Sunday as 0 or 7: "0 9 * * 7" runs on Sundays, and "0 9 * * 5-7" on Fridays, Saturdays and Sundays."""
    assert times("0 9 * * 7", MONDAY, 3) == [dt.datetime(2026, 1, 11, 9), dt.datetime(2026, 1, 18, 9),
                                             dt.datetime(2026, 1, 25, 9)]
    assert times("0 9 * * 5-7", MONDAY, 4) == [dt.datetime(2026, 1, 9, 9), dt.datetime(2026, 1, 10, 9),
                                               dt.datetime(2026, 1, 11, 9), dt.datetime(2026, 1, 16, 9)]


@pytest.mark.invariant("SCH-5")
def test_with_both_a_day_of_the_month_and_a_weekday_either_one_is_a_time():
    """Cron's rule: when the day of the month and the weekday are both given, a day that matches either runs. With
    the day of the month left as *, only the weekday counts (within the months given)."""
    new_year = dt.datetime(2025, 12, 31, 23)
    assert times("0 7 1 * MON", new_year, 7) == [
        dt.datetime(2026, 1, 1, 7), dt.datetime(2026, 1, 5, 7), dt.datetime(2026, 1, 12, 7), dt.datetime(2026, 1, 19, 7),
        dt.datetime(2026, 1, 26, 7), dt.datetime(2026, 2, 1, 7), dt.datetime(2026, 2, 2, 7)]
    assert times("0 7 * 2 MON", new_year, 5) == [
        dt.datetime(2026, 2, 2, 7), dt.datetime(2026, 2, 9, 7), dt.datetime(2026, 2, 16, 7), dt.datetime(2026, 2, 23, 7),
        dt.datetime(2027, 2, 1, 7)]


@pytest.mark.invariant("SCH-5")
def test_only_the_months_given_have_times_and_month_names_make_a_range():
    """ "0 7 1 JAN-MAR *" runs on the first of January, February and March, then not until next January."""
    assert times("0 7 1 JAN-MAR *", dt.datetime(2026, 1, 1), 4) == [
        dt.datetime(2026, 1, 1, 7), dt.datetime(2026, 2, 1, 7), dt.datetime(2026, 3, 1, 7), dt.datetime(2027, 1, 1, 7)]


@pytest.mark.invariant("SCH-5")
def test_a_step_from_a_single_start_counts_on_to_the_end_of_the_field():
    """ "5/15" in the minute field is 5, 20, 35 and 50 (from 5 to the end, every 15); "1/2" is every odd minute."""
    ten = dt.datetime(2026, 6, 15, 10, 0)
    assert times("5/15 * * * *", ten, 5) == [ten.replace(minute=m) for m in (5, 20, 35, 50)] + [
        dt.datetime(2026, 6, 15, 11, 5)]
    assert times("1/2 * * * *", ten, 3) == [ten.replace(minute=m) for m in (1, 3, 5)]


@pytest.mark.invariant("SCH-5")
def test_a_step_of_1_from_a_single_start_is_every_minute_from_it_to_the_end():
    """ "5/1" in the minute field is cron's every minute from :05 to :59, not :05 alone (as "5" is). A larger step
    from a single start is in the test above."""
    ten = dt.datetime(2026, 6, 15, 10, 0)
    assert times("5/1 * * * *", ten, 56) == [ten.replace(minute=m) for m in range(5, 60)] + [
        dt.datetime(2026, 6, 15, 11, 5)]


@pytest.mark.invariant("SCH-5")
@pytest.mark.parametrize("asked", [dt.datetime(2026, 6, 15, 10, 7, 0, 1), dt.datetime(2026, 6, 15, 10, 7, 30, 250000),
                                   dt.datetime(2026, 6, 15, 10, 7, 59, 999999)])
def test_after_a_moment_with_seconds_and_fractions_the_next_time_is_the_next_whole_minute(asked):
    """The service asks from the moment of its last check, which has seconds and fractions of a second: the next time
    of an every-minute cron line is still the very next whole minute, as a local time and as a moment (UTC), and none
    is dropped from the times due since then."""
    every_minute = Trigger({"schedule": {"cron": "* * * * *"}})
    assert every_minute.next_after(asked) == dt.datetime(2026, 6, 15, 10, 8)
    assert every_minute.next_after(asked.replace(tzinfo=UTC)) == dt.datetime(2026, 6, 15, 10, 8, tzinfo=UTC)
    assert every_minute.due(asked, dt.datetime(2026, 6, 15, 10, 10, 30)) == [
        dt.datetime(2026, 6, 15, 10, 8), dt.datetime(2026, 6, 15, 10, 9), dt.datetime(2026, 6, 15, 10, 10)]


# --- SCH-15: a time the clock skips --------------------------------------------------------------------------------

@pytest.fixture
def berlin():
    """The local time zone is Europe/Berlin: on 2026-03-29 the clock goes from 01:59:59 CET to 03:00:00 CEST at
    01:00:00 UTC, so 02:00-02:59 is skipped."""
    if not hasattr(time, "tzset"):
        pytest.skip("the local time zone cannot be changed inside a running process here (Windows)")
    before = os.environ.get("TZ")
    os.environ["TZ"] = "Europe/Berlin"
    time.tzset()
    try:
        jump = dt.datetime(2026, 3, 29, 1, 0, tzinfo=UTC).timestamp()
        if (dt.datetime.fromtimestamp(jump - 1), dt.datetime.fromtimestamp(jump)) != (
                dt.datetime(2026, 3, 29, 1, 59, 59), dt.datetime(2026, 3, 29, 3, 0)):
            pytest.skip("this system has no Europe/Berlin time zone")
        yield
    finally:
        if before is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = before
        time.tzset()


@pytest.mark.invariant("SCH-15")
@pytest.mark.invariant("SCH-5")
def test_each_time_the_clock_skips_runs_exactly_at_the_jump(berlin):
    """Each clock time 02:00-02:59 of the spring-forward night, as a daily cron line and as a one-off time, has the
    jump itself as its time: not a second before it (never before its time) and not a second after it (that second
    would come out of the run's 2 seconds). The next time after it is the next day's."""
    jump = dt.datetime(2026, 3, 29, 1, 0, tzinfo=UTC)
    an_hour_before = jump - dt.timedelta(hours=1)
    for minute in range(60):
        skipped = dt.datetime(2026, 3, 29, 2, minute)
        daily = Trigger({"schedule": {"cron": f"{minute} 2 * * *"}})
        once = Trigger({"schedule": {"at": f"{skipped:%Y-%m-%d %H:%M}"}})
        assert clock_moment(skipped) == jump, f"{skipped:%H:%M}"
        assert daily.next_after(an_hour_before) == jump, f"{skipped:%H:%M}"
        assert once.next_after(an_hour_before) == jump, f"{skipped:%H:%M}"
        assert daily.next_after(jump) == dt.datetime(2026, 3, 30, 0, minute, tzinfo=UTC), f"{skipped:%H:%M}"


# --- a one-off time with a UTC offset: the moment it names (SCH-5, SCH-14, C10) ------------------------------------

THE_MOMENT = dt.datetime(2026, 10, 8, 13, 30, tzinfo=UTC)


@pytest.mark.invariant("SCH-5")
@pytest.mark.invariant("SCH-14")
@pytest.mark.charter("C10")
@pytest.mark.parametrize("written", ["{at: '2026-10-08 15:30+02:00'}", "{at: 2026-10-08 15:30:00+02:00}",
                                     "{at: '2026-10-08T13:30:00Z'}"])
def test_a_one_off_time_with_a_utc_offset_runs_at_the_moment_it_names(written):
    """A one-off time written by hand with its UTC offset, quoted (text) or not (YAML reads it as a date and time),
    names one moment, with nothing to guess: it is that schedule's only time. The service asks in moments (UTC): the
    times due since its last check and up to 5 s ahead, its next time (when to check next), and whether a run started
    early still has its time; the editor asks in this PC's local time and is answered in local time. None of these
    may fail: an error there would stop the service or the editor."""
    trigger = Trigger({"schedule": yaml.safe_load(written)})
    before = THE_MOMENT - dt.timedelta(minutes=10)
    assert trigger.next_after(before) == THE_MOMENT
    assert trigger.next_after(THE_MOMENT - dt.timedelta(seconds=1)) == THE_MOMENT
    assert trigger.next_after(THE_MOMENT) is None
    assert trigger.due(before, THE_MOMENT - dt.timedelta(minutes=1)) == []
    assert trigger.due(THE_MOMENT - dt.timedelta(seconds=10), THE_MOMENT + dt.timedelta(seconds=2)) == [THE_MOMENT]
    on_this_clock = THE_MOMENT.astimezone().replace(tzinfo=None)
    assert trigger.next_after(on_this_clock - dt.timedelta(minutes=10)) == on_this_clock
    assert trigger.next_after(on_this_clock) is None


# --- SCH-7, SCH-10: every N, and a changed schedule ------------------------------------------------------------------

@pytest.mark.invariant("SCH-7")
def test_an_every_n_schedule_has_no_time_until_the_service_has_seen_it():
    """Every N counts from when the service first sees the schedule: until then it has no next time (the editor says
    so instead of showing one), and from then on its times are that moment plus whole multiples of N."""
    every = Trigger({"schedule": {"every": "5 minutes"}})
    asked = dt.datetime(2026, 6, 15, 10, 7, 30)
    assert every.next_after(asked) is None
    assert every.next_after(asked, dt.datetime(2026, 6, 15, 9, 58, 14)) == dt.datetime(2026, 6, 15, 10, 8, 14)


@pytest.mark.invariant("SCH-10")
@pytest.mark.parametrize("was, now", [({"at": "2026-10-08 15:30"}, {"at": "2026-10-08 15:10"}),
                                      ({"every": "5 minutes"}, {"every": "10 minutes"}),
                                      ({"cron": "0 7 * * *"}, {"cron": "0 8 * * *"}),
                                      ({"at": "2026-10-08 15:30+02:00"}, {"at": "2026-10-08 15:30+01:00"}),
                                      ({"at": "2026-10-08 15:30:30"}, {"at": "2026-10-08 15:30:07"})],
                         ids=["at", "every", "cron", "at with a UTC offset", "at changed only in its seconds"])
def test_a_changed_schedule_is_told_apart_from_what_it_was(was, now):
    """The scheduler service keeps a schedule's state (the newest time it handled, where Every N counts from) under
    the schedule's text, so a schedule changed to another time is a new one and starts afresh: its text is not the
    same. A one-off time written by hand with seconds runs at that second, so changing only its seconds is a change
    too."""
    assert Trigger({"schedule": was}).text != Trigger({"schedule": now}).text


# --- the words shown for a schedule (C12) ------------------------------------------------------------------------------

@pytest.mark.charter("C12")
@pytest.mark.parametrize("written, words", [("1 minute", "1 minute"), ("1 minutes", "1 minute"),
                                            ("5 minutes", "5 minutes"), ("5 minute", "5 minutes"),
                                            ("1 hour", "1 hour"), ("2 hours", "2 hours")])
def test_an_every_n_schedule_is_named_in_plain_words(written, words):
    """The editor and the service name an Every schedule by its text ("Every 5 minutes", "Every minute", "every 2
    hours"): one minute or hour in the singular, more in the plural, however the flow file writes it."""
    assert Every(written).text == words
    assert Trigger({"schedule": {"every": written}}).text == f"every {words}"


@pytest.mark.charter("C12")
def test_days_of_the_week_are_listed_monday_first():
    """The editor's week starts on Monday: Sunday, Monday and Saturday are saved as MON,SAT,SUN and shown as "Mon, Sat,
    Sun", however the line lists them; weekday 7 is Sunday there too."""
    assert plan_cron({"kind": "weekly", "days": [0, 1, 6], "hour": 7, "minute": 0}) == "0 7 * * MON,SAT,SUN"
    assert cron_plan("0 7 * * SUN,MON,SAT") == {"kind": "weekly", "days": [1, 6, 0], "hour": 7, "minute": 0}
    assert describe_cron("0 7 * * SUN,MON,SAT") == "Mon, Sat, Sun at 07:00"
    assert cron_plan("0 9 * * 7") == {"kind": "weekly", "days": [0], "hour": 9, "minute": 0}
    assert describe_cron("0 9 * * 7") == "Sun at 09:00"
    assert describe_cron("0 9 * * 5-7") == "Fri, Sat, Sun at 09:00"


@pytest.mark.charter("C12")
@pytest.mark.parametrize("line", ["*/15 * * * *", "0 7 1 * MON", "0 7 1-5 * *"])
def test_a_cron_line_the_plans_do_not_cover_is_shown_as_written(line):
    """Every 15 minutes is no hourly plan, a day of the month together with a weekday is no monthly plan (it also runs
    every Monday), and days 1 to 5 are no single day: each is shown as the Custom line it is."""
    assert cron_plan(line) is None
    assert describe_cron(line) == f"Custom: {line}"


# --- mistakes in a schedule: refused, in words that say what is wrong (C9, C10) --------------------------------------

@pytest.mark.charter("C9")
@pytest.mark.charter("C10")
@pytest.mark.parametrize("line, wrong, field", [
    ("x 7 * * *", "'x'", "minute"), ("5-x 7 * * *", "5-x", "minute"), ("1,,2 7 * * *", "''", "minute"),
    ("0 seven * * *", "seven", "hour"), ("0 7 1-y * *", "1-y", "day"), ("0 7 1 JUNE *", "june", "month"),
    ("0 7 * * MON-FRY", "mon-fry", "dow|weekday"), ("0 7 * * FRY-MON", "fry-mon", "dow|weekday"),
    ("61 * * * *", "61", "minute"), ("10-75 * * * *", "10-75", "minute"), ("30-10 * * * *", "30-10", "minute"),
    ("*/0 * * * *", "*/0", "minute"), ("0 24 * * *", "24", "hour"), ("0 7 0-5 * *", "0-5", "day"),
    ("0 7 1 0-6 *", "0-6", "month"), ("0 7 * * 8", "'8'", "dow|weekday"),
    ("*/x * * * *", "*/x", "minute"), ("0 5/x * * *", "5/x", "hour"),
])
def test_a_mistake_in_a_cron_field_is_refused_naming_the_field_and_what_is_wrong(line, wrong, field):
    """A field that cannot be read (a word, a misspelt name in a range, an empty item, a step that is not a number) or
    is out of its range (past its end, before its start, backwards, a step of 0) is refused, and the message quotes
    what is wrong and names the field."""
    message = refused({"schedule": {"cron": line}})
    assert wrong in message.lower() and re.search(field, message), message


@pytest.mark.charter("C12")
@pytest.mark.charter("C9")
@pytest.mark.parametrize("line, field", [
    ("x * * * *", "minute"), ("60 * * * *", "minute"), ("0 x * * *", "hour"), ("0 24 * * *", "hour"),
    ("0 0 x * *", "day of the month"), ("0 0 32 * *", "day of the month"), ("0 0 * x *", "month"),
    ("0 0 * 13 *", "month"), ("0 0 * * x", "weekday"), ("0 0 * * 8", "weekday"),
])
def test_a_mistake_in_a_cron_field_names_the_field_in_plain_words(line, field):
    """The message names the field as you would: minute, hour, day of the month, month, weekday (not "dow")."""
    message = refused({"schedule": {"cron": line}})
    assert field in message and not re.search(r"\bdow\b", message), message


@pytest.mark.charter("C9")
@pytest.mark.charter("C10")
@pytest.mark.parametrize("line", ["0 7 * *", "0 7 * * * *", ""])
def test_a_cron_line_without_five_fields_is_refused_saying_it_needs_five(line):
    message = refused({"schedule": {"cron": line}})
    assert "5 fields" in message and line in message, message


@pytest.mark.charter("C9")
@pytest.mark.charter("C10")
@pytest.mark.parametrize("written", ["five minutes", "0 minutes", "5 mins", "90 seconds"])
def test_an_every_that_is_not_n_minutes_or_hours_is_refused_with_examples(written):
    message = refused({"schedule": {"every": written}})
    assert written in message and "minutes" in message.replace(written, "") and "hours" in message, message


@pytest.mark.charter("C9")
@pytest.mark.charter("C10")
@pytest.mark.parametrize("data", [
    {"schedule": {}}, {"schedule": {"misfire": "skip"}},
    {"schedule": {"cron": "0 7 * * *", "every": "5 minutes"}}, {"schedule": {"every": "5 minutes", "at": "2026-10-08"}},
    {"schedule": "0 7 * * *"}, {"cron": "0 7 * * *"}, "0 7 * * *", ["0 7 * * *"], None,
])
def test_a_trigger_that_is_not_exactly_one_of_cron_every_or_at_is_refused_with_an_example_of_each(data):
    """No time, two kinds of time, or not a mapping at all (a flow-level `triggers:` entry written by hand): the
    message reads "a trigger looks like A, B or C", where A, B and C are triggers that work, with a cron line, an
    every and an at."""
    message = refused(data)
    found = re.fullmatch(r"a trigger looks like (\{.*?\}\}), (\{.*?\}\}) or (\{.*?\}\})", message)
    assert found, message
    examples = [yaml.safe_load(example) for example in found.groups()]
    assert [set(example["schedule"]) for example in examples] == [{"cron"}, {"every"}, {"at"}], message
    for example in examples:
        Trigger(example)  # (raises if the example does not work)


@pytest.mark.invariant("SCH-5")
@pytest.mark.charter("C9")
@pytest.mark.parametrize("written", ["{at: '2026-10-08 15:30:07.5'}", "{at: '2026-10-08 15:30:07.5+02:00'}",
                                     "{at: 2026-10-08 15:30:07.5}"])
def test_a_one_off_time_with_a_fraction_of_a_second_is_refused_saying_it_is_to_the_second(written):
    """A run begins on a whole second, so a one-off time between two seconds could only begin before it or after it:
    rather than round it without saying so, the schedule is refused when it is read, and the message names the time
    and says one-off times are to the second. Quoted (text, with or without its UTC offset) or not (YAML reads it as a
    date and time)."""
    message = refused({"schedule": yaml.safe_load(written)})
    assert "15:30:07" in message and re.search(r"\bsecond\b", message), message


@pytest.mark.charter("C9")
@pytest.mark.charter("C10")
def test_an_unknown_schedule_setting_is_refused_naming_it():
    message = refused({"schedule": {"cron": "0 7 * * *", "misfre": "skip", "overlap": "allow"}})
    assert "misfre" in message and "overlap" in message, message


@pytest.mark.charter("C9")
@pytest.mark.charter("C10")
@pytest.mark.parametrize("misfire", ["sometimes", "Skip", "", None])
def test_a_misfire_that_is_not_one_of_its_choices_is_refused_naming_the_choices(misfire):
    message = refused({"schedule": {"cron": "0 7 * * *", "misfire": misfire}})
    assert "misfire" in message and {"skip", "run_once", "catch_up"} <= set(re.findall(r"\w+", message)), message
