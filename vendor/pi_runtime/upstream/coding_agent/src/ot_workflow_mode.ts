import { Payload } from "./ot_workflow_kernel_types.js";
import { runWorkflowKernel } from "./ot_workflow_kernel.js";

export async function runWorkflowMode(payload: Payload) {
	return runWorkflowKernel(payload);
}
