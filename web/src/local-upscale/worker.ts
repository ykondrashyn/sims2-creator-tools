import { decode, encode } from "fast-png";
import {
  checkDimensions,
  SCALE,
  tiles,
  tileInput,
  writeTile,
} from "./tiles.js";
import type * as Ort from "onnxruntime-web";
import { backendAssets, gpuAdapter, type LocalBackend } from "./backend.js";

interface Work {
  attempt: string;
  source: ArrayBuffer;
  model: ArrayBuffer;
  runtime: string;
  glue: string;
  wasm: ArrayBuffer;
  backend: LocalBackend;
}
let started = false;
self.onmessage = (event: MessageEvent<Work>) => {
  if (started) return;
  started = true;
  const { attempt } = event.data;
  const send = (
    value: Record<string, unknown>,
    transfer: Transferable[] = [],
  ) => self.postMessage({ attempt, ...value }, { transfer });
  void (async () => {
    let session: Ort.InferenceSession | undefined;
    let device:
      | { lost: Promise<{ message: string }>; destroy(): void }
      | undefined;
    let finishing = false;
    const startedAt = performance.now();
    const metrics: Record<string, number> = {};
    try {
      const backend = event.data.backend;
      backendAssets(backend);
      const adapter = backend === "webgpu" ? await gpuAdapter() : undefined;
      const input = new Uint8Array(event.data.source);
      // This is the Rust-prepared RGB PNG, never an arbitrary decoder input.
      if (
        input.length < 33 ||
        input.length > 128 * 1024 ** 2 ||
        input[0] !== 137 ||
        input[1] !== 80 ||
        input[25] !== 2 ||
        input[24] !== 8
      )
        throw new Error("The prepared input must be an RGB8 PNG.");
      const view = new DataView(input.buffer);
      const width = view.getUint32(16),
        height = view.getUint32(20);
      checkDimensions(width, height);
      const decoded = decode(input, { checkCrc: true });
      if (
        decoded.width !== width ||
        decoded.height !== height ||
        decoded.channels !== 3 ||
        decoded.depth !== 8
      )
        throw new Error("Invalid prepared image dimensions or channels.");
      send({ progress: "Loading the built-in Real-ESRGAN model…" });
      const ort: typeof Ort = await import(
        /* @vite-ignore */ event.data.runtime
      );
      ort.env.wasm.numThreads = 1;
      ort.env.wasm.proxy = false;
      ort.env.wasm.initTimeout = 30_000;
      ort.env.wasm.wasmPaths = { mjs: event.data.glue };
      ort.env.wasm.wasmBinary = event.data.wasm;
      if (adapter) ort.env.webgpu.adapter = adapter;
      const initAt = performance.now();
      session = await ort.InferenceSession.create(event.data.model, {
        executionProviders: [backend],
        ...(adapter
          ? { extra: { session: { disable_cpu_ep_fallback: "1" } } }
          : {}),
        graphOptimizationLevel: "all",
        enableCpuMemArena: false,
        enableMemPattern: false,
      });
      metrics.initialization_ms = performance.now() - initAt;
      if (adapter) {
        device = (await ort.env.webgpu.device) as NonNullable<typeof device>;
        void device!.lost.then((info) => {
          if (!finishing)
            send({
              error: `The WebGPU device was lost. Retry or choose the CPU option. ${info.message}`,
            });
        });
      }
      // Neither images nor tokens are fetched or stored by this worker.
      const output = new Uint8Array(width * height * SCALE * SCALE * 3);
      const regions = [...tiles(width, height)];
      let completed = 0;
      for (const tile of regions) {
        const w = tile.right - tile.left,
          h = tile.bottom - tile.top;
        const tensor = new ort.Tensor(
          "float32",
          tileInput(decoded.data as Uint8Array, width, height, tile),
          [1, 3, h, w],
        );
        let result: Ort.InferenceSession.OnnxValueMapType | undefined;
        try {
          const tileAt = performance.now();
          result = await session.run({ image: tensor });
          const tileMs = performance.now() - tileAt;
          if (!completed) metrics.first_tile_ms = tileMs;
          else metrics.warm_tiles_ms = (metrics.warm_tiles_ms || 0) + tileMs;
          if (
            result.upscaled.type !== "float32" ||
            result.upscaled.dims.join(",") !==
              [1, 3, h * SCALE, w * SCALE].join(",")
          )
            throw new Error("Unexpected local model output.");
          writeTile(output, width, tile, result.upscaled.data as Float32Array);
        } finally {
          tensor.dispose();
          if (result)
            for (const value of Object.values(result)) value.dispose();
        }
        completed++;
        send({
          progress: `Upscaling on ${backend === "webgpu" ? "GPU" : "CPU"}, tile ${completed} of ${regions.length}…`,
          completed,
          total: regions.length,
        });
      }
      finishing = true;
      await session.release();
      session = undefined;
      send({ progress: "Encoding the local result as PNG…" });
      const png = encode({
        width: width * SCALE,
        height: height * SCALE,
        channels: 3,
        depth: 8,
        data: output,
      });
      if (png.byteLength > 128 * 1024 ** 2)
        throw new Error("The output exceeds 128 MiB.");
      const bytes = new Uint8Array(png).buffer;
      metrics.tiles = regions.length;
      metrics.total_ms = performance.now() - startedAt;
      send({ result: bytes, backend, metrics }, [bytes]);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      send({
        error:
          event.data.backend === "webgpu"
            ? `WebGPU upscaling failed. Retry or choose Real-ESRGAN (built-in) for CPU processing. ${message}`
            : message,
      });
    } finally {
      finishing = true;
      await session?.release();
      device?.destroy();
    }
  })();
};
