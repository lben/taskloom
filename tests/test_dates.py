"""Business-day arithmetic in parameter expressions."""

import datetime as dt

import pytest

from taskloom.calendars import load_calendar
from taskloom.expr import ExpressionError, evaluate, functions


def funcs(now: dt.datetime, calendars_dir, calendar=None):
    return functions(now, load_calendar(calendar, calendars_dir), lambda n: load_calendar(n, calendars_dir))


MONDAY = dt.datetime(2026, 10, 5, 7, 0)


def test_previous_business_day_on_monday_is_friday(tmp_path):
    assert evaluate("today() - bdays(1)", funcs(MONDAY, tmp_path)) == dt.date(2026, 10, 2)
    assert evaluate("int(format(today() - bdays(1), '%Y%m%d'))", funcs(MONDAY, tmp_path)) == 20261002


def test_custom_calendar_skips_holidays_and_weekends(tmp_path):
    (tmp_path / "work.yaml").write_text("add: [2026-10-02]\n")  # that Friday is a day off
    assert evaluate("today() - bdays(1)", funcs(MONDAY, tmp_path, "work")) == dt.date(2026, 10, 1)


def test_public_holiday_calendar_by_name(tmp_path):
    tuesday_after_mlk_day = dt.datetime(2026, 1, 20)
    f = funcs(tuesday_after_mlk_day, tmp_path)
    assert evaluate("today() - bdays(1)", f) == dt.date(2026, 1, 19)
    assert evaluate("today() - bdays(1, cal='US')", f) == dt.date(2026, 1, 16)


def test_expressions_cannot_reach_attributes(tmp_path):
    with pytest.raises(ExpressionError):
        evaluate("today().__class__", funcs(MONDAY, tmp_path))
