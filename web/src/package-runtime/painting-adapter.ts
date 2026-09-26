import { asset, manifest } from "./assets.js";
import { open } from "./lifecycle.js";
import { exclusive, idle } from "./queue.js";
import { boot, call, put, state } from "./session.js";
import type { Metadata, RuntimeManifest, SavedJob } from "./types.js";
export function validatePainting(file: File, m: RuntimeManifest) {
  return exclusive(async () => {
    idle();
    if (!file || file.size > 32 * 1024 ** 2)
      throw new Error("Choose one image no larger than 32 MiB.");
    await boot(m || (await manifest()), "painting");
    state.contextKey = null;
    await call("reset_inputs", {});
    await put("painting-input", file);
    return call("painting_inspect_image", { input: "painting-input" });
  });
}
export const openPainting = (record: SavedJob) =>
  exclusive(async () => {
    idle();
    return open(record);
  });
export const paintingPreview = (record: SavedJob) =>
  exclusive(async () => {
    idle();
    await open(record);
    return call("painting_compose", { job: record.parameters }, record);
  });
export const paintingImage = (record: SavedJob) =>
  exclusive(async () => {
    idle();
    await open(record);
    return call("painting_inspect_image", { input: "painting-input" }, record);
  });
export async function paintingReferences(m: RuntimeManifest) {
  const [definition, am, af] = await Promise.all(
    ["object-reference", "object-reference-am", "object-reference-af"].map(
      (n) => asset(m, n),
    ),
  );
  return {
    definition: JSON.parse(await definition.text()),
    am: await am.arrayBuffer(),
    af: await af.arrayBuffer(),
  };
}
export async function paintingThumbnail(m: RuntimeManifest, item: Metadata) {
  return URL.createObjectURL(await asset(m, item.thumbnail_asset));
}
