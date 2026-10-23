// Chart labels for BlockRun. The chart uses Mutant = FALSE.
export const readerActions = [];

function attempt(v) {
	if (v.attempt === 0 && v.phase === "idle") return "waiting to start";
	if (v.attempt === 0 && v.phase === "cancelled") return "cancelled before it started";
	if (v.phase === "running") return `attempt ${v.attempt} running`;
	if (v.phase === "succeeded") return `attempt ${v.attempt} succeeded`;
	if (v.phase === "failed") return `attempt ${v.attempt} failed`;
	return `attempt ${v.attempt} cancelled`;
}

export function state(v) {
	const lead = v.phase === "idle" || v.phase === "running" ? "In progress" : "Settled";
	return `${lead}: ${attempt(v)}`;
}

export function group(v) {
	if (v.phase === "succeeded") return "Succeeded";
	if (v.phase === "failed") return "Failed";
	if (v.phase === "cancelled") return "Cancelled";
	return "In progress";
}

export const groupColors = {
	"In progress": "#d6eaf8",
	Succeeded: "#d5f5e3",
	Failed: "#fdebd0",
	Cancelled: "#e8daef",
};

export function step(action, _before, after) {
	const steps = {
		Start: "the block starts",
		Succeed: "the attempt succeeds",
		Retry: `the attempt fails and try ${after.attempt} starts`,
		GiveUp: "the last attempt fails",
		Cancel: "the run is cancelled",
	};
	return steps[action] ?? action;
}

export function detail(v) {
	return `${v.phase}, attempt ${v.attempt}`;
}
