"""Business-day calendars: weekend days plus a holiday list."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import holidays
import yaml

WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


class Calendar:
    def __init__(self, name: str, weekend=(5, 6), base: str | None = None, add=(), remove=()):
        self.name = name
        self.weekend = frozenset(weekend)
        self.base = _holiday_source(base) if base else None
        self.add = {_as_date(d) for d in add}
        self.remove = {_as_date(d) for d in remove}

    def is_holiday(self, day: dt.date) -> bool:
        if day in self.remove:
            return False
        return day in self.add or (self.base is not None and day in self.base)

    def is_business_day(self, day: dt.date) -> bool:
        return day.weekday() not in self.weekend and not self.is_holiday(day)

    def shift(self, day, n: int):
        """Move `n` business days (negative = backwards). Keeps the time of a datetime."""
        step = 1 if n >= 0 else -1
        remaining = abs(n)
        while remaining:
            day += dt.timedelta(days=step)
            if self.is_business_day(_date_of(day)):
                remaining -= 1
        return day

    def warnings_for_year(self, year: int) -> list[str]:
        if self.base is None and not any(d.year == year for d in self.add):
            return [f"calendar '{self.name}' lists no holidays for {year}; only weekends will be skipped"]
        return []


def load_calendar(name: str | None, calendars_dir: Path) -> Calendar:
    """Resolve a calendar by name.

    No name: weekends only. A file `calendars/<name>.yaml` wins; otherwise the name is
    a holiday source from the `holidays` package, e.g. "US", "US-NY", "NYSE".
    """
    if not name:
        return Calendar("weekends")
    path = calendars_dir / f"{name}.yaml"
    if path.exists():
        data = yaml.safe_load(path.read_text()) or {}
        unknown = set(data) - {"weekend", "base", "add", "remove"}
        if unknown:
            raise ValueError(f"{path}: unknown keys {sorted(unknown)}")
        weekend = [WEEKDAYS[str(d).lower()[:3]] for d in data.get("weekend", ["sat", "sun"])]
        return Calendar(name, weekend, data.get("base"), data.get("add") or (), data.get("remove") or ())
    return Calendar(name, base=name)


def _holiday_source(spec: str):
    if spec.upper() in holidays.list_supported_financial():
        return holidays.financial_holidays(spec.upper())
    country, _, subdiv = spec.partition("-")
    try:
        return holidays.country_holidays(country.upper(), subdiv=subdiv.upper() or None)
    except NotImplementedError:
        raise ValueError(f"unknown holiday calendar '{spec}' (expected e.g. US, US-NY, GB, NYSE)") from None


def _as_date(value) -> dt.date:
    return value if isinstance(value, dt.date) else dt.date.fromisoformat(str(value))


def _date_of(value) -> dt.date:
    return value.date() if isinstance(value, dt.datetime) else value
