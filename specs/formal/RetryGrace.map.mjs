// Chart labels for RetryGrace.
export const readerActions = [];

export const regionNames = ["What the block did", "The attempt"];

function did(v) {
	if (v.sawValue) return "Block returned a value";
	if (v.returned === "error") return "Block raised an error";
	if (v.returned === "stuck" || (v.phase === "failed" && !v.sawValue)) return "Block did not finish in time";
	return "Block in progress";
}

function progress(v) {
	if (v.phase === "running") return `attempt ${v.attempt} running`;
	if (v.phase === "grace" && v.returned === "value") return `attempt ${v.attempt} returned during the grace period`;
	if (v.phase === "grace") return `attempt ${v.attempt} in the grace period`;
	if (v.phase === "succeeded") return "kept as success";
	return "failed";
}

export function state(v) {
	return `${did(v)}: ${progress(v)}`;
}

export function group(v) {
	if (v.phase === "succeeded") return "Success kept";
	if (v.phase === "failed") return "Run failed";
	if (v.sawValue && v.phase !== "grace") return "Success discarded";
	return "Still trying";
}

export const groupColors = {
	"Still trying": "#d6eaf8",
	"Success kept": "#d5f5e3",
	"Success discarded": "#f5b7b1",
	"Run failed": "#fdebd0",
};

export function step(action, before, after) {
	if (action === "FinishWork" && after.returned === "value") return "the block returns a value during the grace period";
	if (action === "FinishWork" && after.returned === "error") return "the block raises an error";
	if (action === "FinishWork") return "the grace period ends with the block still going";
	if (action === "Decide" && before.returned === "value" && after.phase === "succeeded")
		return "the engine keeps that result";
	if (action === "Decide" && before.returned === "value") return "the engine throws that result away";
	if (action === "Decide" && after.phase === "failed") return "the last attempt fails";
	return "the engine tries again";
}

export function cause() {
	return "The block finished during the timeout grace and the engine threw that result away";
}

export function detail(v) {
	return `attempt ${v.attempt}, ${v.phase}, worker ${v.returned}, saw value ${v.sawValue}`;
}
