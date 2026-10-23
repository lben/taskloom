// Chart labels for CatchUpBusy. 6:31 is already running. 6:32 and 6:33 are the new minutes.
export const readerActions = [];

export const regionNames = ["The run already going", "The two new minutes"];

const HOW = {
	due: "still due",
	running: "running",
	queued: "waiting its turn",
	skipped: "skipped",
	done: "finished",
};

export function state(v) {
	const lead = v.phase === "before" ? "Before the check" : "After the check";
	return `${lead}: 6:31 ${HOW[v.older]}, 6:32 ${HOW[v.previous]}, 6:33 ${HOW[v.onTime]}`;
}

export function group(v) {
	if (v.phase === "before") return "Waiting to check";
	if (v.previous === "skipped" || v.onTime === "skipped") return "A minute was skipped";
	return "Both new minutes are kept";
}

export const groupColors = {
	"Waiting to check": "#d6eaf8",
	"Both new minutes are kept": "#d5f5e3",
	"A minute was skipped": "#fdebd0",
};

export function step(action, _before, after) {
	if (action === "Check" && after.previous === "skipped") return "the check skips 6:32";
	if (action === "Check") return "the check queues 6:32 and 6:33";
	return "the running job finishes";
}

export function cause() {
	return "6:31 is still running, so catch-up skips 6:32 and only queues 6:33";
}

export function detail(v) {
	return `${v.phase}: 6:31 ${v.older}, 6:32 ${v.previous}, 6:33 ${v.onTime}`;
}
