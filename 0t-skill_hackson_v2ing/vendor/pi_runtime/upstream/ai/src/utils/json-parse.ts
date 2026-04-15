import { parse as partialParse } from "partial-json";

/**
 * Attempts to parse potentially incomplete JSON during streaming.
 * Always returns a valid object, even if the JSON is incomplete.
 *
 * @param partialJson The partial JSON string from streaming
 * @returns Parsed object or empty object if parsing fails
 */
export function parseStreamingJson<T = any>(partialJson: string | undefined): T {
	if (!partialJson || partialJson.trim() === "") {
		throw new Error("parseStreamingJson: input is empty or undefined");
	}

	// Try standard parsing first (fastest for complete JSON)
	try {
		return JSON.parse(partialJson) as T;
	} catch {
		// Try partial-json for incomplete JSON
		try {
			const result = partialParse(partialJson);
			if (result === null || result === undefined) {
				throw new Error("parseStreamingJson: partial-json returned null/undefined");
			}
			return result as T;
		} catch (innerError) {
			throw new Error(
				`parseStreamingJson: all parsing attempts failed (input length=${partialJson.length}): ${innerError}`,
			);
		}
	}
}
