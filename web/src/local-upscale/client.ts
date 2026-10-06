import { asset, manifest } from "../package-runtime/assets.js";
import { acquire, heartbeat, release, id } from "../package-runtime/store.js";
import { backendAssets, gpuAdapter, type LocalBackend } from "./backend.js";

import { modelProfile, type ModelProfile } from "./models.js";

export interface LocalProgress {
  message: string;
  completed?: number;
  total?: number;
}

/** Session-only inference. Shared immutable assets use the verified asset cache. */
export async function upscaleLocally(
  source: Blob,
  signal: AbortSignal,
  progress: (value: LocalProgress) => void,
  backend: LocalBackend = "wasm",
  profile: Readonly<ModelProfile> = modelProfile("full"),
): Promise<{
  png: Blob;
  version: string;
  backend: LocalBackend;
  metrics: Record<string, number>;
}> {
  signal.throwIfAborted();
  const assets = backendAssets(backend);
  const lease = await acquire("local-upscale-" + id());
  let worker: Worker | undefined;
  const urls: string[] = [];
  let rejectStopped!: (reason: Error) => void;
  const stopped = new Promise<never>((_, reject) => {
    rejectStopped = reject;
  });
  void stopped.catch(() => {});
  const wait = <T>(value: Promise<T>) => Promise.race([value, stopped]);
  const stop = (error: Error) => {
    worker?.terminate();
    rejectStopped(error);
  };
  const abort = () =>
    stop(new DOMException("Local upscaling cancelled.", "AbortError"));
  signal.addEventListener("abort", abort, { once: true });
  if (signal.aborted) abort();
  const beat = setInterval(() => {
    void heartbeat(lease).catch(stop);
  }, 20_000);
  const objectUrl = (blob: Blob) => {
    const value = URL.createObjectURL(blob);
    urls.push(value);
    return value;
  };
  try {
    if (backend === "webgpu")
      await wait(gpuAdapter(undefined, profile.gpu_buffer_bytes));
    signal.throwIfAborted();
    progress({
      message: "Loading local model assets…",
    });
    const m = await wait(manifest());
    const expected = m.assets[profile.asset];
    if (
      !expected ||
      expected.sha256 !== profile.sha256 ||
      expected.size !== profile.size
    )
      throw new Error(
        "The selected model does not match this release. Reload the page.",
      );
    const names = ["local-upscale-worker", ...assets, profile.asset];
    const [code, _runtime, _glue, wasm, model] = await wait(
      Promise.all(names.map((name) => asset(m, name))),
    );
    const [imageBytes, wasmBytes, modelBytes] = await wait(
      Promise.all([
        source.arrayBuffer(),
        wasm.arrayBuffer(),
        model.arrayBuffer(),
      ]),
    );
    signal.throwIfAborted();
    worker = new Worker(
      objectUrl(new Blob([code], { type: "text/javascript" })),
      { type: "module" },
    );
    const finished = new Promise<{
      png: Blob;
      metrics: Record<string, number>;
    }>((resolve, reject) => {
      worker!.onerror = () =>
        reject(
          new Error(
            "The local model stopped. Try a smaller image or close other busy tabs.",
          ),
        );
      worker!.onmessage = ({ data }) => {
        if (signal.aborted || data.attempt !== lease) return;
        if (data.error) reject(new Error(data.error));
        else if (data.result)
          resolve({
            png: new Blob([data.result], { type: "image/png" }),
            metrics: data.metrics,
          });
        else if (data.progress)
          progress({
            message: data.progress,
            completed: data.completed,
            total: data.total,
          });
      };
    });
    worker.postMessage(
      {
        attempt: lease,
        backend,
        modelId: profile.id,
        source: imageBytes,
        model: modelBytes,
        wasm: wasmBytes,
        // ORT resolves paths relative to import.meta.url. Its verified immutable
        // modules need HTTP URLs because blob URLs have no relative base path.
        runtime: new URL(m.assets[assets[0]].url, location.href).href,
        glue: new URL(m.assets[assets[1]].url, location.href).href,
      },
      [imageBytes, modelBytes, wasmBytes],
    );
    const result = await wait(finished);
    signal.throwIfAborted();
    return {
      ...result,
      backend,
      version: m.assets[profile.asset].sha256,
    };
  } finally {
    worker?.terminate();
    clearInterval(beat);
    signal.removeEventListener("abort", abort);
    urls.forEach((value) => URL.revokeObjectURL(value));
    await release(lease);
  }
}
