---- MODULE SchedulerCheck ----
\* One scheduler check, a few seconds before the next minute, after the scheduler was off.
\* Three minutes are in that check: 6:31 (older), 6:32 (previous), and 6:33 (on time, still
\* inside the five-second early window). Overlap is skip, so only one run of this flow goes
\* at a time. This module is the behavior at commit d6d6a16. Line numbers below are from that
\* commit. Not in the model: more than one flow, a catch-up longer than 100, max_concurrent_runs,
\* or a crash in the middle of the check.

EXTENDS Integers

Statuses == {"due", "running", "queued", "missed", "skipped", "done", "dropped"}

VARIABLES policy, phase, up, older, previous, onTime

vars == <<policy, phase, up, older, previous, onTime>>

\* Either the early-window check has not happened yet, or a catch-up backlog is already accepted
\* (6:31 running, 6:32 and 6:33 waiting). The backlog is the state after a check that is not inside
\* the early window: the oldest starts, and the later times are queued (scheduler.py:136).
Init ==
  \/ /\ policy \in {"run_once", "skip", "catch_up"}
     /\ phase = "before"
     /\ up = TRUE
     /\ older = "due"
     /\ previous = "due"
     /\ onTime = "due"
  \/ /\ policy = "catch_up"
     /\ phase = "checked"
     /\ up = TRUE
     /\ older = "running"
     /\ previous = "queued"
     /\ onTime = "queued"

\* One tick. Past times are chosen first, then every soon time is requested on its own
\* (scheduler.py:124 and scheduler.py:137). force_queue is only n > 0 (scheduler.py:136),
\* so the soon time hits overlap skip (scheduler.py:166) once a past time is running or queued.
Check ==
  /\ up \* scheduler.py:454
  /\ older = "due"
  /\ previous = "due"
  /\ onTime = "due"
  /\ phase' = "checked"
  /\ IF policy = "catch_up"
       THEN older' = "running" /\ previous' = "queued" /\ onTime' = "skipped"
       ELSE older' = "missed" /\ previous' = "running" /\ onTime' = "skipped"
  /\ UNCHANGED <<policy, up>>

\* A started run finishes. While this process is still up, the oldest queued run starts
\* (scheduler.py:245). A run that already started keeps going after the process stops.
Finish ==
  /\ older = "running" \/ previous = "running" \/ onTime = "running"
  /\ LET going == IF older = "running" THEN "older"
                  ELSE IF previous = "running" THEN "previous"
                  ELSE "onTime"
         nxt == IF up /\ older = "queued" THEN "older"
                ELSE IF up /\ previous = "queued" THEN "previous"
                ELSE IF up /\ onTime = "queued" THEN "onTime"
                ELSE "none"
     IN /\ older' = IF going = "older" THEN "done" ELSE IF nxt = "older" THEN "running" ELSE older
        /\ previous' = IF going = "previous" THEN "done" ELSE IF nxt = "previous" THEN "running" ELSE previous
        /\ onTime' = IF going = "onTime" THEN "done" ELSE IF nxt = "onTime" THEN "running" ELSE onTime
  /\ UNCHANGED <<policy, phase, up>>

\* The process exits. Accepted runs that have not started live only in memory (scheduler.py:170)
\* and are gone (scheduler.py:458).
Stop ==
  /\ up
  /\ older = "queued" \/ previous = "queued" \/ onTime = "queued" \* scheduler.py:170
  /\ up' = FALSE
  /\ older' = IF older = "queued" THEN "dropped" ELSE older
  /\ previous' = IF previous = "queued" THEN "dropped" ELSE previous
  /\ onTime' = IF onTime = "queued" THEN "dropped" ELSE onTime
  /\ UNCHANGED <<policy, phase>>

Resume ==
  /\ ~up
  /\ up' = TRUE
  /\ UNCHANGED <<policy, phase, older, previous, onTime>>

\* Start a queued run when nothing is going. The old process never reloads a queue, so this
\* stays disabled there. The guard is the same shape as _poll (scheduler.py:245).
Drain ==
  /\ up \* scheduler.py:245
  /\ older /= "running"
  /\ previous /= "running"
  /\ onTime /= "running"
  /\ LET nxt == IF older = "queued" THEN "older"
                ELSE IF previous = "queued" THEN "previous"
                ELSE IF onTime = "queued" THEN "onTime"
                ELSE "none"
     IN /\ nxt /= "none"
        /\ older' = IF nxt = "older" THEN "running" ELSE older
        /\ previous' = IF nxt = "previous" THEN "running" ELSE previous
        /\ onTime' = IF nxt = "onTime" THEN "running" ELSE onTime
  /\ UNCHANGED <<policy, phase, up>>

Next == Check \/ Finish \/ Stop \/ Resume \/ Drain

Spec == Init /\ [][Next]_vars

\* The three minute-slots have a known status, and the scheduler is either up or not.
TypeOK ==
  /\ policy \in {"run_once", "skip", "catch_up"}
  /\ phase \in {"before", "checked"}
  /\ up \in BOOLEAN
  /\ older \in Statuses
  /\ previous \in Statuses
  /\ onTime \in Statuses

\* Overlap skip: two runs of this flow are never going together.
OneRunAtATime ==
  /\ ~(older = "running" /\ previous = "running")
  /\ ~(older = "running" /\ onTime = "running")
  /\ ~(previous = "running" /\ onTime = "running")

\* Run once keeps the newest minute and does not start an older one.
RunOnceKeepsTheNewest ==
  policy = "run_once" /\ phase = "checked" =>
    older = "missed" /\ previous = "missed" /\ onTime \in {"running", "queued", "done"}

\* Skip starts the on-time minute.
SkipKeepsTheOnTimeRun ==
  policy = "skip" /\ phase = "checked" =>
    onTime \in {"running", "queued", "done"}

\* Skip counts every earlier minute as missed.
SkipCountsTheMissedRuns ==
  policy = "skip" /\ phase = "checked" =>
    older = "missed" /\ previous = "missed"

\* Catch-up keeps every minute in the check while the scheduler is still running.
CatchUpKeepsEveryTime ==
  policy = "catch_up" /\ phase = "checked" /\ up =>
    /\ older \in {"running", "queued", "done"}
    /\ previous \in {"running", "queued", "done"}
    /\ onTime \in {"running", "queued", "done"}

\* A run that was accepted is not thrown away when the scheduler stops.
QueuedWorkIsNotDropped ==
  /\ older /= "dropped"
  /\ previous /= "dropped"
  /\ onTime /= "dropped"

====
