---- MODULE OverlapSkip ----
\* One new catch-up minute (6:32) while 6:31 is still running. Overlap is skip. A backlog is
\* more than one planned time (scheduler.py:192 in this PR; scheduler.py:136 at d6d6a16, where
\* force_queue is n > 0). One planned time still uses overlap skip (scheduler.py:222 now,
\* scheduler.py:166 then). Both versions do this. The model is the current code.

VARIABLES phase, going, minute

vars == <<phase, going, minute>>

Init ==
  /\ phase = "before"
  /\ going = "running"
  /\ minute = "due"

Check ==
  /\ phase = "before"
  /\ going = "running" \* scheduler.py:220
  /\ minute = "due"
  /\ phase' = "checked"
  /\ minute' = "skipped"
  /\ UNCHANGED going

Finish ==
  /\ going = "running"
  /\ going' = "done"
  /\ UNCHANGED <<phase, minute>>

Next == Check \/ Finish

Spec == Init /\ [][Next]_vars

\* The run that is going, and the single new minute, have a known status.
TypeOK ==
  /\ phase \in {"before", "checked"}
  /\ going \in {"running", "done"}
  /\ minute \in {"due", "skipped"}

\* One new minute while a run is going follows overlap skip.
OneOverlappingTimeFollowsSkip ==
  phase = "checked" => minute = "skipped" /\ going \in {"running", "done"}

====
