"""Schedules: cron expressions and one-off times, in the machine's local time."""

from __future__ import annotations

import datetime as dt

MISFIRE_POLICIES = ("skip", "run_once", "catch_up")
_NAMES = {
    "month": {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)},
    "dow": {d: i for i, d in enumerate(["sun", "mon", "tue", "wed", "thu", "fri", "sat"])},
}
_FIELDS = (("minute", 0, 59), ("hour", 0, 23), ("day", 1, 31), ("month", 1, 12), ("dow", 0, 7))


def _parse_field(text: str, name: str, lo: int, hi: int) -> set[int]:
    values = set()
    for part in text.lower().split(","):
        part, _, step = part.partition("/")
        step = int(step) if step else 1
        if part == "*":
            start, end = lo, hi
        else:
            a, _, b = part.partition("-")
            start = _NAMES.get(name, {}).get(a) if not a.isdigit() else int(a)
            end = (_NAMES.get(name, {}).get(b) if not b.isdigit() else int(b)) if b else (hi if step > 1 else start)
            if start is None or end is None:
                raise ValueError(f"cannot read '{part}' in the {name} field")
        if not lo <= start <= hi or not lo <= end <= hi or start > end or step < 1:
            raise ValueError(f"'{text}' is out of range for the {name} field ({lo}-{hi})")
        values.update(range(start, end + 1, step))
    if name == "dow" and 7 in values:  # both 0 and 7 mean Sunday
        values = (values - {7}) | {0}
    return values


class Cron:
    """Five fields: minute hour day-of-month month day-of-week, e.g. "0 7 * * MON-FRI"."""

    def __init__(self, expr: str):
        parts = expr.split()
        if len(parts) != 5:
            raise ValueError(f"cron needs 5 fields (minute hour day month weekday), got {expr!r}")
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


class At:
    """A single run at a given time, e.g. "2026-10-08 15:30"."""

    def __init__(self, when):
        self.when = when if isinstance(when, dt.datetime) else dt.datetime.fromisoformat(str(when))

    def next_after(self, t: dt.datetime) -> dt.datetime | None:
        return self.when if self.when > t else None


class Trigger:
    def __init__(self, data: dict):
        spec = data.get("schedule") if isinstance(data, dict) else None
        if not isinstance(spec, dict) or ("cron" in spec) == ("at" in spec):
            raise ValueError("a trigger looks like {schedule: {cron: '0 7 * * MON-FRI'}} or {schedule: {at: '2026-10-08 15:30'}}")
        unknown = set(spec) - {"cron", "at", "misfire"}
        if unknown:
            raise ValueError(f"unknown schedule setting(s): {sorted(unknown)}")
        self.timing = Cron(str(spec["cron"])) if "cron" in spec else At(spec["at"])
        self.text = f"cron {spec['cron']}" if "cron" in spec else f"at {self.timing.when:%Y-%m-%d %H:%M}"
        self.misfire = spec.get("misfire", "run_once")
        if self.misfire not in MISFIRE_POLICIES:
            raise ValueError(f"misfire must be one of {', '.join(MISFIRE_POLICIES)}")

    def next_after(self, t: dt.datetime) -> dt.datetime | None:
        return self.timing.next_after(t)

    def due(self, after: dt.datetime, until: dt.datetime, limit: int = 1000) -> list[dt.datetime]:
        """Fire times in (after, until], oldest first."""
        times, t = [], after
        while len(times) < limit and (t := self.next_after(t)) is not None and t <= until:
            times.append(t)
        return times
