import {
  engine_request,
  saved_record,
  worker_response,
} from "../generated/validators.mjs";
import { RuntimeError } from "./errors.js";
import type { SavedJob } from "./types.js";
const hash = /^[a-f0-9]{64}$/;
/** Inspect without reserializing. In particular, never rewrite a snapshot. */
export function validateSavedJob(value: unknown): asserts value is SavedJob {
  if (!saved_record(value))
    throw new RuntimeError(
      "This saved batch is unsupported or damaged. It has been kept. Start a new batch from its original input.",
      "saved_record_recovery",
    );
  const record = value as SavedJob;
  if (
    record.schema_version !== 1 ||
    record.manifest?.protocol_version !== 1 ||
    !hash.test(record.manifest?.release || "")
  )
    throw new RuntimeError(
      "This saved batch uses an unsupported runtime. Its inputs and output have been kept.",
      "saved_runtime_recovery",
    );
  for (const file of record.files || [])
    if (
      !hash.test(file.sha256) ||
      !Number.isSafeInteger(file.size) ||
      file.size < 0
    )
      throw new RuntimeError(
        "A saved input descriptor is damaged. Reopen the original input in a new batch.",
        "saved_input_recovery",
      );
}
export function validateResponse(value: unknown): void {
  if (!worker_response(value))
    throw new RuntimeError(
      "The worker returned an unsupported response. Saved work is kept. Reload and resume.",
      "worker_protocol",
    );
}
export function validateRequest(value: unknown): void {
  if (!engine_request(value))
    throw new RuntimeError(
      "The selected engine does not support this operation. Saved work is kept.",
      "worker_protocol",
    );
}
