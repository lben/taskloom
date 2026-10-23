---
name: verify-taskloom-concurrency
description: Check Taskloom's scheduler, retries, and server keeper against the TLA+ specs in specs/formal. Use when changing tick, misfire, overlap, schedule_pending, attempt timeouts, the block retry loop, or the keeper.
---

# Verify Taskloom concurrency

The specs in `specs/formal` are small TLA+ models of the scheduler check, a catch-up backlog, overlap skip, the attempt timeout grace, the server keeper, and one block attempt. TLC is the checker. The charts under `docs/statecharts` are what those models show a person: gray states work, green states are fixed or new with the fix, and a numbered path is a bug.

A passing run covers the model and the bounds below. It does not cover every flow Taskloom can run.

## When this applies

Re-run the check when a change touches any of these:

- `taskloom/scheduler.py`: `tick`, `_request`, `_remember`, `_forget`, `_load_pending`, `_drain_idle`, `_poll`, `check_servers`, `_stop_hung`, `_remote_alive`, `acquire_lock`, `serve`
- `taskloom/engine.py`: `_run_block` (the retry loop) and `_attempt` (the timeout grace)
- `taskloom/schedule.py`: `Trigger.due`, only if the early-window meaning of `(after, until]` changes
- `taskloom/flow.py`: `Retry`, only if `max_attempts` stops being the cap

## Sync

Last confirmed at commit `09ade8d84dfb6f3a06fedcb6983d9a3880c53c61`.

The pre-fix modules (`SchedulerCheck`, `CatchUpBusy`, `RetryGrace`, `Watchdog`) cite line numbers from `d6d6a16`. The `*Fixed` modules, `OverlapSkip`, and `BlockRun` cite the code at the sync commit. Move the sync commit forward only after you have opened each cited line, confirmed the model still matches, and `specs/formal/check.sh` still exits 0. A green run on a spec you have not re-read is not a confirmation.

## Run

TLC 2.19 (8 August 2024, rev 5a47802) and the pstack statechart script from [cursor/plugins @ 4ec7ffa](https://github.com/cursor/plugins/tree/4ec7ffa/pstack) (`pstack` 0.15.30). Java 21.

```sh
export TLA2TOOLS=/path/to/tla2tools.jar
export STATECHART=/path/to/pstack/skills/statechart/scripts/statechart.mjs
export JAVA=java
specs/formal/check.sh
```

`check.sh` exits 0 only when the pre-fix models break exactly the expected rules, the fixed models and `OverlapSkip` and `BlockRun` hold, and `BlockRunMutant.cfg` breaks `AttemptsEnd`. The charts are replaced under `docs/statecharts/`. Quote counts from each `summary.json`, not from memory.

The Python tests that replay the bugs:

```sh
python3 -m pytest \
  tests/test_scheduler_on_time.py::test_a_check_in_the_early_window_keeps_the_on_time_run \
  tests/test_scheduler_on_time.py::test_catch_up_while_a_run_is_going_does_not_drop_the_oldest_time \
  tests/test_scheduler_on_time.py::test_one_catch_up_time_while_a_run_is_going_still_follows_overlap_skip \
  tests/test_scheduler_on_time.py::test_queued_catch_up_runs_still_happen_after_the_scheduler_restarts \
  tests/test_engine.py::test_a_block_that_finishes_during_the_timeout_grace_is_kept \
  tests/test_engine.py::test_block_timeout_fails_the_attempt \
  tests/test_servers.py::test_the_keeper_stops_a_hung_scheduler_so_another_can_start \
  tests/test_servers.py::test_the_keeper_leaves_an_unrelated_process_in_the_pid_file \
  -q --tb=short
```

## Bounds

One flow, overlap skip, at most one run of that flow at a time. The early-window model has three minutes and at most one of them inside the five-second early start. Catch-up of more than 100 times, `max_concurrent_runs`, several flows, the wait between retries, and a nested `ForEach` run are outside the models. `BlockRun` uses three attempts and weak fairness so a block that can still take a step eventually succeeds, fails, or is cancelled.

## A real bug or a modeling error

- Replay the TLC story with a test that drives `Scheduler.tick` or `taskloom run` and asserts what the run history or the keeper message shows. A test that only reads a flag it set does not count.
- When the test does not show the bad outcome, treat the model as wrong and fix the model. Do not weaken the invariant so TLC passes.
- `OverlapSkip` holding, together with `test_one_catch_up_time_while_a_run_is_going_still_follows_overlap_skip`, means one new catch-up minute while a run is going still follows overlap skip. That is not a bug.
- `AttemptsEnd` holding on `BlockRun.cfg` proves nothing by itself. `specs/formal/traces/BlockRunMutant.out` is the run where two extra attempts make it fail. If you change the retry limit, re-run the mutant and keep a trace that fails.
- Conditions that only read a variable the action does not change need a `\* file:line` comment. `summary.json` field `uncited` must be empty (or absent) before you report that a rule holds.

## What a failure means

| TLC result | Meaning |
| --- | --- |
| An expected rule breaks on a pre-fix module and holds on the matching `*Fixed` module | The old behavior is still described, and the current code matches the fix model. Confirm with the Python test before changing product code. |
| A rule breaks on a `*Fixed` module | The current code's model no longer matches the property. Read the new counterexample before editing the code. |
| `check.sh` says a rule was expected to break but holds | The pre-fix model no longer reaches that bug. Fix the model, or say the bug is gone and remove it from `--expect-broken` only after a test shows the old code path is gone. |
| `BlockRunMutant` passes | The attempt cap is not actually checked. |
