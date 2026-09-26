import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { pathToFileURL } from "node:url";
import { encode } from "fast-png";
import { source } from "./helpers.mjs";

for (const failure of ["initialization", "device-loss", "none"])
  test(`WebGPU worker handles ${failure} without using the CPU provider`, async () => {
    const saved = Object.fromEntries(
      ["self", "navigator", "isSecureContext", "testOrt"].map((name) => [
        name,
        Object.getOwnPropertyDescriptor(globalThis, name),
      ]),
    );
    let lose;
    let destroyed = false;
    const device = {
      lost: new Promise((resolve) => (lose = resolve)),
      destroy() {
        destroyed = true;
      },
    };
    const calls = [];
    let done;
    const messages = [];
    const finished = new Promise((resolve) => (done = resolve));
    const host = {
      postMessage(data) {
        messages.push(data);
        if (data.error || data.result) done();
      },
    };
    const harness = {
      env: { wasm: {}, webgpu: { device } },
      InferenceSession: {
        async create(_model, options) {
          calls.push(options);
          if (failure === "initialization")
            throw new Error("GPU initialization rejected");
          return {
            async run({ image }) {
              if (failure === "device-loss") {
                lose({ message: "Test GPU disconnected" });
                throw new Error("Device lost during inference");
              }
              const h = image.dims[2] * 4,
                w = image.dims[3] * 4;
              return {
                upscaled: {
                  type: "float32",
                  dims: [1, 3, h, w],
                  data: new Float32Array(h * w * 3),
                  dispose() {},
                },
              };
            },
            async release() {},
          };
        },
      },
      Tensor: class {
        constructor(type, data, dims) {
          Object.assign(this, { type, data, dims });
        }
        dispose() {}
      },
    };
    try {
      for (const [name, value] of Object.entries({
        self: host,
        isSecureContext: true,
        testOrt: harness,
        navigator: {
          gpu: {
            requestAdapter: async () => ({
              limits: {
                maxBufferSize: 1024 ** 3,
                maxStorageBufferBindingSize: 1024 ** 3,
              },
            }),
          },
        },
      }))
        Object.defineProperty(globalThis, name, { configurable: true, value });
      const directory = await mkdtemp(tmpdir() + "/ts2-gpu-worker-");
      const runtime = directory + "/ort.mjs";
      await writeFile(
        runtime,
        "export const {env,InferenceSession,Tensor}=globalThis.testOrt;",
      );
      await source("local-upscale/worker.ts");
      const png = encode({
        width: 1,
        height: 1,
        channels: 3,
        depth: 8,
        data: new Uint8Array([1, 2, 3]),
      });
      host.onmessage({
        data: {
          attempt: "test",
          backend: "webgpu",
          source: new Uint8Array(png).buffer,
          model: new ArrayBuffer(0),
          runtime: pathToFileURL(runtime).href,
        },
      });
      await finished;
      await new Promise((resolve) => setImmediate(resolve));
      assert.equal(calls.length, 1);
      assert.deepEqual(calls[0].executionProviders, ["webgpu"]);
      assert.equal(calls[0].extra.session.disable_cpu_ep_fallback, "1");
      if (failure === "none") {
        assert.ok(
          messages.some((value) => value.result && value.backend === "webgpu"),
        );
      } else {
        assert.ok(messages.some((value) => value.error?.includes("CPU")));
        assert.ok(!messages.some((value) => value.result));
      }
      if (failure !== "initialization") assert.ok(destroyed);
    } finally {
      for (const [name, descriptor] of Object.entries(saved)) {
        if (descriptor) Object.defineProperty(globalThis, name, descriptor);
        else delete globalThis[name];
      }
    }
  });
