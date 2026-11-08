# Formal model of the scheduler service

`SchedulerService.tla` is a TLA+ model of the scheduler-service protocol, the **target design** agreed on
2026-11-07. It does not copy every detail of `taskloom/scheduler.py`. TLC checks every interleaving of the
service, its run processes and the user within small bounds, against the promises in
`docs/promises/scheduler.yaml`.

## What is modelled

- **Service lives.** A life starts only while no service holds the lock and the user has not turned the service
  off. Taking the lock and naming itself in the service-on record are one step. A life can crash; its run
  processes keep going.
- **The check**, as three kinds of atomic step:
  - `Poll` reads the run history. It records runs whose process died as crashed and adopts the scheduled runs
    going that it did not start. It applies a turn-off it sees for the first time, and ends the service once
    nothing it lets finish is left.
  - `PlanFlow(f)` is one transaction per schedule. It judges the times that came, and the times whose run did
    not begin, by Misfire, then requests them by Overlap and the limit. Each time is started, skipped and told,
    or saved in `waiting_runs` as `queue` (waiting for its flow) or `slot` (waiting for a free slot). A time on
    time starts now only if no saved run that came before it could start now (`Ahead`: its flow is free, or it waits
    for a slot and its Overlap is allow).
  - `StartNext(d)` starts one saved run when a slot is free and, unless Overlap is allow, no run of its flow
    is going. While turning off, it starts only Let-it-finish flows.
- **Run processes** load, then `Claim`: one transaction. The claim checks the lock, the service-on record, the
  schedule version and the turn-off. It then takes the row and writes the run's history row. If refused, the
  process ends and its row stays. Processes can be killed while loading or while going. One killed while loading
  counts as going until its service's next `Poll`, which drops its row and tells you it failed (fate `failed`); if
  that service is gone, its row stays.
- **The user** can change a schedule or turn it off (the editor drops its state and saved runs), start one run
  by hand, turn the service off with Proceed and per-flow choices, and turn it on again.

### What is abstracted

- **Time.** Time is a few ticks. A time handled at its own tick is on time; a later one is late. The 5-second
  and 2-minute windows, the ask-over-100 dialog, `run_once` (supported but not in the cfg), clock changes,
  e-mail and notices are not modelled.
- **Run order.** Any saved run may start next. This over-approximates "in the order they came". Likewise, a time
  due again that is on time may or may not have come before the saved runs that could start now (`first`).
- **The dialog.** It is one step with Proceed and lists the flows with runs going at that moment.
- **Atomicity.** A check's reads of the history and its decisions are atomic within each step above. This is
  sound for these promises: between a read and a decision, only the service's own claims (already counted),
  runs ending (making the read conservative) and runs by hand can change things, and runs by hand are free to
  begin at any step anyway.

## Constants (`SchedulerService.cfg`)

| Constant | Value | Meaning |
|---|---|---|
| `Flows` | `{f1, f2}` | two flows; the model is symmetric in them (`SYMMETRY`) |
| `MaxTime` | 2 | each schedule is due at times 1 and 2 |
| `MaxLives` | 2 | two service lives |
| `Limit` | 1 | Max concurrent runs |
| `MaxManual` | 1 | one run by hand |
| `MaxChanges` | 1 | one schedule change or schedule turn-off |
| `MaxTurnOffs` | 1 | one Proceed (and then one turn-on) |
| `MaxKills` | 1 | one run process killed |
| `Overlaps` | `{"skip", "queue"}` | each flow's Overlap is chosen at the start |
| `Misfires` | `{"skip", "catch_up"}` | each flow's Misfire is chosen at the start |

The four design switches (`ClaimChecksOverlap`, `TermOnTurnOff`, `ProceedDropsStop`, `EditorStartsSchedule`)
are `TRUE`: each is the smallest change that makes one promise hold that the first target design broke, and all
four are part of the design being implemented (see Decided). Set one to `FALSE` to see its counterexample.

With these bounds TLC finds each broken promise within seconds to a few minutes; the sweeps without a broken
promise are much larger (see Results). Overlap `allow` is left out to halve the state space. It is exempt from
SCH-11, and with `Limit = 1` it acts differently only alongside a run by hand. An `allow`-only sweep stopped at
0.7M states with no promise broken.

## How to run

```sh
cd specs/formal
java -XX:+UseParallelGC -cp ~/.local/share/tlaplus/1.7.4/tla2tools.jar tlc2.TLC -workers 4 \
     -metadir /tmp/tlc-scheduler SchedulerService
```

TLC stops at the first broken promise and prints the steps that lead to it. To check one promise, list only
that one under `INVARIANTS`/`PROPERTIES`. To check a design change, set its switch to `TRUE`.

## What each check maps to

| Check | Kind | Promise |
|---|---|---|
| `OneTerm` | invariant | SCH-1: one service at a time; the service-on record names the one holding the lock |
| `SCH6` (`AtMostOnce`) | step | SCH-6: each due time begins at most once |
| `SCH11` (`OverlapAtBegin`) | step | SCH-11, strict: when a scheduled run begins, no other run of its flow (scheduled or by hand) is going, unless Overlap is allow |
| `SCH11Relaxed` | step | SCH-11, relaxed: the same, but a run by hand may go if it started while this run's process was loading |
| `LimitHeld` | invariant | SCH-12: scheduled runs going never exceed Max concurrent runs |
| `SCH8` (`NoBeginAfterOffOrChange`) | step | SCH-8: a run never begins if, while it loaded, the service was turned off (except a Let-it-finish waiting run) or its schedule changed or was turned off |
| `SCH4` (`OffBegins`, `StopRowsStay`) | step | SCH-4: while off, only Let-it-finish waiting runs begin; the runs saved for a Stop-now flow at Proceed never begin later in that service's term |
| `NoLoss`, `NoOrphanRows` | invariant | SCH-16 (and SCH-9, SCH-11): every time that has come is begun, saved, told skipped, missed or failed, dropped by a user choice, or still to be handled; a saved run is always for a schedule that is on |
| `ChangedTimesHandled` | invariant | SCH-9 with SCH-10: a changed schedule starts fresh from the change; its later times follow Misfire |
| `AdoptionCounted`, `Adoption` | invariant, step | Adoption: once the service on has checked, every scheduled run going is its own or adopted; when a run begins, every other scheduled run going was counted by the service that started it |
| `TypeOK` | invariant | well-formed states |

## Results (2026-11-07, TLC 2.19)

With the four switches `FALSE` (the first target design), TLC stops at the first broken promise,
`ChangedTimesHandled`, at depth 6. Checking each promise on its own, the design as decided breaks four:

| Broken | Shortest counterexample | Design change that makes it hold (switch) |
|---|---|---|
| `ChangedTimesHandled` (SCH-9/10) | The schedule is changed; a time of the new schedule comes before the service next looks at it (service off, PC asleep, or within the second before its check); the service then starts the schedule "afresh from now" and that time is neither run nor told missed. | The editor writes the changed schedule's state (`last` = the change time) when it saves it (`EditorStartsSchedule`). |
| `SCH11` strict (SCH-11) | A time comes and its run process starts loading; the user starts a run of the same flow by hand; the scheduled run claims and begins next to it (Overlap skip). | The claim also refuses while another run of its flow is going, unless Overlap is allow; the refused time is then judged by Overlap again (`ClaimChecksOverlap`). |
| `SCH8` (SCH-8) | A run process is loading; the user turns the service off (Proceed), then on again before it claims; it begins. | Proceed and turn-on each start a new epoch; a run begins only in the epoch it was started in, except a Let-it-finish waiting run while off (`TermOnTurnOff`). |
| `SCH4` (SCH-4) | A flow's catch-up run is going and its next run is saved; Proceed with Stop now; the service has not looked yet, so it starts the saved run; the user turns the service on; the saved run begins. | Proceed drops the Stop-now flows' saved runs itself, in its own transaction (`ProceedDropsStop`). |

Each switch is needed: with all four on but one, TLC finds that one's promise broken again.

With all four switches on and `MaxKills = 0` (all else as in the cfg), the sweep is complete, and no promise
is broken: 137,433,947 states generated, 29,338,419 distinct, depth 33, 29.5 minutes with 5 workers.

These sweeps found no broken promise but did not finish. Each was stopped partway through a breadth-first
level, so every behaviour up to the level before it was fully checked:

| Design | Promises checked | Bounds changed from the cfg | Distinct states | Behaviours checked up to |
|---|---|---|---|---|
| As decided | all but the four above | none | 16.5M | 16 steps |
| As decided | all but the four above | `MaxKills = 0` | 20.7M | 20 steps (of about 33) |
| As decided | all but the four above | Overlap queue, Misfire catch_up only | 3.2M | 20 steps |
| All four switches on | all | none | 12.9M | 16 steps |
| All four switches on | all | Overlap queue, Misfire catch_up only | 2.3M | 19 steps |

With the cfg as committed (all four switches on, `MaxKills = 1`, and the two rulings in Decided), the sweep is
complete and no promise is broken: 562,494,724 states generated, 113,767,922 distinct, depth 33, 1 hour 24 minutes
with 4 workers on a 10-core laptop shared with other work (2026-11-08). Before the rulings it was 457,327,633
generated, 89,912,884 distinct, depth 34, 54 minutes. Run it unattended.

One reading of the target design is load-bearing: "a waiting run starts when it may" is taken to also check
Overlap, counting runs by hand. If a run waiting for a free slot starts on the limit alone (as
`_next_in_line` does today), it begins next to a run by hand that started while it waited. That breaks even
`SCH11Relaxed`, at depth 9.

## Decided (2026-11-08)

All four changes are taken; none needs a promise reworded:

- `EditorStartsSchedule`: for a schedule with fixed times, a time after the save that the service did not see
  come is a time missed while the service was off (SCH-9), so it follows Misfire. An Every N schedule still
  counts from when the service first sees it (SCH-7), so it has no times before that.
- `ClaimChecksOverlap`: SCH-11 is read strictly, as its words say: a second run goes at once only if Overlap is
  Allow, also when the run by hand started while the scheduled run's program was loading.
- `TermOnTurnOff` and `ProceedDropsStop`: SCH-8 and SCH-4 as worded. Stop now also holds when no service is
  running at Proceed.

Two rulings on where trace validation found the code and the model apart; in both the code is right, and the model
now does the same:

- **A run's program that ends before its claim** (it failed to load, or died). Its service drops its saved row at
  its next check and tells you it failed (`_ended`); that time does not run again. Its fate is `failed`, which
  `NoLoss` counts as handled.
- **Start now or wait for a slot.** A run for its time waits only behind saved runs that came before it and could
  start now (`_request`'s `ahead`), not behind every run saved for a slot: nothing waits behind a run that cannot
  start anyway.

## Trace validation

`packaging/tla_trace.py` checks that what the real code does is a behaviour of this model. It drives the real service
(`Scheduler.tick`, `claim_time` inside `History.start_run`, `request_turn_off`, `set_paused`, `forget_schedules`,
`acquire_lock`) through 200 generated scenarios (fixed seeds) within the cfg's bounds. Run processes are stand-ins at
`subprocess.Popen`, as in the scheduler tests. Each step is written, with the state the code shows after it, into a
generated module `SchedulerTrace`. `TraceSchedulerService.tla` (with `TraceSchedulerService.cfg`) makes TLC look for a
behaviour of `Next` that takes the same actions through the same states. The match is reported as `Invariant
TraceIncomplete is violated`, because that invariant fails exactly when the whole trace is matched. Then a planted
divergence must fail: a refused claim recorded as begun.

**What each step of the code is.** The clock moving on to the next time is `Tick`. A service taking the lock and
making its `Scheduler` is `ServiceStart`; its process dying is `ServiceCrash`. A check is several steps:
- `_poll` is `Poll`. A service that turns off ends there and lets go of the lock.
- each schedule's `_check` that changes anything is `PlanFlow(f)`, with the runs it starts for their time;
- each saved run `_next_in_line` starts is `StartNext(d)`.

A run's claim is `Claim(p)`, begun or refused, as the code said. A run's program dying before its claim is
`KillLoading(p)`; dying after it is `KillRun(d)`. A run ending is `RunFinish(d)`, also one stopped by Proceed's Stop now.
Then there are runs by hand (`ManualBegin`, `ManualEnd`), the editor saving a changed or turned-off schedule
(`ChangeSchedule`), `Proceed` with the choices for the flows `runs_going` lists, and turning the service on (`TurnOn`).

**The mapping** (code → model):

| Model | Code |
|---|---|
| `now` | Mapped by order: the clock at tick 0 is 06:00, when the editor saved both flows. At tick k it is within 2 minutes after the schedule's k-th time (06:05, 06:10). |
| `ver`, `schedOn` | The flow file's Schedule block: one of two cron texts with the same two times; Active ticked and listed in `scheduled.yaml` |
| `last` | The `schedule_state` row: its text gives `v`, and its time gives `t`, the newest tick at or before it. No row gives `v` = -1. |
| `row` | `waiting_runs`: `now` is `time`, `flow` is `queue`, `slot` is `slot`. A due time `<<f, v, t>>` is the flow, its schedule's text and its time. |
| `run`, `starter` | Scheduled rows in `runs`. `going` is running with the process alive; `died` is running with the process gone; anything else is `over`. The starter is the life whose process claimed it. |
| `manualGoing` | Unscheduled rows in `runs` that are running, with the process alive |
| `lock`, `lives`, `svcOn`, `epoch` | The life holding `scheduler.lock`; the services made; `service_on`; `service_epoch` |
| `paused`, `choice` | `scheduler.paused` and the choices it holds |
| `loading` | Processes started with `--if-scheduled` that have neither claimed nor ended: their due time, the life that started them, and the epoch they carry. The model keeps one that ended before its claim (`dead`) until its service polls; it is not shown. |
| `adopted`, `turning`, `checked` | The memory of the service on: its runs from before (`as_of` None), `turning_off`, and whether it has polled |

The ghost variables (`fate`, `stopRows`, `changedAt`) and the counters are left to the model.

**Not covered.**
- The 2-minute load window. Once a run for its time has loaded for more than 2 minutes, the code refuses its claim, and
  the service asks it to stop (`_loading_too_long`). The model has no such window, and there that claim would begin. So
  the clock never moves on while a run for its time loads under the service on.
- Outside the cfg's bounds or not modelled: Overlap allow, Misfire run_once, the more-than-100 question (`missed` and
  `answer` rows), Every N schedules, Max concurrent runs other than 1, more than two flows or times, clock changes,
  unreadable files.
- What you are told (skipped, missed, failed) and e-mails are not compared.
- A stand-in asked to stop ends at once. The model's `Poll` applies Stop now in one step, while a real run takes a moment.
- A service is started only while the service is on, as the model assumes.

**Results (2026-11-08, the code as of 05b7249, the model with the two rulings in Decided).** 198 of the 200 traces
match, and they take every action above; the planted divergence is caught. Before the rulings, 31 more stopped where
a run's program died before its claim (21) or at start now or wait for a slot (10). The other 2 stop at a bug in the
code, being fixed and not modelled: **the service's memory of saved runs goes stale.** Saving a changed schedule drops
its saved runs, but until the end of its next check the service still counts them. A new time of that flow is then
skipped as "still waiting its turn", or queued (trace 198). Proceed's Stop now also drops saved runs. If you turn the
service on again before it sees the turn-off, it starts a process for one of those runs, which its claim then refuses
(trace 181).

**How to run.** Run `python packaging/tla_trace.py --out /tmp/tla-trace`. It needs `java`, and tla2tools.jar 1.7.4 at
`~/.local/share/tlaplus/1.7.4/tla2tools.jar` or `$TLA2TOOLS_JAR`. It takes about 1.5 minutes on 4 cores. Each trace
and its TLC log go to `trace-NNN/`. The last line sums up. The command exits 0 only if every trace matches and the
planted divergence is caught. `tests/test_tla_trace.py` keeps the planted case as a test.
