---- MODULE CatchUpBusy ----
\* catch_up, overlap skip, and a run already going (6:31). The next check sees two due minutes,
\* 6:32 then 6:33. At commit d6d6a16, force_queue is only n > 0 (scheduler.py:136), so 6:32 uses
\* overlap skip (scheduler.py:166) and is lost. 6:33 is queued. One flow, two new times, no early
\* window. Not in the model: a single new time (that case is OverlapSkip) or more than 100 times.

VARIABLES phase, older, previous, onTime

vars == <<phase, older, previous, onTime>>

Init ==
  /\ phase = "before"
  /\ older = "running"
  /\ previous = "due"
  /\ onTime = "due"

Check ==
  /\ phase = "before"
  /\ older = "running" \* scheduler.py:163
  /\ previous = "due"
  /\ onTime = "due"
  /\ phase' = "checked"
  /\ previous' = "skipped"
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
