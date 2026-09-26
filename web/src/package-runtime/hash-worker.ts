import { Sha256 } from "./streaming-sha256.js";
type HashRequest =
  | { id: number; blob: Blob; op?: never }
  | { id: number; op: "cancel" };
const scope = self as unknown as {
  onmessage: ((event: MessageEvent<HashRequest>) => void) | null;
  postMessage(value: unknown): void;
};
const running = new Map<number, { cancelled: boolean }>();
scope.onmessage = ({ data }) => {
  if (data.op === "cancel") {
    const job = running.get(data.id);
    if (job) job.cancelled = true;
    return;
  }
  const job = { cancelled: false };
  running.set(data.id, job);
  void (async () => {
    try {
      const hash = new Sha256();
      for (let offset = 0; offset < data.blob.size; offset += 1024 * 1024) {
        if (job.cancelled) return;
        const bytes = await data.blob
          .slice(offset, offset + 1024 * 1024)
          .arrayBuffer();
        if (job.cancelled) return;
        hash.update(new Uint8Array(bytes));
        // Yield to cancellation messages even for memory-backed Blobs.
        await new Promise((resolve) => setTimeout(resolve, 0));
      }
      if (!job.cancelled)
        scope.postMessage({ id: data.id, sha256: hash.digest() });
    } catch (error) {
      if (!job.cancelled)
        scope.postMessage({
          id: data.id,
          error: error instanceof Error ? error.message : String(error),
        });
    } finally {
      running.delete(data.id);
    }
  })();
};
