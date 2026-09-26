import type { Metadata } from "./types.js";
import { validateRequest } from "./validation.js";
interface Engine {
  put_asset(name: string, bytes: Uint8Array): string;
  take_asset(name: string): Uint8Array<ArrayBuffer>;
  drop_asset(name: string): void;
  call(request: string): string;
}
let engine: Engine, memory: WebAssembly.Memory;
const scope = self as unknown as {
  onmessage: ((event: MessageEvent<Metadata>) => void) | null;
  postMessage(value: unknown, transfer?: Transferable[]): void;
};
scope.onmessage = async ({ data: r }) => {
  const started = performance.now();
  const base = {
    id: r.id,
    job: r.job,
    revision: r.revision,
    version: 1,
    attempt: r.attempt,
  };
  try {
    let result, bytes;
    if (r.op === "boot") {
      const module = await import(r.glue);
      const wasm = await module.default({ module_or_path: r.wasm });
      memory = wasm.memory;
      engine = new module.BrowserEngine();
      result = { version: 1, attempt: r.attempt };
    } else if (r.op === "put") {
      result = engine.put_asset(r.name, new Uint8Array(r.bytes));
    } else if (r.op === "take") {
      bytes = engine.take_asset(r.name);
      result = { bytes };
    } else if (r.op === "drop") {
      engine.drop_asset(r.name);
      result = {};
    } else {
      const request = { version: 1, op: r.op, params: r.params };
      validateRequest(request);
      result = JSON.parse(engine.call(JSON.stringify(request)));
    }
    const transfers = bytes ? [bytes.buffer] : [];
    if (result?.buffer_asset) {
      result.buffer = engine.take_asset(result.buffer_asset).buffer;
      transfers.push(result.buffer);
      for (const texture of result.textures || []) {
        texture.bytes = engine.take_asset(texture.asset).buffer;
        transfers.push(texture.bytes);
      }
    }
    scope.postMessage(
      {
        ...base,
        result,
        metrics: {
          heap_bytes: memory?.buffer.byteLength || 0,
          milliseconds: performance.now() - started,
        },
      },
      transfers,
    );
  } catch (error) {
    const trapped =
      error instanceof WebAssembly.RuntimeError || error instanceof RangeError;
    scope.postMessage({
      ...base,
      error: {
        code: trapped ? "engine_resource_failure" : "engine_error",
        operation: r.op,
        message: trapped
          ? "The package engine stopped or exceeded available memory. Your saved inputs and valid checkpoints are retained. Resume the batch, close other tabs, or create a smaller batch."
          : String(error instanceof Error ? error.message : error),
        detail: trapped
          ? String(error instanceof Error ? error.message : error)
          : undefined,
      },
    });
  }
};
