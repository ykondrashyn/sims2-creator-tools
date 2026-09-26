import test from "node:test";
import assert from "node:assert/strict";
import { source } from "./helpers.mjs";
const api = await source("upscale-download.ts");

test("Filenames use inspected formats and remove path separators", () => {
  assert.equal(
    api.outputFilename(
      "hair.png",
      "local-real-esrgan",
      api.outputExtension("WebP"),
    ),
    "hair_local-real-esrgan_upscaled.webp",
  );
  assert.equal(
    api.outputFilename("../my texture.jpeg", "local-real-esrgan-webgpu", "png"),
    "my_texture_local-real-esrgan-webgpu_upscaled.png",
  );
  assert.throws(() => api.outputExtension("Gif"));
});
