// Chart labels for OverlapSkip. 6:31 is already running. 6:32 is the only new minute.
export const readerActions = [];

const HOW = { due: "still due", running: "running", skipped: "skipped", done: "finished" };

export function state(v) {
	const lead = v.phase === "before" ? "Before the check" : "After the check";
	return `${lead}: 6:31 ${HOW[v.going]}, 6:32 ${HOW[v.minute]}`;
}

export function group(v) {
	if (v.minute === "skipped") return "Overlap skip";
	if (v.phase === "before") return "Waiting to check";
	return "The run finishes";
}

export const groupColors = {
	"Waiting to check": "#d6eaf8",
	"Overlap skip": "#d5f5e3",
	"The run finishes": "#d6eaf8",
};

export function step(action) {
	if (action === "Check") return "the check skips the one new minute";
	return "the running job finishes";
}

export function detail(v) {
	return `${v.phase}: 6:31 ${v.going}, 6:32 ${v.minute}`;
}
