---- MODULE SchedulerCheckFixed ----
\* The same check as SchedulerCheck, after the fix in this PR. Misfire chooses among the past
\* times and the early-window time together (scheduler.py:166). A catch-up of more than one time
\* is queued behind a run that is already going (scheduler.py:192). Accepted runs are stored in
\* schedule_pending (scheduler.py:86) and loaded again (scheduler.py:98), so stopping the
\* scheduler no longer drops them. Line numbers are this PR.

EXTENDS Integers

Statuses == {"due", "running", "queued", "missed", "skipped", "done", "dropped"}

VARIABLES policy, phase, up, older, previous, onTime

vars == <<policy, phase, up, older, previous, onTime>>

\* The same two starting points as SchedulerCheck. The backlog is what _load_pending restores
\* (scheduler.py:98) together with a run that had already started.
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

\* scheduler.py:167 run_once keeps only the newest time. scheduler.py:176 skip, when a soon time
\* exists, runs that time and counts every past time as missed. scheduler.py:171 catch_up keeps
\* the soon time in the same oldest-first list.
Check ==
  /\ up \* scheduler.py:532
  /\ older = "due"
  /\ previous = "due"
  /\ onTime = "due"
  /\ phase' = "checked"
  /\ IF policy = "catch_up"
       THEN older' = "running" /\ previous' = "queued" /\ onTime' = "queued"
       ELSE older' = "missed" /\ previous' = "missed" /\ onTime' = "running"
  /\ UNCHANGED <<policy, up>>

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

\* scheduler.py:536 still says running jobs continue. Queued jobs stay queued: they were written
\* to schedule_pending before the process stopped (scheduler.py:86).
Stop ==
  /\ up
  /\ older = "queued" \/ previous = "queued" \/ onTime = "queued" \* scheduler.py:105
  /\ up' = FALSE
  /\ older' = older
  /\ previous' = previous
  /\ onTime' = onTime
  /\ UNCHANGED <<policy, phase>>

Resume ==
  /\ ~up
  /\ up' = TRUE
  /\ UNCHANGED <<policy, phase, older, previous, onTime>>

\* scheduler.py:108 loads a queued run whose flow has nothing going, on the next tick (scheduler.py:144).
Drain ==
  /\ up \* scheduler.py:144
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
