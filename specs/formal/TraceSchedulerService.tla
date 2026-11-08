------------------------- MODULE TraceSchedulerService -------------------------
(***************************************************************************)
(* Trace validation: is a behaviour the real code showed a behaviour of    *)
(* SchedulerService?                                                       *)
(*                                                                         *)
(* packaging/tla_trace.py drives taskloom/scheduler.py (the real Scheduler,*)
(* claim_time, History.start_run, request_turn_off, forget_schedules) with *)
(* stand-in run processes, and writes what happened as the module          *)
(* SchedulerTrace, one per trace:                                          *)
(*  - Setup: each flow's Overlap and Misfire, and the state at the start;  *)
(*  - Trace: the events, in order.  Each names an action of the model, its *)
(*    arguments, and the state the code showed just after it, mapped onto  *)
(*    the model's variables (the refinement mapping: README.md, "Trace     *)
(*    validation").                                                        *)
(* Event i must be a step of the model's Next, by the action it names, into *)
(* a state whose mapped variables are what the code showed.                *)
(*                                                                         *)
(* TLC checks the invariant TraceIncomplete, which is violated exactly when *)
(* a behaviour of the model matches the whole trace.  So "Invariant        *)
(* TraceIncomplete is violated" means the trace MATCHES (TLC prints that   *)
(* behaviour); "No error has been found" means no behaviour of the model   *)
(* matches it, and the depth of the search is the first event that cannot *)
(* be matched.                                                             *)
(***************************************************************************)
EXTENDS SchedulerService, SchedulerTrace

VARIABLE i   \* the next event of the trace to match

\* The model's variables as the code shows them: the run history and the service's saved state (rows, runs,
\* last, epoch, service_on), the paused file, the flow files, the run processes, and the memory of the service on.
\* The ghost variables (fate, stopRows, changedAt) and the bounds' counters are not shown: the model's own steps
\* decide them.
Shown == [now |-> now, lives |-> lives, lock |-> lock, svcOn |-> svcOn,
          paused |-> paused, choice |-> choice, epoch |-> epoch,
          ver |-> ver, schedOn |-> schedOn, last |-> last,
          rows |-> {<<d, row[d]>> : d \in {x \in Dues : row[x] # "none"}},
          runs |-> {<<d, run[d], starter[d]>> : d \in {x \in Dues : run[x] # "no"}},
          manualGoing |-> manualGoing,
          loading |-> {<<p.d, p.l, p.e>> : p \in loading},
          adopted |-> adopted, turning |-> turning, checked |-> checked]

ShowsAfter(e) == \A k \in DOMAIN e.state : e.state[k] = Shown'[k]

\* The action event e names, with its arguments: each one a disjunct of Next.
Step(e) ==
    \/ e.kind = "Tick" /\ Tick
    \/ e.kind = "ServiceStart" /\ ServiceStart
    \/ e.kind = "ServiceCrash" /\ ServiceCrash
    \/ e.kind = "Poll" /\ Poll
    \/ e.kind = "PlanFlow" /\ e.flow \in Flows /\ PlanFlow(e.flow)
    \/ e.kind = "StartNext" /\ e.due \in Dues /\ StartNext(e.due)
    \/ e.kind = "Claim" /\ \E p \in loading : /\ p.d = e.due /\ p.l = e.life
                                              /\ ClaimOK(p) = e.begins   \* it began, or was refused, as the code said
                                              /\ Claim(p)
    \/ e.kind = "KillLoading" /\ \E p \in loading : p.d = e.due /\ p.l = e.life /\ KillLoading(p)
    \/ e.kind = "RunFinish" /\ e.due \in Dues /\ RunFinish(e.due)
    \/ e.kind = "KillRun" /\ e.due \in Dues /\ KillRun(e.due)
    \/ e.kind = "ManualBegin" /\ e.flow \in Flows /\ ManualBegin(e.flow)
    \/ e.kind = "ManualEnd" /\ e.flow \in Flows /\ ManualEnd(e.flow)
    \/ e.kind = "ChangeSchedule" /\ e.flow \in Flows /\ ChangeSchedule(e.flow)
    \/ e.kind = "Proceed" /\ Proceed
    \/ e.kind = "TurnOn" /\ TurnOn

TraceInit == /\ Init
             /\ overlap = Setup.overlap /\ misfire = Setup.misfire
             /\ \A k \in DOMAIN Setup.state : Setup.state[k] = Shown[k]
             /\ i = 1

TraceNext == /\ i <= Len(Trace)
             /\ Step(Trace[i])
             /\ ShowsAfter(Trace[i])
             /\ i' = i + 1

TraceSpec == TraceInit /\ [][TraceNext]_<<vars, i>>

\* Violated exactly when the whole trace is matched (see above).
TraceIncomplete == i <= Len(Trace)
=============================================================================
