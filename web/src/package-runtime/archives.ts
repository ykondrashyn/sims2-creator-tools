import { asset } from "./assets.js";
import * as store from "./store.js";
import type { Metadata, SavedJob } from "./types.js";
export async function archive(
  record: SavedJob,
  entries: Metadata[],
  token: string,
  isActive: () => boolean,
) {
  if (!record.manifest.assets.archive)
    throw new Error(
      "This saved batch uses an unsupported archive format. Its inputs and checkpoints have been kept. Start a new batch to build it.",
    );
  const source = await asset(record.manifest, "archive");
  const url = URL.createObjectURL(
    new Blob([source], { type: "text/javascript" }),
  );
  try {
    const module = await import(url);
    return await module.archive(record, entries, token, {
      getBlob: store.getBlob,
      putBlob: store.putBlob,
      deleteBlob: store.deleteBlob,
      checkActive: () => {
        if (!isActive())
          throw new Error(
            "Archive creation was cancelled. Resume the saved batch.",
          );
      },
    });
  } finally {
    URL.revokeObjectURL(url);
  }
}
