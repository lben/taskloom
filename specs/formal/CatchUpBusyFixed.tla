---- MODULE CatchUpBusyFixed ----
\* The same backlog as CatchUpBusy, after the fix. A catch-up of more than one time is serialized
\* even when a run is already going, including the oldest minute (scheduler.py:192).

VARIABLES phase, older, previous, onTime

vars == <<phase, older, previous, onTime>>

Init ==
  /\ phase = "before"
  /\ older = "running"
  /\ previous = "due"
  /\ onTime = "due"

Check ==
  /\ phase = "before"
  /\ older = "running" \* scheduler.py:191
  /\ previous = "due"
  /\ onTime = "due"
  /\ phase' = "checked"
  /\ previous' = "queued"
  /\ onTime' = "queued"
  /\ UNCHANGED older

Finish ==
  /\ older = "running" \/ previous = "running" \/ onTime = "running"
  /\ LET going == IF older = "running" THEN "older"
                  ELSE IF previous = "running" THEN "previous"
                  ELSE "onTime"
         nxt == IF previous = "queued" THEN "previous"
                ELSE IF onTime = "queued" THEN "onTime"
                ELSE "none"
     IN /\ older' = IF going = "older" THEN "done" ELSE older
        /\ previous' = IF going = "previous" THEN "done"
                       ELSE IF nxt = "previous" THEN "running"
                       ELSE previous
        /\ onTime' = IF going = "onTime" THEN "done"
                     ELSE IF nxt = "onTime" THEN "running"
                     ELSE onTime
  /\ UNCHANGED phase

Next == Check \/ Finish

Spec == Init /\ [][Next]_vars

\* Each minute has a known status.
TypeOK ==
  /\ phase \in {"before", "checked"}
  /\ older \in {"running", "done"}
  /\ previous \in {"due", "skipped", "queued", "running", "done"}
  /\ onTime \in {"due", "skipped", "queued", "running", "done"}

\* Two runs of this flow are never going together.
OneRunAtATime ==
  /\ ~(older = "running" /\ previous = "running")
  /\ ~(older = "running" /\ onTime = "running")
  /\ ~(previous = "running" /\ onTime = "running")

\* Catch-up does not skip the oldest minute of a backlog while a run is going.
CatchUpDoesNotSkipTheOldest ==
  phase = "checked" =>
    /\ previous \in {"queued", "running", "done"}
    /\ onTime \in {"queued", "running", "done"}

====
