---- MODULE RetryGraceFixed ----
\* The same grace window as RetryGrace, after the fix. A value with no error is kept
\* (engine.py:522). A real error is the block's own (engine.py:526) and may be retried.
\* Still running, or Cancelled only because this attempt was told to stop, is a timeout
\* (engine.py:529) and may be retried. Three attempts at most (engine.py:435).

EXTENDS Integers

Max == 3

VARIABLES phase, attempt, returned, sawValue

vars == <<phase, attempt, returned, sawValue>>

Init ==
  /\ phase = "running"
  /\ attempt = 1
  /\ returned = "none"
  /\ sawValue = FALSE

FinishWork ==
  /\ phase = "running"
  /\ returned = "none"
  /\ phase' = "grace"
  /\ returned' \in {"value", "stuck", "error"}
  /\ sawValue' = (sawValue \/ returned' = "value")
  /\ UNCHANGED attempt

\* engine.py:522 keeps a value. Anything else follows the retry limit (engine.py:455).
Decide ==
  /\ phase = "grace"
  /\ IF returned = "value"
       THEN phase' = "succeeded" /\ UNCHANGED <<attempt, returned, sawValue>>
       ELSE /\ sawValue' = sawValue
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
