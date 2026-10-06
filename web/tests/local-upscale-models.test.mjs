import test from "node:test";
import assert from "node:assert/strict";
import { source } from "./helpers.mjs";
const { MODELS, MODEL_PROFILES, modelProfile } = await source(
  "local-upscale/models.ts",
);
const { gpuAdapter } = await source("local-upscale/backend.ts");
const core = await source("local-upscale/tiles.ts");
test("Eight immutable selections preserve full IDs and default to Compact CPU", () => {
  assert.equal(MODELS.length, 8);
  assert.equal(MODELS[0].id, "local-compact");
  assert.deepEqual(
    MODELS.slice(-2).map((x) => x.id),
    ["local-real-esrgan", "local-real-esrgan-webgpu"],
  );
  assert.equal(new Set(MODEL_PROFILES.map((x) => x.sha256)).size, 4);
  assert.equal(new Set(MODELS.map((x) => x.id)).size, 8);
  for (const p of MODEL_PROFILES) {
    assert.match(p.sha256, /^[a-f0-9]{64}$/);
    assert.ok(p.size > 1_000_000);
    assert.ok(Object.isFrozen(p));
    assert.equal(MODELS.filter((x) => x.profile === p).length, 2);
  }
  assert.throws(() => modelProfile("unknown"), /Unknown/);
});
test("Nomos 2x tiles assemble every pixel without the 4x stride", () => {
  const p = modelProfile("nomos"),
    width = 131,
    height = 129;
  assert.equal(p.scale, 2);
  const target = new Uint8Array(width * height * p.scale * p.scale * 3).fill(
    99,
  );
  for (const tile of core.tiles(width, height, p.tile, p.overlap, p.prepad)) {
    const count =
      (tile.right - tile.left) * (tile.bottom - tile.top) * p.scale * p.scale;
    core.writeTile(
      target,
      width,
      tile,
      new Float32Array(count * 3).fill(0.5),
      p.scale,
    );
  }
  assert.ok(target.every((x) => x === 128));
});
test("Compact does not inherit the full model's GPU buffer requirement", async () => {
  const adapter = {
    limits: {
      maxBufferSize: 128 * 1024 ** 2,
      maxStorageBufferBindingSize: 128 * 1024 ** 2,
    },
  };
  const host = {
    isSecureContext: true,
    navigator: { gpu: { requestAdapter: async () => adapter } },
  };
  for (const p of MODEL_PROFILES.slice(0, 3))
    assert.equal(await gpuAdapter(host, p.gpu_buffer_bytes), adapter);
  await assert.rejects(
    gpuAdapter(host, modelProfile("full").gpu_buffer_bytes),
    /tile buffers/,
  );
});
