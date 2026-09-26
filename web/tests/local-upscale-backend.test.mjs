import test from "node:test";
import assert from "node:assert/strict";
import { source } from "./helpers.mjs";
const { gpuAdapter, backendAssets } = await source("local-upscale/backend.ts");
const adapter = {
  limits: { maxStorageBufferBindingSize: 1024 ** 3, maxBufferSize: 1024 ** 3 },
};
const host = (value) => ({
  isSecureContext: true,
  navigator: { gpu: { requestAdapter: async () => value } },
});

test("WebGPU rejects insecure contexts before touching the GPU", async () => {
  const context = host(adapter);
  context.isSecureContext = false;
  context.navigator.gpu.requestAdapter = () =>
    assert.fail("must not request adapter");
  await assert.rejects(gpuAdapter(context), /localhost.*trusted HTTPS/);
});
test("WebGPU requires hardware and sufficient per-buffer limits", async () => {
  await assert.rejects(
    gpuAdapter({ isSecureContext: true, navigator: {} }),
    /unavailable/,
  );
  await assert.rejects(gpuAdapter(host(null)), /No hardware/);
  await assert.rejects(
    gpuAdapter(host({ ...adapter, isFallbackAdapter: true })),
    /No hardware/,
  );
  await assert.rejects(
    gpuAdapter(host({ ...adapter, info: { isFallbackAdapter: true } })),
    /No hardware/,
  );
  await assert.rejects(
    gpuAdapter(
      host({
        limits: { maxBufferSize: 1024, maxStorageBufferBindingSize: 1024 },
      }),
    ),
    /tile buffers/,
  );
  assert.equal(await gpuAdapter(host(adapter)), adapter);
});
test("Backend routing selects disjoint runtime assets and rejects unknown backends", () => {
  assert.deepEqual(backendAssets("wasm"), [
    "local-upscale-runtime",
    "local-upscale-glue",
    "local-upscale-wasm",
  ]);
  assert.deepEqual(backendAssets("webgpu"), [
    "local-upscale-webgpu-runtime",
    "local-upscale-webgpu-glue",
    "local-upscale-webgpu-wasm",
  ]);
  assert.throws(() => backendAssets("auto"), /Unknown/);
});
