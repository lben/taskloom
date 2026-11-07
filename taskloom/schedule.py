"""Schedules: cron expressions, every N minutes or hours, and one-off times, in the machine's local time.

A clock time (cron, one-off) is when this PC's clock shows it, and a one-off time written with its UTC offset the
moment it names; every N counts in real elapsed time. Asked with a moment (a datetime with its UTC offset), a schedule
answers in moments, so a clock change neither moves an every-N rhythm nor loses or repeats a clock time; asked with a
local time (no offset), it answers in local times, as on screen."""

from __future__ import annotations

import datetime as dt
import re

MISFIRE_POLICIES = ("skip", "run_once", "catch_up")
_NAMES = {
    "month": {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)},
    "weekday": {d: i for i, d in enumerate(["sun", "mon", "tue", "wed", "thu", "fri", "sat"])},
}
_FIELDS = (("minute", 0, 59), ("hour", 0, 23), ("day of the month", 1, 31), ("month", 1, 12), ("weekday", 0, 7))


def _parse_field(text: str, name: str, lo: int, hi: int) -> set[int]:
    values = set()
    for item in text.split(","):
        part, slash, step = item.lower().partition("/")
        if slash and not step.isdigit():
            raise ValueError(f"cannot read '{item}' in the {name} field: the step after / is not a number")
        step = int(step) if slash else 1
        if part == "*":
            start, end = lo, hi
        else:
            a, _, b = part.partition("-")
            start = _NAMES.get(name, {}).get(a) if not a.isdigit() else int(a)
            # a step from a single start counts on to the end of the field, as in cron: 5/15 is 5, 20, 35, 50
            end = (_NAMES.get(name, {}).get(b) if not b.isdigit() else int(b)) if b else (hi if slash else start)
            if start is None or end is None:
                raise ValueError(f"cannot read '{item}' in the {name} field")
        if not lo <= start <= hi or not lo <= end <= hi or start > end or step < 1:
            raise ValueError(f"'{text}' is out of range for the {name} field ({lo}-{hi})")
        values.update(range(start, end + 1, step))
    if name == "weekday" and 7 in values:  # both 0 and 7 mean Sunday
        values = (values - {7}) | {0}
    return values


class Cron:
    """Five fields: minute hour day-of-month month day-of-week, e.g. "0 7 * * MON-FRI"."""

    def __init__(self, expr: str):
        parts = expr.split()
        if len(parts) != 5:
            raise ValueError(f"cron needs 5 fields (minute, hour, day of the month, month, weekday), got {expr!r}")
        self.expr = expr
        self.minute, self.hour, self.day, self.month, self.dow = (
            _parse_field(p, name, lo, hi) for p, (name, lo, hi) in zip(parts, _FIELDS))
        self._any_day, self._any_dow = parts[2] == "*", parts[4] == "*"

    def _day_matches(self, d: dt.date) -> bool:
        if d.month not in self.month:
            return False
        dom, dow = d.day in self.day, (d.weekday() + 1) % 7 in self.dow
        if self._any_day or self._any_dow:  # classic cron: if both are restricted, either may match
            return dom and dow
        return dom or dow

    def next_after(self, t: dt.datetime) -> dt.datetime | None:
        t = t.replace(second=0, microsecond=0) + dt.timedelta(minutes=1)
        day = t.date()
        for _ in range(366 * 8):
            if self._day_matches(day):
                for hour in sorted(self.hour):
                    for minute in sorted(self.minute):
                        candidate = dt.datetime.combine(day, dt.time(hour, minute))
                        if candidate >= t:
                            return candidate
            day += dt.timedelta(days=1)
        return None


# --- plans: the common schedules, as the editor builds them, and their cron lines --------------------

DAY_NAMES = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]  # cron's weekdays: 0 (or 7) is Sunday
DAY_WORDS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def _monday_first(days) -> list[int]:
    return sorted(days, key=lambda d: (d - 1) % 7)


def plan_cron(plan: dict) -> str:
    """The cron line of a plan, e.g. {"kind": "weekdays", "hour": 7, "minute": 0} -> "0 7 * * MON-FRI"."""
    kind = plan["kind"]
    if kind == "hourly":
        return f"{plan['minute']} * * * *"
    at = f"{plan['minute']} {plan['hour']}"
    if kind == "daily":
        return f"{at} * * *"
    if kind == "weekdays":
        return f"{at} * * MON-FRI"
    if kind == "weekly":
        return f"{at} * * " + ",".join(DAY_NAMES[d] for d in _monday_first(plan["days"]))
    if kind == "monthly":
        return f"{at} {plan['day']} * *"
    raise ValueError(f"unknown kind of schedule: {kind}")


def cron_plan(text: str) -> dict | None:
    """The plan a cron line is, or None when it is something else (then it stays as you wrote it)."""
    parts = str(text).split()
    try:
        cron = Cron(str(text))
    except ValueError:
        return None
    minute, hour, day, month, dow = parts
    if month != "*":
        return None
    if hour == "*" and day == "*" and dow == "*":
        return {"kind": "hourly", "minute": int(minute)} if minute.isdigit() else None
    if not (minute.isdigit() and hour.isdigit()):
        return None
    at = {"hour": int(hour), "minute": int(minute)}
    if day == "*" and dow == "*":
        return {"kind": "daily", **at}
    if day == "*":
        if cron.dow == {1, 2, 3, 4, 5}:
            return {"kind": "weekdays", **at}
        return {"kind": "weekly", "days": _monday_first(cron.dow), **at}
    if dow == "*" and day.isdigit():
        return {"kind": "monthly", "day": int(day), **at}
    return None


def describe_cron(text: str) -> str:
    """A cron line in words, e.g. "Monday to Friday at 07:00"."""
    plan = cron_plan(text)
    if plan is None:
        return f"Custom: {text}"
    kind = plan["kind"]
    if kind == "hourly":
        return f"Every hour at :{plan['minute']:02d}"
    at = f"at {plan['hour']:02d}:{plan['minute']:02d}"
    return {"daily": f"Every day {at}", "weekdays": f"Monday to Friday {at}",
            "weekly": ", ".join(DAY_WORDS[d] for d in plan.get("days", [])) + f" {at}",
            "monthly": f"Day {plan.get('day')} of every month {at}"}[kind]


class At:
    """A single run at a given time on this PC's clock, e.g. "2026-10-08 15:30", or at the moment a time written with
    its UTC offset names, e.g. "2026-10-08 15:30+02:00"."""

    def __init__(self, when):
        self.when = when if isinstance(when, dt.datetime) else dt.datetime.fromisoformat(str(when))
        if self.when.microsecond:  # (a run begins on a whole second: it would begin before it or after it)
            raise ValueError(f"a one-off time is to the second, got '{when}': write it without the fraction of a "
                             "second")

    @property
    def wall(self) -> dt.datetime:
        """Its time on this PC's clock (of a time with its UTC offset: the moment it names, in local time)."""
        return self.when.astimezone().replace(tzinfo=None) if self.when.tzinfo else self.when

    def next_after(self, t: dt.datetime) -> dt.datetime | None:
        """Its time on this PC's clock if after the local time t."""
        wall = self.wall
        return wall if wall > t else None


class Every:
    """Every N minutes or hours, e.g. "5 minutes", counted from a start: not on the clock, but from when the
    scheduler service first saw the schedule as it is."""

    def __init__(self, text):
        found = re.fullmatch(r"([1-9][0-9]*) *(minute|hour)s?", str(text).strip())
        if not found:
            raise ValueError(f"every looks like 5 minutes or 2 hours, got {text!r}")
        n, unit = int(found[1]), found[2]
        self.interval = dt.timedelta(**{f"{unit}s": n})
        self.text = f"{n} {unit}" + ("s" if n > 1 else "")

    def next_after(self, t: dt.datetime, start: dt.datetime) -> dt.datetime:
        """The first of ..., start - N, start, start + N, ... after t."""
        return start + ((t - start) // self.interval + 1) * self.interval


def clock_moment(wall: dt.datetime) -> dt.datetime:
    """The moment (in UTC) this PC's clock first shows the local time `wall`: a time it shows twice (the night the
    clock goes back) is its first; a time it skips (the night it goes forward) is the moment of the jump."""
    first, second = (int(wall.replace(fold=fold).timestamp()) for fold in (0, 1))
    if first > second:  # skipped: the jump is between the moments its offsets before and after it give
        while first - second > 1:  # the first second whose clock time is `wall` or later
            middle = (first + second) // 2
            if dt.datetime.fromtimestamp(middle) >= wall:
                first = middle
            else:
                second = middle
    return dt.datetime.fromtimestamp(first, dt.timezone.utc)


class Trigger:
    def __init__(self, data: dict, block: str | None = None):
        self.block = block  # the Schedule block it comes from, or None for a flow-level trigger
        spec = data.get("schedule") if isinstance(data, dict) else None
        if not isinstance(spec, dict) or len({"cron", "every", "at"} & set(spec)) != 1:
            raise ValueError("a trigger looks like {schedule: {cron: '0 7 * * MON-FRI'}}, {schedule: {every: 5 minutes}} "
                             "or {schedule: {at: '2026-10-08 15:30'}}")
        unknown = set(spec) - {"cron", "every", "at", "misfire"}
        if unknown:
            raise ValueError(f"unknown schedule setting(s): {sorted(unknown)}")
        if "cron" in spec:
            self.timing = Cron(str(spec["cron"]))
            self.text = f"cron {spec['cron']}"
        elif "every" in spec:
            self.timing = Every(spec["every"])
            self.text = f"every {self.timing.text}"
        else:
            self.timing = At(spec["at"])
            # (to the second, with its UTC offset if written with one: a time changed in either is a new schedule)
            self.text = f"at {self.timing.when.isoformat(' ', 'seconds')}"
        self.every = self.timing.interval if isinstance(self.timing, Every) else None
        self.misfire = spec.get("misfire", "run_once")
        if self.misfire not in MISFIRE_POLICIES:
            raise ValueError(f"misfire must be one of {', '.join(MISFIRE_POLICIES)}")

    def next_after(self, t: dt.datetime, start: dt.datetime | None = None) -> dt.datetime | None:
        """Its first time after t. An every-N schedule's times count from `start`, one of them (where the
        scheduler service counts it from); without one it has none yet. A moment t (with its UTC offset) gives a
        moment: a clock time's is when the clock first shows it (clock_moment)."""
        if self.every:
            return None if start is None else self.timing.next_after(t, start)
        if t.tzinfo is None:
            return self.timing.next_after(t)
        if isinstance(self.timing, At) and self.timing.when.tzinfo:  # written with its UTC offset: already a moment
            moment = self.timing.when.astimezone(dt.timezone.utc)
            return moment if moment > t else None
        wall = t.astimezone().replace(tzinfo=None)
        while (wall := self.timing.next_after(wall)) is not None:  # (after the clock went back: past the repeat)
            if (when := clock_moment(wall)) > t:
                return when
        return None

    def due(self, after: dt.datetime, until: dt.datetime) -> list[dt.datetime]:
        """Fire times in (after, until], oldest first; an every-N schedule's count on from `after`, one of its times."""
        times, t = [], after
        while (t := self.next_after(t, after)) is not None and t <= until:
            times.append(t)
        return times
