--------------------------- MODULE SchedulerService ---------------------------
(***************************************************************************)
(* Taskloom's scheduler-service protocol: the TARGET design of 2026-11-07, *)
(* not every detail of taskloom/scheduler.py.                               *)
(*                                                                         *)
(*  - One service at a time per Taskloom folder holds the lock.  Starting  *)
(*    a life takes the lock and, in the same step, makes the service-on    *)
(*    record name it (the previous service's term ends).                   *)
(*  - The service checks repeatedly.  A time that has come is judged by    *)
(*    Misfire and Overlap; a run is started for it (a run process), or it *)
(*    is skipped and told, or it waits in a saved line (waiting_runs).     *)
(*  - A run process loads, then CLAIMS as it begins: one atomic step that  *)
(*    checks the term, the schedule and the turn-off, takes its row and    *)
(*    writes its own history row.  Refused, it ends; its row stays.        *)
(*  - The service adopts the scheduled runs going that it did not start.   *)
(*  - Turning off: Proceed in the dialog, with a per-flow choice.         *)
(*  - The user changes or turns off schedules, starts runs by hand and     *)
(*    turns the service on again; processes are killed; the clock moves.  *)
(*                                                                         *)
(* Each promise is named by its row in docs/promises/scheduler.yaml.       *)
(***************************************************************************)
EXTENDS Integers, FiniteSets, Sequences, TLC

CONSTANTS
    Flows,        \* the scheduled flows (model values; symmetric)
    MaxTime,      \* the clock runs 0..MaxTime; each schedule is due at 1..MaxTime
    MaxLives,     \* service lives (starts) in one behaviour
    Limit,        \* Max concurrent runs
    MaxManual,    \* runs the user starts by hand
    MaxChanges,   \* schedule changes or schedule turn-offs by the user (editor)
    MaxTurnOffs,  \* times the user clicks Proceed in the turn-off dialog
    MaxKills,     \* run processes killed (while loading, or going)
    Overlaps,     \* the Overlap settings a flow may have
    Misfires,     \* the Misfire settings a flow may have
    \* Design variants (FALSE: the target design as decided)
    ClaimChecksOverlap,   \* the claim also refuses while another run of the flow goes (Overlap not allow)
    TermOnTurnOff,        \* Proceed and turn-on end the term of the processes loading (an epoch they must match)
    ProceedDropsStop,     \* Proceed itself drops the Stop-now flows' saved runs, in its own transaction
    EditorStartsSchedule  \* a changed schedule is due from the change: the editor writes its state

ASSUME Limit >= 1 /\ MaxTime >= 1 /\ MaxLives >= 1
ASSUME Overlaps \subseteq {"skip", "queue", "allow"} /\ Misfires \subseteq {"skip", "run_once", "catch_up"}

Times    == 1..MaxTime
Versions == 0..MaxChanges
Lives    == 1..MaxLives
Dues     == Flows \X Versions \X Times     \* a due time: <<flow, schedule version, time>>
F(d) == d[1]
V(d) == d[2]
T(d) == d[3]
WaitKinds == {"queue", "slot"}           \* a saved run waiting its turn (for its flow, for a free slot)
Min(S) == CHOOSE x \in S : \A y \in S : x <= y
Max(S) == CHOOSE x \in S : \A y \in S : x >= y
RECURSIVE Ascending(_)
Ascending(S) == IF S = {} THEN <<>> ELSE <<Min(S)>> \o Ascending(S \ {Min(S)})

VARIABLES
    now,                  \* the clock
    overlap, misfire,     \* each flow's settings (chosen at the start, fixed)
    ver, schedOn,         \* each flow's schedule as the editor wrote it: its version, and on/off
    changes,              \* schedule changes so far
    lock,                 \* the life holding the folder's lock (its process is alive); 0: none
    svcOn,                \* the service-on record in the database: the life whose term it is
    lives,                \* lives started so far
    paused,               \* scheduler.paused: the user turned the service off (Proceed)
    choice,               \* the per-flow choice at Proceed: "none" | "finish" | "stop"
    offs,                 \* Proceeds so far
    epoch,                \* turn-off/turn-on epoch (variant TermOnTurnOff only; else 0)
    last,                 \* schedule_state: per flow [v |-> version seen (-1: none), t |-> newest time handled]
    row,                  \* waiting_runs: per due time "none" | "time" (started for its time) | "queue" | "slot"
    loading,              \* the run processes loading: [d, l (life that started it), e (epoch), off, chg, man]
    run,                  \* the run history and the run's process: "no" | "going" | "died" (process gone,
                          \* history says running) | "over"
    starter,              \* the life whose process began the run
    manualGoing,          \* per flow: a run the user started by hand is going
    manuals, kills,       \* bounds
    adopted,              \* the service on (memory): the runs going it adopted at its checks
    turning,              \* the service on (memory): it has seen the turn-off
    checked,              \* the service on (memory): it has checked at least once
    fate,                 \* ghost: what became of each due time
    stopRows,             \* ghost: <<due, life>> saved for a Stop-now flow at Proceed, under that life's term
    changedAt             \* ghost: per flow, the time of its last schedule change

vars == <<now, overlap, misfire, ver, schedOn, changes, lock, svcOn, lives, paused, choice, offs, epoch, last,
          row, loading, run, starter, manualGoing, manuals, kills, adopted,
          turning, checked, fate, stopRows, changedAt>>

Fates == {"none",     \* not handled yet
          "void",     \* SCH-10: a time of a changed schedule from before it starts afresh: neither runs nor is missed
          "saved",    \* a row in waiting_runs
          "begun",    \* claimed: in the run history
          "skipped",  \* Overlap skip, told
          "missed",   \* Misfire, told
          "dropped"}  \* by a user choice: Stop now, or the schedule changed or turned off

NewProc(d, L) == [d |-> d, l |-> L, e |-> epoch, off |-> FALSE, chg |-> FALSE, man |-> FALSE]
HasLoading(d, L) == \E p \in loading : p.d = d /\ p.l = L

(* What the service on (life L) counts as going: the scheduled runs going that it started or adopted, and *)
(* its own processes loading (in the processes `ld`).                                                     *)
OwnIn(L, ld) == {d \in Dues : run[d] = "going" /\ (starter[d] = L \/ d \in adopted)}
                  \cup {p.d : p \in {q \in ld : q.l = L}}
FlowGoingIn(L, f, ld) == (\E d \in OwnIn(L, ld) : F(d) = f) \/ manualGoing[f]  \* Overlap counts runs by hand
AtLimitIn(L, ld) == Cardinality(OwnIn(L, ld)) >= Limit                           \* the limit does not
OtherGoing(d) == manualGoing[F(d)] \/ \E d2 \in Dues : d2 # d /\ F(d2) = F(d) /\ run[d2] = "going"

-----------------------------------------------------------------------------
Init ==
    /\ now = 0
    /\ overlap \in [Flows -> Overlaps]
    /\ misfire \in [Flows -> Misfires]
    /\ ver = [f \in Flows |-> 0]
    /\ schedOn = [f \in Flows |-> TRUE]
    /\ changes = 0
    /\ lock = 0 /\ svcOn = 0 /\ lives = 0
    /\ paused = FALSE
    /\ choice = [f \in Flows |-> "none"]
    /\ offs = 0 /\ epoch = 0
    /\ last = [f \in Flows |-> [v |-> 0, t |-> 0]]   \* the schedules were set up (and seen) before time 1
    /\ row = [d \in Dues |-> "none"]
    /\ loading = {}
    /\ run = [d \in Dues |-> "no"]
    /\ starter = [d \in Dues |-> 0]
    /\ manualGoing = [f \in Flows |-> FALSE]
    /\ manuals = 0 /\ kills = 0
    /\ adopted = {} /\ turning = FALSE /\ checked = FALSE
    /\ fate = [d \in Dues |-> "none"]
    /\ stopRows = {}
    /\ changedAt = [f \in Flows |-> 0]

\* ---- The clock --------------------------------------------------------------
Tick == /\ now < MaxTime
        /\ now' = now + 1
        /\ UNCHANGED <<overlap, misfire, ver, schedOn, changes, lock, svcOn, lives, paused, choice, offs, epoch,
                       last, row, loading, run, starter, manualGoing, manuals, kills, adopted, turning, checked,
                       fate, stopRows, changedAt>>

\* ---- Service lives (SCH-1, SCH-2, SCH-3) -----------------------------------------
\* A new life starts only while none holds the lock and the user has not turned the service off. Taking the
\* lock and naming itself in the service-on record are one step: the term of the life before ends.
ServiceStart ==
    /\ lock = 0 /\ ~paused /\ lives < MaxLives
    /\ lives' = lives + 1 /\ lock' = lives + 1 /\ svcOn' = lives + 1
    /\ adopted' = {} /\ turning' = FALSE /\ checked' = FALSE
    /\ UNCHANGED <<now, overlap, misfire, ver, schedOn, changes, paused, choice, offs,
                   epoch, last, row, loading, run, starter, manualGoing, manuals, kills, fate, stopRows, changedAt>>

\* The service process dies (crash, kill, PC restart of the service alone): the OS lets go of its lock; the
\* service-on record still names it; its memory is gone; its run processes go on.
ServiceCrash ==
    /\ lock # 0
    /\ lock' = 0 /\ adopted' = {} /\ turning' = FALSE /\ checked' = FALSE
    /\ UNCHANGED <<now, overlap, misfire, ver, schedOn, changes, svcOn, lives, paused, choice, offs, epoch, last,
                   row, loading, run, starter, manualGoing, manuals, kills, fate, stopRows, changedAt>>

\* ---- The service's check, part 1: poll (one atomic step) ----------------------------
\* It reads the run history: a run whose process died is recorded as crashed (SCH-13); the scheduled runs going
\* that it did not start are adopted. It reads scheduler.paused: seen for the first time, the turn-off is applied
\* (Stop now: the flow's runs going are stopped and recorded as cancelled, its saved runs dropped, its own processes
\* loading asked to stop); turned on again, it is on. Turned off and nothing it lets finish is going or waiting,
\* the service ends.
Poll ==
    LET L == lock
        applyOff == paused /\ ~turning
        stopF == IF applyOff THEN {f \in Flows : choice[f] = "stop"} ELSE {}
        run1 == [d \in Dues |-> IF run[d] = "died" THEN "over"
                                ELSE IF run[d] = "going" /\ F(d) \in stopF THEN "over"
                                ELSE run[d]]
        row1 == [d \in Dues |-> IF F(d) \in stopF THEN "none" ELSE row[d]]
        fate1 == [d \in Dues |-> IF F(d) \in stopF /\ row[d] # "none" THEN "dropped" ELSE fate[d]]
        loading1 == {p \in loading : ~(p.l = L /\ F(p.d) \in stopF)}
        finishF == {f \in Flows : choice[f] = "finish"}
        done == /\ paused
                /\ \A d \in Dues : run1[d] # "going"
                /\ \A p \in loading1 : p.l # L
                /\ \A d \in Dues : F(d) \in finishF => row1[d] \notin WaitKinds
    IN
    /\ lock # 0
    /\ run' = run1 /\ row' = row1 /\ fate' = fate1 /\ loading' = loading1
    /\ IF done
       THEN /\ lock' = 0 /\ adopted' = {} /\ turning' = FALSE /\ checked' = FALSE
       ELSE /\ lock' = L
            /\ adopted' = {d \in Dues : run1[d] = "going" /\ starter[d] # L}
            /\ turning' = paused
            /\ checked' = TRUE
    /\ UNCHANGED <<now, overlap, misfire, ver, schedOn, changes, svcOn, lives, paused, choice, offs, epoch, last,
                   starter, manualGoing, manuals, kills, stopRows, changedAt>>

\* ---- The service's check, part 2: one schedule's times (one transaction) -------------
\* One request, as Overlap and the limit say, given what this check has done so far (`st`).
BusyIn(L, f, st) == FlowGoingIn(L, f, st.loading) \/ \E d \in Dues : F(d) = f /\ st.row[d] \in WaitKinds
SlotLineIn(L, st) == \E d \in Dues : st.row[d] = "slot" /\ ~\E p \in st.loading : p.d = d /\ p.l = L

RECURSIVE Plan(_, _, _, _, _, _)
Plan(L, f, v, ts, behind, st) ==
    IF ts = <<>> THEN st
    ELSE LET t == Head(ts)
             d == <<f, v, t>>
             policy == IF behind THEN "queue" ELSE overlap[f]
             busy == BusyIn(L, f, st)
             onTime == Len(ts) = 1 /\ t = now         \* only the newest, on time, starts for its time
             next == CASE busy /\ policy = "skip" ->
                            [st EXCEPT !.row[d] = "none", !.fate[d] = "skipped"]       \* SCH-11: told
                       [] busy /\ policy = "queue" ->
                            [st EXCEPT !.row[d] = "queue", !.fate[d] = "saved"]        \* waits for its flow
                       [] onTime /\ ~SlotLineIn(L, st) /\ ~AtLimitIn(L, st.loading) ->
                            [st EXCEPT !.row[d] = "time", !.fate[d] = "saved",
                                       !.loading = @ \cup {NewProc(d, L)}]             \* starts now
                       [] OTHER ->
                            [st EXCEPT !.row[d] = "slot", !.fate[d] = "saved"]         \* waits for a slot
         IN Plan(L, f, v, Tail(ts), behind, next)

\* The times of flow f's schedule that have come since it last looked, and its times whose run did not begin
\* (a row "time" with no process of this life: refused, or killed while loading), judged by Misfire (late: the
\* time is before now) and then requested oldest first. A schedule it has not seen (changed) starts afresh.
PlanFlow(f) ==
    LET L == lock IN
    /\ lock # 0 /\ checked /\ ~turning /\ schedOn[f]
    /\ IF last[f].v # ver[f]
       THEN /\ last' = [last EXCEPT ![f] = [v |-> ver[f], t |-> now]]
            /\ fate' = [d \in Dues |-> IF F(d) = f /\ V(d) = ver[f] /\ T(d) <= now /\ fate[d] = "none"
                                       THEN "void" ELSE fate[d]]
            /\ UNCHANGED <<row, loading>>
       ELSE LET v == ver[f]
                newT == {t \in Times : last[f].t < t /\ t <= now}
                againT == {t \in Times : row[<<f, v, t>>] = "time" /\ ~HasLoading(<<f, v, t>>, L)}
                due == newT \cup againT
                planned == CASE misfire[f] = "skip"     -> {t \in due : t = now}
                             [] misfire[f] = "run_once" -> {Max(due)}
                             [] misfire[f] = "catch_up" -> due
                late == {t \in due : t < now}
                catchingUp == \E d \in Dues : F(d) = f /\ V(d) = v
                                               /\ (d \in OwnIn(L, loading) \/ row[d] \in WaitKinds)
                behind == misfire[f] = "catch_up"
                          /\ (Cardinality(planned) > 1 \/ (overlap[f] = "allow" /\ late # {} /\ catchingUp))
                st0 == [row |-> [d \in Dues |-> IF F(d) = f /\ V(d) = v /\ T(d) \in againT THEN "none" ELSE row[d]],
                        loading |-> loading,
                        fate |-> [d \in Dues |-> IF F(d) = f /\ V(d) = v /\ T(d) \in due \ planned
                                                 THEN "missed" ELSE fate[d]]]          \* SCH-9: told
                st == Plan(L, f, v, Ascending(planned), behind, st0)
            IN /\ due # {}
               /\ row' = st.row /\ loading' = st.loading /\ fate' = st.fate
               /\ last' = [last EXCEPT ![f].t = now]
    /\ UNCHANGED <<now, overlap, misfire, ver, schedOn, changes, lock, svcOn, lives, paused, choice, offs, epoch,
                   run, starter, manualGoing, manuals, kills, adopted, turning, checked, stopRows, changedAt>>

\* ---- The service's check, part 3: a saved run starts when it may (one step each) -------
\* Any waiting row may start next (this over-approximates "in the order they came"). It starts when a slot is free
\* and, unless its Overlap is allow (a row queued for its flow always waits), no run of its flow is going (by hand
\* too). While turning off, only the waiting runs of flows chosen Let it finish start.
StartNext(d) ==
    LET L == lock
        f == F(d)
    IN
    /\ lock # 0 /\ checked
    /\ row[d] \in WaitKinds
    /\ ~HasLoading(d, L)
    /\ ~turning \/ choice[f] = "finish"
    /\ ~AtLimitIn(L, loading)
    /\ (row[d] = "queue" \/ overlap[f] # "allow") => ~FlowGoingIn(L, f, loading)
    /\ loading' = loading \cup {NewProc(d, L)}
    /\ UNCHANGED <<now, overlap, misfire, ver, schedOn, changes, lock, svcOn, lives, paused, choice, offs, epoch,
                   last, row, run, starter, manualGoing, manuals, kills, adopted, turning, checked, fate, stopRows,
                   changedAt>>

\* ---- Run processes ------------------------------------------------------------------
\* The claim, as the run begins: one transaction. Refused, the process ends and nothing is written.
ClaimOK(p) ==
    LET d == p.d
        f == F(d)
        letFinish == row[d] \in WaitKinds /\ choice[f] = "finish"
    IN /\ lock # 0 /\ svcOn = p.l                  \* the lock is held and the term is its service's
       /\ ver[f] = V(d) /\ schedOn[f]              \* its schedule is unchanged and on
       /\ row[d] # "none"                          \* its row is still saved: it takes it
       /\ ~paused \/ letFinish                     \* not turned off, but a let-finish waiting run
       /\ TermOnTurnOff => (p.e = epoch \/ (paused /\ letFinish))
       /\ ClaimChecksOverlap => (overlap[f] = "allow" \/ ~OtherGoing(d))

Claim(p) ==
    /\ p \in loading
    /\ IF ClaimOK(p)
       THEN /\ run' = [run EXCEPT ![p.d] = "going"]
            /\ starter' = [starter EXCEPT ![p.d] = p.l]
            /\ row' = [row EXCEPT ![p.d] = "none"]
            /\ fate' = [fate EXCEPT ![p.d] = "begun"]
       ELSE UNCHANGED <<run, starter, row, fate>>
    /\ loading' = loading \ {p}
    /\ UNCHANGED <<now, overlap, misfire, ver, schedOn, changes, lock, svcOn, lives, paused, choice, offs, epoch,
                   last, manualGoing, manuals, kills, adopted, turning, checked, stopRows, changedAt>>

KillLoading(p) ==
    /\ p \in loading /\ kills < MaxKills
    /\ loading' = loading \ {p} /\ kills' = kills + 1
    /\ UNCHANGED <<now, overlap, misfire, ver, schedOn, changes, lock, svcOn, lives, paused, choice, offs, epoch,
                   last, row, run, starter, manualGoing, manuals, adopted, turning, checked, fate, stopRows,
                   changedAt>>

RunFinish(d) ==   \* it ends and records its end itself
    /\ run[d] = "going"
    /\ run' = [run EXCEPT ![d] = "over"]
    /\ UNCHANGED <<now, overlap, misfire, ver, schedOn, changes, lock, svcOn, lives, paused, choice, offs, epoch,
                   last, row, loading, starter, manualGoing, manuals, kills, adopted, turning, checked, fate,
                   stopRows, changedAt>>

KillRun(d) ==     \* its process dies; the history still says running
    /\ run[d] = "going" /\ kills < MaxKills
    /\ run' = [run EXCEPT ![d] = "died"] /\ kills' = kills + 1
    /\ UNCHANGED <<now, overlap, misfire, ver, schedOn, changes, lock, svcOn, lives, paused, choice, offs, epoch,
                   last, row, loading, starter, manualGoing, manuals, adopted, turning, checked, fate, stopRows,
                   changedAt>>

\* ---- The user ---------------------------------------------------------------------
\* The user's Run always starts (it records itself in the run history as it begins).
ManualBegin(f) ==
    /\ manuals < MaxManual /\ ~manualGoing[f]
    /\ manualGoing' = [manualGoing EXCEPT ![f] = TRUE] /\ manuals' = manuals + 1
    /\ loading' = {IF F(p.d) = f THEN [p EXCEPT !.man = TRUE] ELSE p : p \in loading}
    /\ UNCHANGED <<now, overlap, misfire, ver, schedOn, changes, lock, svcOn, lives, paused, choice, offs, epoch,
                   last, row, run, starter, kills, adopted, turning, checked, fate, stopRows, changedAt>>

ManualEnd(f) ==
    /\ manualGoing[f]
    /\ manualGoing' = [manualGoing EXCEPT ![f] = FALSE]
    /\ UNCHANGED <<now, overlap, misfire, ver, schedOn, changes, lock, svcOn, lives, paused, choice, offs, epoch,
                   last, row, loading, run, starter, manuals, kills, adopted, turning, checked, fate, stopRows,
                   changedAt>>

\* The editor saves the flow with its schedule changed, or turned off (one transaction with the file): the
\* schedule's state and saved runs are forgotten (SCH-10); its runs going go on.
ChangeSchedule(f) ==
    /\ changes < MaxChanges /\ schedOn[f]
    /\ \/ /\ ver' = [ver EXCEPT ![f] = @ + 1] /\ UNCHANGED schedOn
          /\ last' = [last EXCEPT ![f] = IF EditorStartsSchedule THEN [v |-> ver[f] + 1, t |-> now]
                                                                 ELSE [v |-> -1, t |-> 0]]
       \/ /\ schedOn' = [schedOn EXCEPT ![f] = FALSE] /\ UNCHANGED ver
          /\ last' = [last EXCEPT ![f] = [v |-> -1, t |-> 0]]
    /\ changes' = changes + 1
    /\ changedAt' = [changedAt EXCEPT ![f] = now]
    /\ row' = [d \in Dues |-> IF F(d) = f THEN "none" ELSE row[d]]
    /\ fate' = [d \in Dues |-> IF F(d) = f /\ V(d) = ver[f] /\ fate[d] \in {"none", "saved"} THEN "dropped"
                               ELSE IF EditorStartsSchedule /\ F(d) = f /\ V(d) = ver'[f] /\ V(d) > ver[f]
                                       /\ T(d) <= now THEN "void"    \* the changed schedule starts after now
                               ELSE fate[d]]
    /\ loading' = {IF F(p.d) = f THEN [p EXCEPT !.chg = TRUE] ELSE p : p \in loading}
    /\ UNCHANGED <<now, overlap, misfire, lock, svcOn, lives, paused, choice, offs, epoch,
                   run, starter, manualGoing, manuals, kills, adopted, turning, checked, stopRows>>

\* Turn off (SCH-4): the dialog lists the flows with scheduled runs going, and Proceed writes the user's choice for
\* each (scheduler.paused). The dialog and Proceed are one step here: the list is the runs going at Proceed (a run
\* that begins while the dialog is open is of a flow not listed: its runs going finish, its waiting runs stay saved).
\* Cancel is not proceeding.
Proceed ==
    /\ ~paused /\ offs < MaxTurnOffs
    /\ LET listed == {f \in Flows : \E d \in Dues : F(d) = f /\ run[d] = "going"}
       IN \E c \in [listed -> {"finish", "stop"}] :
            LET ch == [f \in Flows |-> IF f \in listed THEN c[f] ELSE "none"]
                stopF == {f \in Flows : ch[f] = "stop"}
            IN /\ choice' = ch
               /\ loading' = {[p EXCEPT !.off = p.off \/ ~(row[p.d] \in WaitKinds /\ ch[F(p.d)] = "finish")]
                              : p \in loading}
               /\ stopRows' = stopRows \cup {<<d, svcOn>> : d \in {d2 \in Dues : F(d2) \in stopF /\ row[d2] # "none"}}
               /\ IF ProceedDropsStop
                  THEN /\ row' = [d \in Dues |-> IF F(d) \in stopF THEN "none" ELSE row[d]]
                       /\ fate' = [d \in Dues |-> IF F(d) \in stopF /\ row[d] # "none" THEN "dropped" ELSE fate[d]]
                  ELSE UNCHANGED <<row, fate>>
    /\ paused' = TRUE /\ offs' = offs + 1
    /\ epoch' = IF TermOnTurnOff THEN epoch + 1 ELSE epoch
    /\ UNCHANGED <<now, overlap, misfire, ver, schedOn, changes, lock, svcOn, lives, last, run, starter,
                   manualGoing, manuals, kills, adopted, turning, checked, changedAt>>

TurnOn ==
    /\ paused
    /\ paused' = FALSE /\ choice' = [f \in Flows |-> "none"]
    /\ epoch' = IF TermOnTurnOff THEN epoch + 1 ELSE epoch
    /\ UNCHANGED <<now, overlap, misfire, ver, schedOn, changes, lock, svcOn, lives, offs,
                   last, row, loading, run, starter, manualGoing, manuals, kills, adopted, turning, checked, fate,
                   stopRows, changedAt>>

Next ==
    \/ Tick
    \/ ServiceStart \/ ServiceCrash
    \/ Poll
    \/ \E f \in Flows : PlanFlow(f)
    \/ \E d \in Dues : StartNext(d)
    \/ \E p \in loading : Claim(p) \/ KillLoading(p)
    \/ \E d \in Dues : RunFinish(d) \/ KillRun(d)
    \/ \E f \in Flows : ManualBegin(f) \/ ManualEnd(f) \/ ChangeSchedule(f)
    \/ Proceed \/ TurnOn

Spec == Init /\ [][Next]_vars

Symmetry == Permutations(Flows)

-----------------------------------------------------------------------------
(***************************************************************************)
(* The promises. INVARIANT: true in every state. PROPERTY [][A]_vars: A    *)
(* holds of every step (primed = after the step).                          *)
(***************************************************************************)
Procs == [d : Dues, l : Lives, e : 0..(2 * MaxTurnOffs), off : BOOLEAN, chg : BOOLEAN, man : BOOLEAN]
TypeOK ==
    /\ now \in 0..MaxTime
    /\ overlap \in [Flows -> Overlaps] /\ misfire \in [Flows -> Misfires]
    /\ ver \in [Flows -> Versions] /\ schedOn \in [Flows -> BOOLEAN]
    /\ lock \in 0..MaxLives /\ svcOn \in 0..MaxLives /\ lives \in 0..MaxLives
    /\ paused \in BOOLEAN /\ choice \in [Flows -> {"none", "finish", "stop"}]
    /\ last \in [Flows -> [v : -1..MaxChanges, t : 0..MaxTime]]
    /\ row \in [Dues -> {"none", "time", "queue", "slot"}]
    /\ loading \subseteq Procs
    /\ run \in [Dues -> {"no", "going", "died", "over"}]
    /\ starter \in [Dues -> 0..MaxLives]
    /\ manualGoing \in [Flows -> BOOLEAN]
    /\ adopted \subseteq Dues /\ turning \in BOOLEAN /\ checked \in BOOLEAN
    /\ fate \in [Dues -> Fates]

\* SCH-1: one service at a time, and the service-on record names the one holding the lock.
OneTerm == lock # 0 => svcOn = lock

\* A step in which the scheduled run for d begins (its claim succeeds), and the process that claimed.
Begins(d) == run[d] # "going" /\ run'[d] = "going"
ClaimedBy(p) == p \in loading /\ p \notin loading' /\ Begins(p.d) /\ starter'[p.d] = p.l

\* SCH-6: each due time begins at most once.
AtMostOnce == \A d \in Dues : Begins(d) => run[d] = "no"

\* SCH-11 (strict, at the begin): when a scheduled run begins, no other run of its flow is going, scheduled or by
\* hand, unless its Overlap is allow.
OverlapAtBegin == \A d \in Dues : (Begins(d) /\ overlap[F(d)] # "allow") => ~OtherGoing(d)
\* SCH-11 (relaxed): the same, but a run by hand may go if it started while this run's process loaded (a
\* scheduled run "goes" from its process start: "the user's Run always starts").
OverlapAtBeginRelaxed ==
    \A p \in loading : (ClaimedBy(p) /\ overlap[F(p.d)] # "allow") =>
        /\ \A d2 \in Dues : (d2 # p.d /\ F(d2) = F(p.d)) => run[d2] # "going"
        /\ manualGoing[F(p.d)] => p.man

\* SCH-12: scheduled runs going never exceed Max concurrent runs.
LimitHeld == Cardinality({d \in Dues : run[d] = "going"}) <= Limit

\* SCH-8: a run never begins if, while it loaded, the service was turned off (unless it is a waiting run of a flow
\* chosen Let it finish), or its schedule changed or was turned off.
NoBeginAfterOffOrChange == \A p \in loading : ClaimedBy(p) => (~p.off /\ ~p.chg)

\* SCH-4: while turned off, only let-finish waiting runs begin; and the runs saved for a Stop-now flow at Proceed
\* never begin later under that service's term.
OffBegins == \A d \in Dues : (Begins(d) /\ paused) => (row[d] \in WaitKinds /\ choice[F(d)] = "finish")
StopRowsStay == \A d \in Dues : Begins(d) => <<d, starter'[d]>> \notin stopRows

\* SCH-16, SCH-9, SCH-10 (no loss): every due time that has come is begun, saved, told skipped or missed, dropped by
\* a user choice, void (a changed schedule's time before the change), or still to be handled by a check; never
\* silently gone. A saved run is a row; a row is always for a schedule that is on as it was (so it can begin).
ToHandle(d) == /\ V(d) = ver[F(d)] /\ schedOn[F(d)]
               /\ (last[F(d)].v = V(d) => last[F(d)].t < T(d))
NoLoss == \A d \in Dues :
    /\ (fate[d] = "saved") <=> (row[d] # "none")
    /\ (fate[d] = "begun") <=> (run[d] # "no")
    /\ fate[d] = "void" => (V(d) > 0 /\ V(d) = ver[F(d)])
    /\ (T(d) <= now /\ V(d) <= ver[F(d)] /\ fate[d] = "none") => ToHandle(d)
\* SCH-9 with SCH-10: a changed schedule starts afresh from the change; its times after the change are judged by
\* Misfire like any other (only its times up to the change are void).
ChangedTimesHandled == \A d \in Dues : fate[d] = "void" => T(d) <= changedAt[F(d)]
NoOrphanRows == \A d \in Dues : row[d] # "none" => (V(d) = ver[F(d)] /\ schedOn[F(d)])

\* Adoption: once the service on has checked, every scheduled run going is counted by it (its own, or adopted).
AdoptionCounted == (lock # 0 /\ checked) =>
                       \A d \in Dues : run[d] = "going" => (starter[d] = lock \/ d \in adopted)
\* ... and when a run begins, every other scheduled run going was counted by the service whose process it is.
CountedAtBegin == \A p \in loading : ClaimedBy(p) =>
                      \A d2 \in Dues : (d2 # p.d /\ run[d2] = "going") => (starter[d2] = p.l \/ d2 \in adopted)

SCH6  == [][AtMostOnce]_vars
SCH11 == [][OverlapAtBegin]_vars
SCH11Relaxed == [][OverlapAtBeginRelaxed]_vars
SCH8  == [][NoBeginAfterOffOrChange]_vars
SCH4  == [][OffBegins /\ StopRowsStay]_vars
Adoption == [][CountedAtBegin]_vars
=============================================================================
