import type { Layout, ObjectPreviewResult } from "../shared/scene-types.js";
import { asset } from "./assets.js";
import { open } from "./lifecycle.js";
import { exclusive, idle } from "./queue.js";
import { call } from "./session.js";
import type { RuntimeManifest, SavedJob } from "./types.js";
export const openObject = (record: SavedJob) =>
  exclusive(async () => {
    idle();
    return open({ ...record, kind: "object" });
  });
export const objectPreview = (record: SavedJob) =>
  exclusive(async () => {
    idle();
    await open(record);
    return call(
      "object_preview",
      { job: { ...record.parameters, id: record.id } },
      record,
    ) as Promise<ObjectPreviewResult>;
  });

export const objectLayout = (record: SavedJob) =>
  exclusive(async () => {
    idle();
    await open(record);
    return call(
      "object_layout",
      { job: { ...record.parameters, id: record.id } },
      record,
    ) as Promise<Layout>;
  });
export async function objectReferences(m: RuntimeManifest) {
  const [definition, am, af, table] = await Promise.all(
    [
      "object-reference",
      "object-reference-am",
      "object-reference-af",
      "object-reference-table",
    ].map((n) => asset(m, n)),
  );
  return {
    definition: JSON.parse(await definition.text()),
    am: await am.arrayBuffer(),
    af: await af.arrayBuffer(),
    table: await table.arrayBuffer(),
  };
}
