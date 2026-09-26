import { asset, manifest } from "./package-runtime/assets.js";
import { acquire, heartbeat, release, id } from "./package-runtime/store.js";
import type { Metadata } from "./package-runtime/types.js";

export interface ImageInfo {
  width: number;
  height: number;
  format: "Png" | "Jpeg" | "WebP";
  alpha: boolean;
  alpha_restored?: boolean;
  alpha_error?: string;
  output_bytes: number;
  profile: string;
}

/** Disposable worker. Only immutable runtime assets may enter the shared cache. */
export async function processImage(
  input: Blob,
  options: { output?: Blob; preserve?: boolean; png?: boolean } = {},
  signal?: AbortSignal,
): Promise<{ info: ImageInfo; png: Blob | null }> {
  signal?.throwIfAborted();
  const lease = await acquire("upscale-session-" + id());
  let worker: Worker | undefined;
  const urls: string[] = [];
  const pending = new Map<
    number,
    { resolve: (result: Metadata) => void; reject: (reason: Error) => void }
  >();
  let serial = 0;
  let failure: Error | undefined;
  let rejectStopped!: (reason: Error) => void;
  const stopped = new Promise<never>((_, reject) => {
    rejectStopped = reject;
  });
  // Shared immutable downloads may continue for other tools. This session must
  // stop waiting and release its lease even before a worker has been created.
  void stopped.catch(() => {});
  const wait = <T>(promise: Promise<T>) => Promise.race([promise, stopped]);
  const stop = (reason: Error) => {
    failure = reason;
    rejectStopped(reason);
    worker?.terminate();
    for (const call of pending.values()) call.reject(reason);
    pending.clear();
  };
  const abort = () =>
    stop(new DOMException("Image processing cancelled.", "AbortError"));
  signal?.addEventListener("abort", abort, { once: true });
  if (signal?.aborted) abort();
  const beat = setInterval(() => {
    void heartbeat(lease).catch((error: Error) => stop(error));
  }, 20_000);
  try {
    const m = await wait(manifest());
    const [workerBlob, glueBlob, wasmBlob] = await wait(
      Promise.all(["worker", "glue", "wasm"].map((name) => asset(m, name))),
    );
    signal?.throwIfAborted();
    if (failure) throw failure;
    const workerUrl = URL.createObjectURL(
      new Blob([workerBlob], { type: "text/javascript" }),
    );
    const glueUrl = URL.createObjectURL(
      new Blob([glueBlob], { type: "text/javascript" }),
    );
    urls.push(workerUrl, glueUrl);
    worker = new Worker(workerUrl, { type: "module" });
    worker.onerror = () =>
      stop(
        new Error(
          "The image worker stopped. Try a smaller image or close other busy tabs.",
        ),
      );
    worker.onmessage = ({ data }) => {
      if (data.attempt !== lease || data.job !== "upscale") return;
      const call = pending.get(data.id);
      if (!call) return;
      pending.delete(data.id);
      if (data.error) call.reject(new Error(data.error.message));
      else call.resolve(data.result);
    };
    const rpc = (
      op: string,
      extra: Metadata = {},
      transfer: Transferable[] = [],
    ): Promise<Metadata> => {
      signal?.throwIfAborted();
      if (failure) throw failure;
      const callId = ++serial;
      return new Promise((resolve, reject) => {
        pending.set(callId, { resolve, reject });
        worker!.postMessage(
          {
            version: 1,
            id: callId,
            job: "upscale",
            revision: 1,
            attempt: lease,
            op,
            ...extra,
          },
          transfer,
        );
      });
    };
    const wasm = await wait(wasmBlob.arrayBuffer());
    await rpc("boot", { glue: glueUrl, wasm }, [wasm]);
    const source = await wait(input.arrayBuffer());
    await rpc("put", { name: "source", bytes: source }, [source]);
    let info = await rpc("upscale_prepare_input", {
      params: { input: "source", upload: !options.output },
    });
    if (options.output) {
      const bytes = await wait(options.output.arrayBuffer());
      await rpc("put", { name: "result", bytes }, [bytes]);
      info = await rpc("upscale_inspect_output", {
        params: { input: "result", preserve_alpha: options.preserve !== false },
      });
      if (options.png && !info.output_bytes) {
        const encoded = await rpc("upscale_png", { params: {} });
        info.output_bytes = encoded.output_bytes;
      }
    }
    const output = info.output_bytes
      ? await rpc("take", { name: "output" })
      : null;
    signal?.throwIfAborted();
    if (failure) throw failure;
    return {
      info: info as ImageInfo,
      png: output ? new Blob([output.bytes], { type: "image/png" }) : null,
    };
  } finally {
    worker?.terminate();
    clearInterval(beat);
    signal?.removeEventListener("abort", abort);
    urls.forEach((url) => URL.revokeObjectURL(url));
    await release(lease);
  }
}
