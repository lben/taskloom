#!/bin/sh
# Re-run the Taskloom concurrency checks. TLC must be tla2tools.jar (TLA+ 1.7 / TLC 2.19
# was used for the report). The statechart script is poteto's pstack statechart skill.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/../.." && pwd)
cd "$ROOT"
: "${TLA2TOOLS:?set TLA2TOOLS to the path of tla2tools.jar}"
: "${STATECHART:?set STATECHART to statechart.mjs from the pstack statechart skill}"
export TLA2TOOLS
JAVA=${JAVA:-java}
export JAVA

draw() {
  # shellcheck disable=SC2068
  node "$STATECHART" from-spec "$@"
}

draw specs/formal/SchedulerCheck.tla \
  --fixed specs/formal/SchedulerCheckFixed.tla \
  --expect-broken RunOnceKeepsTheNewest,SkipKeepsTheOnTimeRun,SkipCountsTheMissedRuns,CatchUpKeepsEveryTime,QueuedWorkIsNotDropped \
  --title "Scheduler check in the early window" \
  --out docs/statecharts/scheduler-check \
  --png

draw specs/formal/CatchUpBusy.tla \
  --fixed specs/formal/CatchUpBusyFixed.tla \
  --expect-broken CatchUpDoesNotSkipTheOldest \
  --title "Catch-up while a run is still going" \
  --out docs/statecharts/catch-up-busy \
  --png

draw specs/formal/OverlapSkip.tla \
  --title "One new minute while a run is still going" \
  --out docs/statecharts/overlap-skip \
  --png

draw specs/formal/RetryGrace.tla \
  --fixed specs/formal/RetryGraceFixed.tla \
  --expect-broken SuccessSticks \
  --title "A block that finishes during the timeout grace" \
  --out docs/statecharts/retry-grace \
  --png

draw specs/formal/Watchdog.tla \
  --fixed specs/formal/WatchdogFixed.tla \
  --expect-broken AHungSchedulerIsReplaced \
  --title "The keeper and a server scheduler" \
  --out docs/statecharts/watchdog \
  --png

draw specs/formal/BlockRun.tla \
  --title "A block attempt" \
  --out docs/statecharts/block-run \
  --png

mkdir -p specs/formal/traces
if "$JAVA" -jar "$TLA2TOOLS" -deadlock -workers 1 -metadir /tmp/tlc-meta-BlockRunMutant \
  -config specs/formal/BlockRunMutant.cfg specs/formal/BlockRun.tla \
  > specs/formal/traces/BlockRunMutant.out 2>&1
then
  echo "BlockRunMutant passed; AttemptsEnd was not shown to fail" >&2
  exit 1
fi
grep -q "Invariant AttemptsEnd is violated" specs/formal/traces/BlockRunMutant.out
echo "BlockRunMutant broke AttemptsEnd, as intended."
