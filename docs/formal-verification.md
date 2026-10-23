# Formal verification of Taskloom's scheduler, retries, and keeper

TLC found six broken rules in models of the code at `d6d6a16`. Each one was replayed against Taskloom and confirmed. The fixes are in this PR, each with a test. A seventh rule, "retries stop at the configured maximum," holds. A mutant that allows two extra attempts makes that rule fail, so the passing run is not a check that cannot fail.

The models of the fixed code hold those rules. `docs/statecharts/*/summary.json` is the source of the counts below.

## Method

pstack 0.15.30 was not installed as a command in this environment. The check follows its skills at [cursor/plugins @ 4ec7ffa](https://github.com/cursor/plugins/tree/4ec7ffa/pstack):

- [create-formal-verification-skill](https://github.com/cursor/plugins/blob/4ec7ffa/pstack/skills/create-formal-verification-skill/SKILL.md) (v0.15.27 and later)
- [statechart](https://github.com/cursor/plugins/blob/4ec7ffa/pstack/skills/statechart/SKILL.md)

TLA+ is the model, because the bugs are about the order of a check, a running job, a stop, and a retry. TLC2 2.19 (8 August 2024, rev `5a47802`) ran under Java 21. The statechart script drew the charts with `--fixed` and `--png`. `summary.json` lists no uncited conditions.

Regenerate everything:

```sh
export TLA2TOOLS=/path/to/tla2tools.jar
export STATECHART=/path/to/pstack/skills/statechart/scripts/statechart.mjs
export JAVA=java
specs/formal/check.sh
```

`check.sh` exits 0 when the pre-fix models break exactly the expected rules, the fixed models hold, and `BlockRunMutant.cfg` breaks `AttemptsEnd`. The next agent should use `.cursor/skills/verify-taskloom-concurrency/SKILL.md`.

The project skill was run once against a scratch edit of `taskloom/engine.py` that discarded a value returned during the grace period. `test_a_block_that_finishes_during_the_timeout_grace_is_kept` failed (three attempts, `TimeoutError`, exit 1). The edit was reverted and the test passed again. A second scratch, in `RetryGraceFixed.tla` only, made TLC report `SuccessSticks` violated; restoring the spec made TLC pass. Neither scratch is in the tree.

## Bounds

Each scheduler model is one flow with overlap skip, so at most one run of that flow is going. The early-window model has three minutes, and at most one of them is inside the five-second early start. Catch-up longer than 100, `max_concurrent_runs`, several flows, the pause between retries, and a nested ForEach run are outside the models. `BlockRun` has three attempts. Its liveness rule uses weak fairness: if a step stays possible, one is eventually taken.

A pass is a pass of that model. It is not a pass of every Taskloom flow.

Pre-fix line numbers are from `d6d6a16`. Fixed-module line numbers are from this PR.

## Charts

| Chart | Model states | Chart states | Works | Still wrong | Fixed or new | Bugs |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| [scheduler-check](statecharts/scheduler-check/chart.mmd) | 26 | 36 | 7 | 0 | 29 | 5 |
| [catch-up-busy](statecharts/catch-up-busy/chart.mmd) | 5 | 9 | 2 | 0 | 7 | 1 |
| [overlap-skip](statecharts/overlap-skip/chart.mmd) | 4 | 4 | 4 | 0 | 0 | 0 |
| [retry-grace](statecharts/retry-grace/chart.mmd) | 23 | 20 | 14 | 0 | 6 | 1 |
| [watchdog](statecharts/watchdog/chart.mmd) | 5 | 5 | 4 | 0 | 1 | 1 |
| [block-run](statecharts/block-run/chart.mmd) | 12 | 12 | 12 | 0 | 0 | 0 |

The scheduler chart stayed flat: the two-region drawing did not match the flat chart (88 differences). The other charts stayed flat because a region drawing was larger, or because a green state would have been painted as if it worked. Stories and numbered paths are in each `stories.md`. PNGs sit next to the Mermaid sources.

`BlockRun` is the attempt lifecycle with the real cap of three. TLC also checked the temporal rule `Finishes`: under weak fairness the attempt reaches succeeded, failed, or cancelled. `specs/formal/traces/BlockRunMutant.out` is the same spec with two extra attempts. TLC stops at attempt 4 with `AttemptsEnd` violated.

## Confirmed bugs

Reproduced on `d6d6a16` before the fixes. The tests below pass with the fixes.

### 1. A check a few seconds early drops the on-time minute

Rule, from the chart: a check at 6:32:56, with 6:31, 6:32, and 6:33 in view, starts 6:32 and skips 6:33.

At `d6d6a16` the tick chose a past time and then requested every soon time on its own (`scheduler.py` lines 124–138). Overlap skip then refused the soon time, and `last` had already moved forward, so that minute never came back.

| Misfire | What TLC and the test saw | What the rule requires |
| --- | --- | --- |
| run once | 6:32 runs, 6:33 is skipped | only 6:33 runs |
| skip | 6:32 runs, one minute is counted missed, 6:33 is skipped | 6:33 runs, and both earlier minutes count as missed |
| catch-up | 6:31 runs, 6:32 waits, 6:33 is skipped | all three run, oldest first |

`test_a_check_in_the_early_window_keeps_the_on_time_run` covers all three. The fix chooses among the past times and the early-window time together (`scheduler.py` around the misfire branch in `tick`).

### 2. Catch-up skips the oldest minute of a backlog while a run is going

Rule: 6:31 is still running, the next check sees 6:32 and 6:33, and 6:32 is skipped. Only 6:33 waits.

`force_queue` was `n > 0`, so the first of the new times used overlap skip. `test_catch_up_while_a_run_is_going_does_not_drop_the_oldest_time` holds the 6:31 run, ticks 6:33, and expects 6:31, 6:32, and 6:33. The fix queues every time in a catch-up of more than one time when a run is already going (`serial and (n > 0 or busy)`).

### 3. Accepted runs that have not started are dropped when the scheduler stops

Rule: catch-up has 6:31 running and 6:32 and 6:33 waiting; the scheduler stops; both waiting runs are gone.

`last` was committed before those runs started. The queue lived only on the `Scheduler` object. A new process did not see it, and `due()` does not rediscover a time already passed by `last`. `test_queued_catch_up_runs_still_happen_after_the_scheduler_restarts` queues 8:00 and 9:00 behind 7:00, builds a new `Scheduler` on the same home, and expects all three times in history.

The fix writes them to `schedule_pending` and loads that table on startup. The test compares a sorted list of times. The 7:00 process was already started, and the new scheduler does not adopt it, so its history row can land after 8:00. The run is not lost. Adopting arbitrary running pids was left out, because an editor run would then look like the scheduler's own run.

### 4. A block that finishes during the timeout grace is thrown away

Rule: the block returns a value during the two-second grace, and the engine starts attempt 2 anyway. There are three such counterexamples in the model; the shortest is attempt 1 returning a value, then attempt 2 starting. The same rule also fails when the third attempt's value is discarded and the run fails.

`_attempt` raised `TimeoutError` after `done.wait(STOP_GRACE_SECONDS)` whether or not the worker had stored a value (`engine.py` at `d6d6a16`, lines 517–520). `test_a_block_that_finishes_during_the_timeout_grace_is_kept` sleeps 0.4s with a 150ms timeout and three attempts. Before the fix the marker was written three times and the run failed. It now stays `"1"`, one successful attempt, exit 0.

The fix keeps a finished attempt that stored a value and no error. A block that is still running, and `logic.wait` raising `Cancelled` because the attempt was told to stop, still become `TimeoutError`. `test_block_timeout_fails_the_attempt` still fails a 30s wait with a 500ms timeout. `AttemptsEnd` holds in both grace models: the counter never passes 3.

### 5. A hung server scheduler is not replaced

Rule: the keeper looks at a scheduler whose heartbeat is stale, and the scheduler is still hung. The user is told why the new one did not start.

`_remote_alive` treats a stale heartbeat as dead and the keeper then starts another scheduler. The hung process still holds the folder lock (`acquire_lock`), so the new process exits at once. The old process keeps running, and nothing on that server checks. `test_the_keeper_stops_a_hung_scheduler_so_another_can_start` leaves a process holding the lock with a heartbeat 600 seconds old. After the keeper runs, that process is dead and a new heartbeat is present.

The README says the keeper starts the server scheduler again after a reboot, or tells you why it could not. Telling you is what the old code did, and `YouAreToldWhenItStaysDown` holds. The keeper's own comment says a hung scheduler counts as dead, and a dead one can be replaced. The fix stops a process whose command line contains `scheduler`, then starts a new one. `test_the_keeper_leaves_an_unrelated_process_in_the_pid_file` puts a sleeper in the pid file: the sleeper stays alive, and a scheduler still starts. A command that does not exist is still reported as not started (`test_the_keeper_does_not_say_it_started_a_server_scheduler_whose_command_does_not_exist`).

## False alarms

These were checked and are not bugs.

**One new catch-up minute while a run is going.** `OverlapSkip` has 4 states, all working, and `OneOverlappingTimeFollowsSkip` holds. Catch-up serializes a backlog (`len(planned) > 1`). A single new time still uses overlap skip. `test_one_catch_up_time_while_a_run_is_going_still_follows_overlap_skip` expects the "Run skipped" note and only the run that was already going.

**Catch-up past 100 times.** `CATCH_UP_LIMIT` keeps the oldest 100 and drops the rest. That limit is the code's own bound. It was not given a failing rule.

**Two schedule blocks on one flow.** Overlap is per flow. Both blocks share one running-or-queued slot. That matches the existing scheduler tests and was left as specified.

**A keeper message that claims a start which did not happen.** When the remote command is missing, the keeper must not say the scheduler was started. The existing test locks that in. The hung-scheduler bug is the case where the process to stop is the scheduler itself.

## Not confirmed

These are hypotheses. No test reproduced them, and the fixes above do not claim to close them.

**A crash in the middle of one tick.** `_set_last` and `_remember` are separate autocommit statements. A crash between them can still move `last` past a run that was never written to `schedule_pending`. Clean shutdown after `tick` returns is what the restart test covers.

**A torn heartbeat file.** A partial write could fail the "two numbers" check in `_remote_alive` and look like a dead scheduler. Not observed.

**ForEach and attempt timeout.** An attempt timeout sets `attempt_cancel`. A nested `run_flow` inside ForEach is a separate run. Nothing here shows that the nested run is cancelled with the attempt. Not changed.

**Adopting the already-started process after a restart.** The process keeps running and writes its own history row. The new scheduler does not track its pid. See bug 3 for why that was left out.

## Fixes in this PR

| Bug | Change | Test |
| --- | --- | --- |
| Early-window misfire | One selection over past and soon times | `test_a_check_in_the_early_window_keeps_the_on_time_run` |
| Catch-up backlog while busy | Queue the whole backlog, including the oldest, when a run is already going | `test_catch_up_while_a_run_is_going_does_not_drop_the_oldest_time` |
| Queue lost on restart | `schedule_pending`, loaded in `__init__`, started by `_drain_idle` | `test_queued_catch_up_runs_still_happen_after_the_scheduler_restarts` |
| Grace discards success | A value with no error during the grace stands | `test_a_block_that_finishes_during_the_timeout_grace_is_kept` |
| Hung scheduler keeps the lock | Stop a process whose command line says it is the scheduler, then start | `test_the_keeper_stops_a_hung_scheduler_so_another_can_start` |

Suggested follow-ups, not done: one transaction around `last` and `schedule_pending`, and a look at whether an attempt timeout should cancel a nested ForEach run. Both need a failing test before a fix.
