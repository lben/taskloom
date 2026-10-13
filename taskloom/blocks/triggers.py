"""Start blocks: where a run begins. Connect their `start` output to the first block's `after` input."""

from __future__ import annotations

from ..block import Block, fields, ports
from ..schedule import MISFIRE_POLICIES


class ManualStart(Block):
    type_id = "trigger.manual"
    title = "Manual Start"
    description = "Where the flow starts when you press Run. Connect it to the first block of the flow."
    category = "Start"
    is_trigger = True
    outputs = {"start": ports.Any()}

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
        "cron": fields.Text(required=False, help="Repeating times, e.g. 0 7 * * MON-FRI = 07:00 on weekdays; "
                                                 "*/5 * * * * = every 5 minutes (minute hour day month weekday)"),
        "at": fields.Text(required=False, help="Or one single time, e.g. 2026-10-08 15:30"),
        "misfire": fields.Choice(MISFIRE_POLICIES, default="run_once",
                                 help="Runs due while the computer was off: skip them, run the latest once, or run each"),
        "active": fields.Bool(default=True, help="Untick to pause this schedule"),
    }

    def run(self, ctx):
        return {"start": ctx.functions["now"]()}
