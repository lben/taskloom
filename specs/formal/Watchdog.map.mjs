// Chart labels for Watchdog.
export const readerActions = [];

export const regionNames = ["When the keeper looks", "The server scheduler"];

const WHERE = {
	down: "not running",
	up: "checking",
	hung: "hung",
};

export function state(v) {
	const when = v.phase === "before" ? "Before the keeper looks" : "After the keeper looks";
	const told = v.told ? ", and you were told why" : "";
	return `${when}: scheduler is ${WHERE[v.remote]}${told}`;
}

export function group(v) {
	if (v.phase === "before") return "Not checked yet";
	if (v.remote === "up") return "Scheduler is checking";
	return "Scheduler left hung";
}

export const groupColors = {
	"Not checked yet": "#d6eaf8",
	"Scheduler is checking": "#d5f5e3",
	"Scheduler left hung": "#f5b7b1",
};

export function step(_action, before, after) {
	if (before.remote === "hung" && after.remote === "up") return "the keeper stops the hung scheduler and starts a new one";
	if (before.remote === "hung") return "the keeper tries to start another scheduler";
	if (before.remote === "down") return "the keeper starts the scheduler";
	return "the keeper leaves the scheduler alone";
}

export function cause() {
	return "The hung scheduler still holds the folder lock, so the new one cannot start";
}

export function detail(v) {
	return `${v.phase}, remote ${v.remote}, told ${v.told}`;
}
