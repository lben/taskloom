---- MODULE WatchdogFixed ----
\* The same keeper check as Watchdog, after the fix. A hung scheduler is stopped when its command
\* line says it is the scheduler (scheduler.py:374 and scheduler.py:395), which releases the folder
\* lock, and then a new one is started. A down scheduler is started as before. An up one is left
\* alone (scheduler.py:371).

VARIABLES remote, phase, told

vars == <<remote, phase, told>>

Init ==
  /\ remote \in {"down", "up", "hung"}
  /\ phase = "before"
  /\ told = FALSE

Check ==
  /\ phase = "before"
  /\ phase' = "checked"
  /\ IF remote = "up"
       THEN UNCHANGED <<remote, told>>
       ELSE IF remote = "down"
       THEN remote' = "up" /\ told' = FALSE
       ELSE remote' = "up" /\ told' = FALSE

Next == Check

Spec == Init /\ [][Next]_vars

\* The server scheduler is down, up, or hung, and the keeper has either looked or not.
TypeOK ==
  /\ remote \in {"down", "up", "hung"}
  /\ phase \in {"before", "checked"}
  /\ told \in BOOLEAN

\* After the keeper looks, a scheduler that was dead or hung is checking again.
AHungSchedulerIsReplaced ==
  phase = "checked" => remote = "up"

\* If it is still not checking, the user was told why.
YouAreToldWhenItStaysDown ==
  phase = "checked" /\ remote /= "up" => told

====
