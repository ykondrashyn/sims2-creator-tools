import type { Operation } from "../generated/contracts.js";
import { open } from "./lifecycle.js";
import { paintingReferences } from "./painting-adapter.js";
import { exclusive, idle } from "./queue.js";
import { call, failWorker, state } from "./session.js";
import * as store from "./store.js";
import type { Metadata, SavedJob } from "./types.js";
export const openSim = (record: SavedJob) =>
  exclusive(async () => {
    idle();
    return open(record);
  });
export const simPreview = (record: SavedJob) =>
  simOperation(record, "sim_preview");
export const simOperation = (
  record: SavedJob,
  op: Extract<Operation, `sim_${string}`>,
  extra: Metadata = {},
) =>
  exclusive(async () => {
    idle();
    const token = await store.acquire(record.id);
    const attempt = { id: record.id, token, cancelled: false };
    state.simAttempt = attempt;
    const heartbeat = setInterval(
      () => store.heartbeat(token).catch((e) => failWorker(e.message)),
      30000,
    );
    const timer = setTimeout(
      () =>
        failWorker("Fitting exceeded ten minutes. Your saved model is kept."),
      600000,
    );
    try {
      await open(record);
      if (attempt.cancelled)
        throw new Error("Fitting cancelled. Your model is saved.");
      const result = await call(
        op,
        {
          ...record.parameters,
          ...extra,
          model: record.files[0].name,
          reference: "sim-reference",
        },
        record,
      );
      if (attempt.cancelled)
        throw new Error("Fitting cancelled. Your model is saved.");
      await store.heartbeat(token);
      return result;
    } finally {
      if (state.simAttempt === attempt) state.simAttempt = null;
      clearInterval(heartbeat);
      clearTimeout(timer);
      await store.release(token);
    }
  });
export function cancelSimPreview() {
  if (state.simAttempt) state.simAttempt.cancelled = true;
  if ((state.simAttempt || state.engineMode === "sim") && !state.active)
    failWorker("Fitting cancelled. Your model is saved.");
}

export const simReferences = paintingReferences;
