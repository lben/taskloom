---- MODULE RetryGrace ----
\* One block attempt with a timeout. The worker can return a value, raise its own error, or still
\* be running when the two-second grace ends. At commit d6d6a16, _attempt always raises TimeoutError
\* after that wait (engine.py:517 to engine.py:520), so a value returned during the grace is
\* discarded and the attempt is retried, up to three attempts (engine.py:435). Not in the model:
\* the delay between attempts, jitter, or a nested flow.

EXTENDS Integers

Max == 3

VARIABLES phase, attempt, returned, sawValue

vars == <<phase, attempt, returned, sawValue>>

Init ==
  /\ phase = "running"
  /\ attempt = 1
  /\ returned = "none"
  /\ sawValue = FALSE

\* The worker finishes inside the grace window, or does not.
FinishWork ==
  /\ phase = "running"
  /\ returned = "none"
  /\ phase' = "grace"
  /\ returned' \in {"value", "stuck", "error"}
  /\ sawValue' = (sawValue \/ returned' = "value")
  /\ UNCHANGED attempt

\* engine.py:520 raises TimeoutError no matter what the worker stored.
Decide ==
  /\ phase = "grace"
  /\ sawValue' = sawValue
  /\ IF attempt < Max
       THEN phase' = "running" /\ attempt' = attempt + 1 /\ returned' = "none"
       ELSE phase' = "failed" /\ attempt' = attempt /\ returned' = returned

Next == FinishWork \/ Decide

Spec == Init /\ [][Next]_vars

\* The attempt counter, the worker's result, and where the engine is.
TypeOK ==
  /\ phase \in {"running", "grace", "succeeded", "failed"}
  /\ attempt \in 1..Max
  /\ returned \in {"none", "value", "stuck", "error"}
  /\ sawValue \in BOOLEAN

\* A value returned during the grace period is the attempt's result.
SuccessSticks ==
  sawValue => phase \in {"grace", "succeeded"}

\* Retries stop at the configured maximum.
AttemptsEnd ==
  attempt <= Max

====
