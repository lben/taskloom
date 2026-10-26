"""Start blocks: where a run begins. Connect their `start` output to the first block's `after` input."""

from __future__ import annotations

from ..block import Block, fields, ports
from ..config import kept_runs
from ..schedule import MISFIRE_POLICIES


class KeptRuns(fields.Field):
    """How a start block's runs are kept: all, last N, or N days, months or years (none: as in Settings)."""

    def coerce(self, value):
        kept_runs(value)  # ValueError if it cannot be read
        return str(value).strip()


# Every start block's: how the runs it starts are kept (see Keep runs in the README).
KEEP_RUNS = {
    "keep": KeptRuns(required=False, label="Keep runs",
                     help="As in Settings (keep_runs_for), all of them, the last N, or for a time"),
    "keep_failed": fields.Bool(default=False, label="Always keep failed runs",
                               help="A failed run is kept, whatever Keep runs says"),
}


class ManualStart(Block):
    type_id = "trigger.manual"
    title = "Manual Start"
    description = "Where the flow starts when you press Run. Connect it to the first block of the flow."
    category = "Start"
    is_trigger = True
    outputs = {"start": ports.Any()}
    config = KEEP_RUNS

    def run(self, ctx):
        return {"start": ctx.functions["now"]()}


class Schedule(Block):
    type_id = "trigger.schedule"
    title = "Schedule"
    description = ("Starts the flow by itself at the times you set, for example every weekday at 7:00, "
                   "while the scheduler is on. Connect it to the first block of the flow.")
    category = "Start"
    is_trigger = True
    outputs = {"start": ports.Any()}
    config = {
        "cron": fields.Text(required=False, help="Repeating times on the clock, e.g. 0 7 * * MON-FRI = 07:00 on "
                                                 "weekdays; */5 * * * * = at :00, :05, :10, ... (minute hour day month "
                                                 "weekday)"),
        "every": fields.Text(required=False, help="Or every N minutes or hours, e.g. 5 minutes, counted from when the "
                                                  "scheduler service picks the schedule up (not on the clock)"),
        "at": fields.Text(required=False, help="Or one single time, e.g. 2026-10-08 15:30"),
        "misfire": fields.Choice(MISFIRE_POLICIES, default="run_once",
                                 help="Runs due while the computer was off: skip them, run the latest once, or run each"),
        "active": fields.Bool(default=True, help="Untick it and this schedule does not start the flow "
                                                   "(the flow's other schedules are not affected)"),
        **KEEP_RUNS,
    }

    def run(self, ctx):
        return {"start": ctx.functions["now"]()}
