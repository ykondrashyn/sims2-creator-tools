import type {
  ConversionProgress,
  ConversionStep,
  Operation,
} from "../generated/contracts.js";
import { asset } from "./assets.js";
import { RuntimeError } from "./errors.js";
import type {
  Attempt,
  Context,
  Metadata,
  PendingCall,
  RuntimeManifest,
} from "./types.js";
import { validateResponse } from "./validation.js";
export const state: {
  worker: Worker | null;
  release: string | null;
  serial: number;
  active: Attempt | null;
  simAttempt: Attempt | null;
  engineReady: Promise<Metadata> | null;
  engineMode: string | null;
  contextKey: string | null;
  contextInfo: Metadata | null;
  metrics: { peak_heap_bytes: number; worker_ms: number };
} = {
  worker: null,
  release: null,
  serial: 0,
  active: null,
  simAttempt: null,
  engineReady: null,
  engineMode: null,
  contextKey: null,
  contextInfo: null,
  metrics: { peak_heap_bytes: 0, worker_ms: 0 },
};
const pending = new Map<number, PendingCall>();
export function failWorker(message: string) {
  state.worker?.terminate();
  state.worker = null;
  state.release = null;
  state.engineReady = null;
  state.contextKey = null;
  state.contextInfo = null;
  for (const { reject } of pending.values()) reject(new Error(message));
  pending.clear();
}
export async function rpc(
  op: string,
  extra: Metadata = {},
  context: Context = {},
  transfer?: Transferable[],
) {
  if (!state.worker)
    throw new Error("Package worker is unavailable. Resume your saved batch.");
  const request = {
    version: 1,
    id: ++state.serial,
    job: context.id || "",
    revision: context.revision || 0,
    attempt:
      state.active && context.id === state.active.id
        ? state.active.token
        : state.simAttempt && context.id === state.simAttempt.id
          ? state.simAttempt.token
          : "",
    op,
    ...extra,
  };
  return new Promise<Metadata>((resolve, reject) => {
    pending.set(request.id, {
      resolve,
      reject,
      job: request.job,
      revision: request.revision,
      attempt: request.attempt,
    });
    try {
      state.worker!.postMessage(
        request,
        transfer ?? (extra.bytes ? [extra.bytes] : []),
      );
    } catch (error) {
      pending.delete(request.id);
      reject(error);
    }
  });
}
export async function boot(m: RuntimeManifest, kind = "package") {
  const mode = ["conversion", "painting", "sim"].includes(kind)
    ? kind
    : "package";
  if (state.worker && state.release === m.release && state.engineMode === mode)
    return state.engineReady;
  failWorker("Package engine changed.");
  if (!globalThis.WebAssembly || !globalThis.Worker || !globalThis.indexedDB)
    throw new Error(
      "Package creation needs WebAssembly, browser workers and local storage. Use a current browser with site storage enabled.",
    );
  const [w, g, b] = await Promise.all(
    ["worker", "glue", "wasm"].map((n) => asset(m, n)),
  );
  const urls = [
    URL.createObjectURL(new Blob([w], { type: "text/javascript" })),
    URL.createObjectURL(new Blob([g], { type: "text/javascript" })),
  ];
  state.worker = new Worker(urls[0], { type: "module" });
  state.release = m.release;
  state.engineMode = mode;
  state.worker.onmessage = ({ data: r }) => {
    const responseCall = pending.get(r.id);
    if (
      !responseCall ||
      r.job !== responseCall.job ||
      r.revision !== responseCall.revision
    )
      return;
    if (r.attempt !== undefined && r.attempt !== responseCall.attempt) return;
    try {
      validateResponse({ ...r, attempt: r.attempt ?? responseCall.attempt });
    } catch (error) {
      pending.delete(r.id);
      responseCall.reject(RuntimeError.from(error));
      return;
    }
    pending.delete(r.id);
    if (r.result?.version === 2 && r.result?.buffer_asset)
      r.result.runtimeMetrics = r.metrics;
    if (r.metrics) {
      state.metrics.peak_heap_bytes = Math.max(
        state.metrics.peak_heap_bytes,
        r.metrics.heap_bytes,
      );
      state.metrics.worker_ms += r.metrics.milliseconds;
    }
    if (state.active && state.metrics.worker_ms > 600000) {
      responseCall.reject(
        new Error(
          "Build exceeded ten minutes of processing. Your saved batch and validated checkpoints are retained.",
        ),
      );
      return;
    }
    if (r.error) responseCall.reject(RuntimeError.from(r.error));
    else responseCall.resolve(r.result);
  };
  state.worker.onerror = () =>
    failWorker(
      "The package worker stopped. Resume the saved batch or reduce its size.",
    );
  const wasm = await b.arrayBuffer();
  state.engineReady = rpc(
    "boot",
    {
      glue: urls[1],
      wasm,
    },
    {},
    [wasm],
  ).finally(() => urls.forEach(URL.revokeObjectURL));
  await state.engineReady;
  if (!["conversion", "painting", "sim"].includes(mode)) {
    await put("palette", await asset(m, "palette"));
    await call("init", {});
  }
}
export async function put(name: string, blob: Blob, context: Context = {}) {
  return rpc("put", { name, bytes: await blob.arrayBuffer() }, context);
}
export function call(
  op: "conversion_step",
  params: ConversionStep,
  context?: Context,
): Promise<ConversionProgress>;
export function call(
  op: Operation,
  params?: Metadata,
  context?: Context,
): Promise<Metadata>;
export async function call(
  op: Operation,
  params: Metadata = {},
  context: Context = {},
) {
  return rpc(op, { params }, context);
}
