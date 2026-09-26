let worker: Worker | undefined;
let serial = 0;
let idleTimer: ReturnType<typeof setTimeout> | undefined;
const pending = new Map<
  number,
  {
    resolve: (digest: string) => void;
    reject: (reason: Error) => void;
    detach: () => void;
  }
>();
function abortError(signal?: AbortSignal): Error {
  return signal?.reason instanceof Error
    ? signal.reason
    : new DOMException("Hashing cancelled.", "AbortError");
}
function idle() {
  clearTimeout(idleTimer);
  if (!pending.size)
    idleTimer = setTimeout(() => {
      worker?.terminate();
      worker = undefined;
    }, 5000);
}
function stop() {
  clearTimeout(idleTimer);
  worker?.terminate();
  worker = undefined;
  for (const item of pending.values()) {
    item.detach();
    item.reject(
      new Error(
        "Hashing stopped. Your original files have not been changed. Retry saving the batch.",
      ),
    );
  }
  pending.clear();
}
export function hashBlob(
  blob: Blob,
  { signal }: { signal?: AbortSignal } = {},
): Promise<string> {
  if (signal?.aborted) return Promise.reject(abortError(signal));
  clearTimeout(idleTimer);
  if (!worker) {
    worker = new Worker(new URL("./hash-worker.mjs", import.meta.url), {
      type: "module",
    });
    worker.onmessage = ({
      data,
    }: {
      data: { id: number; sha256: string; error?: string };
    }) => {
      const item = pending.get(data.id);
      if (!item) return;
      pending.delete(data.id);
      item.detach();
      if (data.error) item.reject(new Error(data.error));
      else item.resolve(data.sha256);
      idle();
    };
    worker.onerror = stop;
  }
  const id = ++serial;
  return new Promise((resolve, reject) => {
    const abort = () => {
      if (!pending.delete(id)) return;
      signal?.removeEventListener("abort", abort);
      worker?.postMessage({ id, op: "cancel" });
      reject(abortError(signal));
      idle();
    };
    pending.set(id, {
      resolve,
      reject,
      detach: () => signal?.removeEventListener("abort", abort),
    });
    signal?.addEventListener("abort", abort, { once: true });
    try {
      worker!.postMessage({ id, blob });
    } catch (error) {
      pending.delete(id);
      signal?.removeEventListener("abort", abort);
      reject(error);
      idle();
    }
  });
}
