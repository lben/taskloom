---- MODULE Watchdog ----
\* The keeper looks at one server scheduler. It can be down, up, or hung (process alive, heartbeat
\* older than 120 seconds). At commit d6d6a16, a stale heartbeat counts as dead (scheduler.py:341)
\* and the keeper tries to start another scheduler (scheduler.py:317). The hung process still holds
\* the folder lock (scheduler.py:399), so the new one exits and the user is told. Not in the model:
\* a bad command, an unreachable server, or the five-minute pause between checks.

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
       ELSE remote' = "hung" /\ told' = TRUE

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
