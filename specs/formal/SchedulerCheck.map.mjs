// Chart labels for SchedulerCheck. v.up is a boolean. Times are 6:31, 6:32, and 6:33.
export const readerActions = [];

export const regionNames = ["Misfire", "The three minutes"];

const POLICY = { run_once: "Run once", skip: "Skip missed runs", catch_up: "Catch up" };
const HOW = {
	due: "still due",
	running: "running",
	queued: "waiting its turn",
	missed: "counted as missed",
	skipped: "skipped",
	done: "finished",
	dropped: "dropped",
};

function minutes(v) {
	const body = `6:31 ${HOW[v.older]}, 6:32 ${HOW[v.previous]}, 6:33 ${HOW[v.onTime]}`;
	if (v.phase === "before") return v.up ? "waiting for the check" : "scheduler is off";
	return v.up ? body : `${body}, scheduler is off`;
}

export function state(v) {
	return `${POLICY[v.policy]}: ${minutes(v)}`;
}

export function group(v) {
	const slots = [v.older, v.previous, v.onTime];
	if (v.phase === "before") return "Waiting to check";
	if (slots.includes("dropped")) return "A run was dropped";
	if (slots.includes("skipped")) return "A run was skipped";
	return "Every chosen run is kept";
}

export const groupColors = {
	"Waiting to check": "#d6eaf8",
	"Every chosen run is kept": "#d5f5e3",
	"A run was skipped": "#fdebd0",
	"A run was dropped": "#f5b7b1",
};

const STEPS = {
	Check: "the scheduler checks",
	Finish: "the running job finishes",
	Stop: "the scheduler stops",
	Resume: "the scheduler starts again",
	Drain: "a waiting job starts",
};

export function step(action, before, after) {
	if (action === "Check" && after.onTime === "skipped") return "the check skips 6:33";
	if (action === "Check") return "the check keeps 6:33";
	if (action === "Stop" && [after.older, after.previous, after.onTime].includes("dropped"))
		return "the scheduler stops and drops a waiting run";
	return STEPS[action] ?? action;
}

export function cause(_v, _facts, invariant) {
	const causes = {
		RunOnceKeepsTheNewest:
			"The check a few seconds early starts 6:32 and skips 6:33, so run once loses the newest minute",
		SkipKeepsTheOnTimeRun:
			"The check a few seconds early starts 6:32 and skips 6:33, the minute that was on time",
		SkipCountsTheMissedRuns:
			"The early check runs 6:32 and counts only 6:31 as missed, so one missed minute is never counted",
		CatchUpKeepsEveryTime:
			"Catch-up starts 6:31 and queues 6:32, then skips 6:33 because that minute is still a few seconds away",
		QueuedWorkIsNotDropped:
			"The scheduler stops while a catch-up run is only in memory, and that run is gone",
	};
	return causes[invariant] ?? null;
}

export function detail(v) {
	const on = v.up ? "up" : "off";
	return `${v.policy}, ${v.phase}, scheduler ${on}: 6:31 ${v.older}, 6:32 ${v.previous}, 6:33 ${v.onTime}`;
}
