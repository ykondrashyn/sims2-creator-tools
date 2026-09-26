import "fake-indexeddb/auto";
import test from "node:test";
import assert from "node:assert/strict";
import { source } from "./helpers.mjs";
const store = await source("package-runtime/store.ts");
const { validateSavedJob } = await source("package-runtime/validation.ts");
const { RuntimeError } = await source("package-runtime/errors.ts");
function job(id = store.id()) {
  return {
    schema_version: 1,
    id,
    kind: "hair",
    revision: 1,
    state: "draft",
    manifest: { protocol_version: 1, release: "a".repeat(64) },
    files: [],
    parameters: {},
    ui: {},
    unknown: { floating: 1.0000000000000002 },
  };
}
test("worker error codes survive saved record serialization", async () => {
  const error = {
    code: "engine_resource_failure",
    operation: "build_hair",
    message: "Too large",
  };
  const j = { ...job(), error: RuntimeError.from(error).toJSON() };
  await store.saveJob(j);
  assert.deepEqual((await store.getJob(j.id)).error, error);
  await store.removeJob(j.id);
});
async function meta(value) {
  const req = indexedDB.open("sims2-creator-packages", 1);
  const db = await new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
  await new Promise((resolve, reject) => {
    const tx = db.transaction("meta", "readwrite");
    tx.objectStore("meta").put(value);
    tx.oncomplete = resolve;
    tx.onabort = () => reject(tx.error);
  });
  db.close();
}
test("stale edits, deletion tombstones and pinned snapshots", async () => {
  const j = job();
  await store.saveJob(j);
  await assert.rejects(store.saveJob(j), /another tab/);
  const snapshot = {
    pixels: [0, 255],
    number: 1.0000000000000002,
    identities: { texture: "ffffffffffffffff" },
  };
  j.revision++;
  j.snapshot = snapshot;
  await store.saveJob(j);
  const restored = await store.getJob(j.id);
  validateSavedJob(restored);
  assert.deepEqual(restored.snapshot, snapshot);
  assert.deepEqual(restored.unknown, j.unknown);
  await store.removeJob(j.id);
  assert.equal(await store.getJob(j.id), undefined);
  await assert.rejects(store.saveJob({ ...j, revision: 99 }), /deleted/);
});
test("cross-tab lease, expired attempts and cancellation fencing", async () => {
  const j = job();
  await store.saveJob(j);
  const token = await store.acquire(j.id);
  await assert.rejects(store.acquire("second"), /Another package build/);
  await assert.rejects(store.saveJob({ ...j, revision: 2 }), /read-only/);
  await store.saveJob({ ...j, state: "building" }, token);
  await store.interrupt(token);
  assert.equal((await store.getJob(j.id)).state, "interrupted");
  await assert.rejects(
    store.putBlob("stale", new Blob(["wrong"]), token),
    /interrupted/,
  );
  const token2 = await store.acquire(j.id);
  await meta({ id: "lease", job: j.id, token: token2, until: Date.now() - 1 });
  await assert.rejects(store.heartbeat(token2), /interrupted/);
  const token3 = await store.acquire(j.id);
  await store.release(token2);
  assert.equal((await store.lease()).token, token3);
  await store.release(token3);
  await store.removeJob(j.id);
});
test("quota failure keeps the record and existing blobs", async () => {
  const j = job();
  await store.saveJob(j);
  await store.putBlob("original", new Blob(["original"]));
  await meta({ id: "usage", bytes: store.BUDGET });
  await assert.rejects(store.putBlob("overflow", new Blob(["x"])), /2 GiB/);
  assert.equal(await (await store.getBlob("original")).text(), "original");
  assert.ok(await store.getJob(j.id));
  assert.equal(await store.getBlob("overflow"), undefined);
  await store.cleanup();
  await store.removeJob(j.id);
});
test("unsupported records and immutable complete results remain stored", async () => {
  const j = job();
  await store.saveJob({ ...j, schema_version: 99 });
  assert.throws(
    () => validateSavedJob({ ...j, schema_version: 99 }),
    /unsupported/,
  );
  assert.equal((await store.getJob(j.id)).schema_version, 99);
  await store.removeJob(j.id);
  const completed = job();
  await store.saveJob(completed);
  const token = await store.acquire(completed.id);
  await store.saveJob(
    { ...completed, state: "complete", output: { parts: [] } },
    token,
  );
  await store.release(token);
  await assert.rejects(
    store.saveJob({ ...completed, revision: 9 }),
    /read-only/,
  );
  await store.removeJob(completed.id);
});
test("browser quota exception retains previously committed data", async () => {
  const j = job();
  await store.saveJob(j);
  const original = IDBObjectStore.prototype.put;
  IDBObjectStore.prototype.put = function (value, ...keys) {
    if (value.id === "quota-exception")
      throw new DOMException("Quota exceeded", "QuotaExceededError");
    return original.call(this, value, ...keys);
  };
  try {
    await assert.rejects(
      store.putBlob("quota-exception", new Blob(["x"])),
      /storage is full/,
    );
    assert.ok(await store.getJob(j.id));
  } finally {
    IDBObjectStore.prototype.put = original;
    await store.removeJob(j.id);
  }
});
test("newer database version is preserved and explains recovery", async () => {
  const j = job();
  await store.saveJob(j);
  const request = indexedDB.open("sims2-creator-packages", 2);
  const db = await new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  db.close();
  const olderClient = await source("package-runtime/store.ts");
  await assert.rejects(olderClient.listJobs(), /Local storage is unavailable/);
  const reopen = indexedDB.open("sims2-creator-packages", 2);
  const retained = await new Promise((resolve, reject) => {
    reopen.onsuccess = () => resolve(reopen.result);
    reopen.onerror = () => reject(reopen.error);
  });
  const get = retained.transaction("jobs").objectStore("jobs").get(j.id);
  const value = await new Promise((resolve) => {
    get.onsuccess = () => resolve(get.result);
  });
  assert.equal(value.id, j.id);
  retained.close();
});
