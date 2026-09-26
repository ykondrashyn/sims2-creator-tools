globalThis.document = {
  baseURI: "http://static.test/sims2-creator-tools/",
  querySelector: () => null,
};
import "fake-indexeddb/auto";
import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { source } from "./helpers.mjs";
class HashWorker {
  static starts = 0;
  static hashes = 0;
  static bytes = 0;
  static stopped = 0;
  cancelled = new Set();
  constructor() {
    HashWorker.starts++;
  }
  terminate() {
    this.stopped = true;
    HashWorker.stopped++;
  }
  postMessage(data) {
    if (data.op === "cancel") {
      this.cancelled.add(data.id);
      return;
    }
    HashWorker.hashes++;
    HashWorker.bytes += data.blob.size;
    void data.blob.arrayBuffer().then((bytes) => {
      if (!this.stopped && !this.cancelled.has(data.id))
        this.onmessage?.({
          data: {
            id: data.id,
            sha256: createHash("sha256")
              .update(new Uint8Array(bytes))
              .digest("hex"),
          },
        });
    });
  }
}
globalThis.Worker = HashWorker;
const assets = await source("package-runtime/assets.ts");
const store = await source("package-runtime/store.ts");
const hashing = await source("package-runtime/hashing.ts");
const digest = (bytes) => createHash("sha256").update(bytes).digest("hex");
function manifest(name, bytes) {
  return {
    schema_version: 1,
    protocol_version: 1,
    release: "a".repeat(64),
    assets: {
      [name]: {
        sha256: digest(bytes),
        size: bytes.length,
        mime: "application/octet-stream",
        url: `/fixture/${name}`,
      },
    },
  };
}
test("concurrent manifest calls share the pending request and retry failures", async () => {
  let fetches = 0,
    fail = false;
  globalThis.fetch = async () => {
    fetches++;
    await new Promise((resolve) => queueMicrotask(resolve));
    return new Response(
      JSON.stringify({
        schema_version: 1,
        protocol_version: 1,
        release: "a".repeat(64),
      }),
      { status: fail ? 500 : 200 },
    );
  };
  await Promise.all([assets.manifest(), assets.manifest(), assets.manifest()]);
  assert.equal(fetches, 1);
  await assets.manifest();
  assert.equal(fetches, 1);
  fail = true;
  await assert.rejects(assets.manifest({ refresh: true }));
  fail = false;
  await Promise.all([
    assets.manifest({ refresh: true }),
    assets.manifest({ refresh: true }),
  ]);
  assert.equal(fetches, 3);
});
test("warm assets skip hashing, concurrent loads share verification, eviction verifies storage", async () => {
  assets.clearAssetCache();
  const bytes = Buffer.alloc(1024, 53),
    m = manifest("first", bytes);
  let fetches = 0;
  globalThis.fetch = async () => {
    fetches++;
    return new Response(bytes);
  };
  const before = HashWorker.hashes;
  const [a, b] = await Promise.all([
    assets.asset(m, "first"),
    assets.asset(m, "first"),
  ]);
  assert.equal(a, b);
  assert.equal(fetches, 1);
  assert.equal(HashWorker.hashes - before, 1);
  for (let i = 0; i < 5; i++) assert.equal(await assets.asset(m, "first"), a);
  assert.equal(HashWorker.hashes - before, 1);
  assets.clearAssetCache();
  await assets.asset(m, "first");
  assert.equal(HashWorker.hashes - before, 2);
  assert.equal(fetches, 1);
  await store.putBlob(`asset:${m.assets.first.sha256}`, new Blob(["damaged"]));
  assets.clearAssetCache();
  await assert.rejects(assets.asset(m, "first"), /damaged/);
});
test("asset LRU retains no more than 64 MiB and rejects changed expected sizes", async () => {
  assets.clearAssetCache();
  const all = [1, 2, 3].map((n) => {
    const bytes = Buffer.alloc(24 * 1024 ** 2, n);
    return { bytes, m: manifest(String(n), bytes), name: String(n) };
  });
  globalThis.fetch = async (url) =>
    new Response(all.find((a) => url === `/fixture/${a.name}`).bytes);
  const before = HashWorker.hashes;
  for (const a of all) await assets.asset(a.m, a.name);
  await assets.asset(all[1].m, all[1].name);
  assert.equal(HashWorker.hashes - before, 3);
  await assets.asset(all[0].m, all[0].name);
  assert.equal(HashWorker.hashes - before, 4);
  const wrong = structuredClone(all[0].m);
  wrong.assets["1"].size--;
  await assert.rejects(assets.asset(wrong, "1"), /damaged/);
});
test("sequential hashes share a worker and cancellation is request scoped", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const started = HashWorker.starts,
    stopped = HashWorker.stopped;
  for (const value of ["one", "two"])
    assert.equal(await hashing.hashBlob(new Blob([value])), digest(value));
  assert.equal(HashWorker.starts - started, 1);
  const controller = new AbortController();
  const cancelled = hashing.hashBlob(new Blob(["cancel"]), {
    signal: controller.signal,
  });
  const successful = hashing.hashBlob(new Blob(["keep"]));
  controller.abort();
  await assert.rejects(cancelled, { name: "AbortError" });
  assert.equal(await successful, digest("keep"));
  t.mock.timers.tick(4999);
  assert.equal(HashWorker.stopped - stopped, 0);
  t.mock.timers.tick(1);
  assert.equal(HashWorker.stopped - stopped, 1);
});
