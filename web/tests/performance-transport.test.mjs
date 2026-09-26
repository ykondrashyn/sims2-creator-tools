import "fake-indexeddb/auto";
import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { source } from "./helpers.mjs";

const digest = (bytes) => createHash("sha256").update(bytes).digest("hex");

test("hash worker stops between chunks without cancelling another request", async () => {
  const messages = [],
    reads = [];
  globalThis.self = { postMessage: (value) => messages.push(value) };
  await source("package-runtime/hash-worker.ts");
  const scope = globalThis.self;
  const large = new Blob([new Uint8Array(8 * 1024 ** 2)]);
  const observed = {
    size: large.size,
    slice(start, end) {
      reads.push(start);
      return {
        async arrayBuffer() {
          const bytes = await large.slice(start, end).arrayBuffer();
          scope.onmessage({ data: { id: 1, op: "cancel" } });
          return bytes;
        },
      };
    },
  };
  scope.onmessage({ data: { id: 1, blob: observed } });
  scope.onmessage({ data: { id: 2, blob: new Blob(["unrelated save"]) } });
  for (let i = 0; i < 100 && !messages.length; i++)
    await new Promise((resolve) => setTimeout(resolve, 5));
  assert.deepEqual(reads, [0]);
  assert.deepEqual(messages, [{ id: 2, sha256: digest("unrelated save") }]);
});

test("WASM boot transfers its buffer and leaves the saved Blob usable", async () => {
  let bootTransfer = false;
  class Worker {
    terminate() {}
    postMessage(request, transfer = []) {
      if (request.blob) {
        void request.blob.arrayBuffer().then((bytes) => {
          this.onmessage({
            data: { id: request.id, sha256: digest(new Uint8Array(bytes)) },
          });
        });
        return;
      }
      assert.equal(request.op, "boot");
      assert.deepEqual(transfer, [request.wasm]);
      const received = structuredClone(request, { transfer });
      assert.equal(request.wasm.byteLength, 0);
      assert.equal(received.wasm.byteLength, 8);
      bootTransfer = true;
      queueMicrotask(() =>
        this.onmessage({
          data: {
            version: 1,
            id: request.id,
            job: request.job,
            revision: request.revision,
            attempt: request.attempt,
            result: { version: 1 },
          },
        }),
      );
    }
  }
  globalThis.Worker = Worker;
  const data = {
    worker: new TextEncoder().encode("worker fixture"),
    glue: new TextEncoder().encode("glue fixture"),
    wasm: new Uint8Array([0, 97, 115, 109, 1, 0, 0, 0]),
  };
  const manifest = { release: "b".repeat(64), assets: {} };
  for (const [name, bytes] of Object.entries(data))
    manifest.assets[name] = {
      sha256: digest(bytes),
      size: bytes.length,
      url: `/test/${name}`,
    };
  globalThis.fetch = async (url) => new Response(data[url.split("/").at(-1)]);
  const session = await source("package-runtime/session.ts");
  await session.boot(manifest, "conversion");
  assert.equal(bootTransfer, true);
  const store = await source("package-runtime/store.ts");
  const saved = await store.getBlob(`asset:${manifest.assets.wasm.sha256}`);
  assert.equal(saved.size, 8);
  assert.equal(
    digest(new Uint8Array(await saved.arrayBuffer())),
    manifest.assets.wasm.sha256,
  );
  session.failWorker("test finished");
});
