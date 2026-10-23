---- MODULE BlockRun ----
\* One block inside a run. It starts, then each attempt succeeds, fails and tries again, fails for
\* the last time, or the run is cancelled. The retry limit is three (engine.py:435). Giving up is
\* the last attempt (engine.py:455). Cancel covers the run being cancelled (engine.py:452).
\* Mutant is an artificial extra two attempts, used only to show that AttemptsEnd can fail.
\* The statechart config sets Mutant to FALSE. Not in the model: the timeout grace (RetryGrace),
\* delay, or a nested flow.

EXTENDS Integers

CONSTANTS Mutant

Max == 3

VARIABLES phase, attempt

vars == <<phase, attempt>>

Limit == IF Mutant THEN Max + 2 ELSE Max

Init ==
  /\ phase = "idle"
  /\ attempt = 0

Start ==
  /\ phase = "idle"
  /\ phase' = "running"
  /\ attempt' = 1

Succeed ==
  /\ phase = "running"
  /\ phase' = "succeeded"
  /\ UNCHANGED attempt

Retry ==
  /\ phase = "running"
  /\ attempt < Limit \* engine.py:435
  /\ phase' = "running"
  /\ attempt' = attempt + 1

GiveUp ==
  /\ phase = "running"
  /\ attempt >= Limit \* engine.py:455
  /\ phase' = "failed"
  /\ UNCHANGED attempt

Cancel ==
  /\ phase \in {"idle", "running"} \* engine.py:452
  /\ phase' = "cancelled"
  /\ UNCHANGED attempt

Next == Start \/ Succeed \/ Retry \/ GiveUp \/ Cancel

Spec == Init /\ [][Next]_vars /\ WF_vars(Next)

\* The block is idle, running, or finished, and the attempt counter has not wrapped.
TypeOK ==
  /\ phase \in {"idle", "running", "succeeded", "failed", "cancelled"}
  /\ attempt \in 0..Limit

\* Retries stop at the configured maximum.
AttemptsEnd ==
  attempt <= Max

\* The block eventually succeeds, fails, or is cancelled.
Finishes ==
  <>(phase \in {"succeeded", "failed", "cancelled"})

====
