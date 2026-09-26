import "fake-indexeddb/auto";
import test from "node:test";
import assert from "node:assert/strict";
import { source } from "./helpers.mjs";

const store = await source("package-runtime/store.ts");
const database = await new Promise((resolve, reject) => {
  const req = indexedDB.open("sims2-creator-packages", 1);
  req.onsuccess = () => resolve(req.result);
  req.onerror = () => reject(req.error);
});
const wait = (ms = 0) => new Promise((resolve) => setTimeout(resolve, ms));
const bytes = (value) => new TextEncoder().encode(JSON.stringify(value)).length;
async function raw(names, fn, mode = "readwrite") {
  const tx = database.transaction(names, mode);
  const done = new Promise((resolve, reject) => {
    tx.oncomplete = resolve;
    tx.onabort = () => reject(tx.error);
  });
  const value = fn(tx);
  await done;
  return value?.result;
}
const readMeta = (key) =>
  raw(["meta"], (tx) => tx.objectStore("meta").get(key), "readonly");
function job(id = store.id(), kind = "tattoo") {
  return {
    schema_version: 1,
    id,
    kind,
    revision: 1,
    state: "draft",
    updated: 17,
    label: `Saved ${id}`,
    manifest: {
      schema_version: 1,
      protocol_version: 1,
      release: "old-engine",
      assets: {},
    },
    files: [],
    parameters: {},
    ui: {},
    runtimeBlobs: [],
    snapshot: { number: 1.0000000000000002, future: { untouched: [0, 255] } },
    unknown: { preserve: "all fields" },
  };
}
function reads() {
  const calls = [];
  const originals = new Map();
  for (const method of ["get", "getAll", "getKey", "openCursor"]) {
    originals.set(method, IDBObjectStore.prototype[method]);
    IDBObjectStore.prototype[method] = function (...args) {
      calls.push({ store: this.name, method, mode: this.transaction.mode });
      return originals.get(method).apply(this, args);
    };
  }
  return {
    calls,
    stop() {
      for (const [method, original] of originals)
        IDBObjectStore.prototype[method] = original;
    },
  };
}
test.beforeEach(async () => {
  await raw(["jobs", "blobs", "meta"], (tx) => {
    for (const name of ["jobs", "blobs", "meta"]) tx.objectStore(name).clear();
  });
});

test("v1 backfill preserves legacy records and paginates tied timestamps by kind", async () => {
  const originals = Array.from({ length: 61 }, (_, n) =>
    job(`legacy-${String(n).padStart(3, "0")}`),
  );
  await raw(["jobs"], (tx) => {
    for (const value of originals) tx.objectStore("jobs").put(value);
    tx.objectStore("jobs").put(job("hair", "hair"));
  });
  assert.deepEqual(await store.backfillJobSummaries({ limit: 7 }), {
    complete: false,
    processed: 7,
  });
  assert.equal(await store.countJobSummaries("tattoo"), 61);
  const observed = reads();
  try {
    const first = await store.listJobSummaries("tattoo");
    const second = await store.listJobSummaries("tattoo", {
      cursor: first.cursor,
    });
    const third = await store.listJobSummaries("tattoo", {
      cursor: second.cursor,
    });
    assert.deepEqual(
      [first.items.length, second.items.length, third.items.length],
      [25, 25, 11],
    );
    assert.equal(third.cursor, undefined);
    assert.deepEqual(
      [...first.items, ...second.items, ...third.items].map((item) => item.id),
      originals.map((item) => item.id),
    );
    assert.ok(
      first.items.every(
        (item) => !item.manifest && !item.snapshot && !item.files,
      ),
    );
    assert.equal(await store.countJobSummaries("hair"), 1);
    assert.ok(
      observed.calls.every(
        (call) => call.store === "meta" && call.mode === "readonly",
      ),
    );
    assert.ok(observed.calls.every((call) => call.method !== "getAll"));
  } finally {
    observed.stop();
  }
  for (const original of originals)
    assert.deepEqual(await store.getJob(original.id), original);
  assert.equal(database.version, 1);
  assert.deepEqual([...database.objectStoreNames], ["blobs", "jobs", "meta"]);
});

test("save, interrupt and removal commit summaries, counts and accounting with jobs", async () => {
  const value = job("transactional");
  await store.saveJob(value);
  assert.equal(await store.countJobSummaries("tattoo"), 1);
  assert.equal(
    (await readMeta("usage")).bytes,
    bytes(await store.getJob(value.id)),
  );
  const token = await store.acquire(value.id);
  await store.saveJob({ ...value, state: "building" }, token);
  await store.interrupt(token);
  assert.equal(
    (await store.listJobSummaries("tattoo")).items[0].state,
    "interrupted",
  );
  assert.equal(
    (await readMeta("usage")).bytes,
    bytes(await store.getJob(value.id)),
  );
  await store.removeJob(value.id);
  assert.equal(await store.countJobSummaries("tattoo"), 0);
  assert.equal((await readMeta("usage")).bytes, 0);
  assert.ok(await readMeta(`deleted:${value.id}`));
  await assert.rejects(store.saveJob({ ...value, revision: 9 }), /deleted/);
});

test("legacy notifications reconcile authoritative edits, new jobs and removals", async () => {
  const value = job("legacy-update");
  await store.saveJob(value);
  await store.countJobSummaries("tattoo");
  const legacy = {
    ...(await store.getJob(value.id)),
    kind: "hair",
    label: "Old tab edit",
    revision: 2,
  };
  await raw(["jobs"], (tx) => tx.objectStore("jobs").put(legacy));
  assert.equal((await store.reconcileJobSummary(value.id)).label, legacy.label);
  assert.equal(await store.countJobSummaries("tattoo"), 0);
  assert.equal(await store.countJobSummaries("hair"), 1);
  await raw(["jobs"], (tx) => {
    tx.objectStore("jobs").delete(value.id);
    tx.objectStore("jobs").put(job("unannounced", "sim"));
  });
  await store.reconcileJobSummaries();
  assert.equal(await store.countJobSummaries("hair"), 0);
  assert.equal(await store.countJobSummaries("sim"), 1);
  assert.equal(await store.reconcileJobSummary(value.id), undefined);
  assert.deepEqual(
    (await store.getJob("unannounced")).snapshot,
    value.snapshot,
  );
});

test("verified runtime pins restore missing blobs atomically and ignore unrelated map entries", async () => {
  const blob = new Blob([new Uint8Array([0, 255, 10, 3])]);
  const key = "asset:verified";
  const value = job("pinned");
  value.runtimeBlobs = [key, key];
  value.manifest.assets.wasm = { sha256: "verified", size: blob.size };
  const pins = new Map([
    [key, blob],
    ["asset:unrelated", new Blob(["unrelated"])],
  ]);
  await store.saveJob(value, null, pins);
  assert.deepEqual(
    await (await store.getBlob(key)).arrayBuffer(),
    await blob.arrayBuffer(),
  );
  assert.equal(await store.getBlob("asset:unrelated"), undefined);
  const saved = await store.getJob(value.id);
  assert.deepEqual({ ...saved, updated: value.updated }, value);
  assert.equal((await readMeta("usage")).bytes, bytes(saved) + blob.size);
  const observed = reads();
  try {
    await store.saveJob({ ...saved, revision: 2 }, null, pins);
    assert.ok(
      !observed.calls.some(
        (call) => call.store === "blobs" && call.method === "get",
      ),
    );
  } finally {
    observed.stop();
  }
});

test("pin, quota and summary failures roll back the entire job transaction", async () => {
  const value = job("rollback");
  const key = "asset:pin";
  const blob = new Blob(["pin"]);
  value.runtimeBlobs = [key];
  value.manifest.assets.wasm = { sha256: "pin", size: blob.size };
  await assert.rejects(store.saveJob(value, null, new Map()), /pinned/);
  await assert.rejects(
    store.saveJob(value, null, new Map([[key, new Blob(["wrong size"])]])),
    /pinned/,
  );
  await raw(["meta"], (tx) =>
    tx.objectStore("meta").put({ id: "usage", bytes: store.BUDGET }),
  );
  await assert.rejects(
    store.saveJob(value, null, new Map([[key, blob]])),
    /2 GiB/,
  );
  assert.equal(await store.getBlob(key), undefined);
  assert.equal(await store.getJob(value.id), undefined);
  await raw(["meta"], (tx) =>
    tx.objectStore("meta").put({ id: "usage", bytes: 0 }),
  );
  const original = IDBObjectStore.prototype.put;
  IDBObjectStore.prototype.put = function (record, ...keys) {
    if (this.name === "meta" && record.id[0] === "job-summary")
      throw new DOMException("Full", "QuotaExceededError");
    return original.call(this, record, ...keys);
  };
  try {
    await assert.rejects(
      store.saveJob(value, null, new Map([[key, blob]])),
      /storage is full/,
    );
  } finally {
    IDBObjectStore.prototype.put = original;
  }
  assert.equal(await store.getBlob(key), undefined);
  assert.equal(await store.getJob(value.id), undefined);
  assert.equal(await store.countJobSummaries("tattoo"), 0);
  assert.equal((await readMeta("usage")).bytes, 0);
});

test("incremental deletion checks actual legacy references in the deletion transaction", async () => {
  const value = job("references");
  await store.saveJob(value);
  await store.cleanup();
  const names = ["file", "model", "runtime", "checkpoint", "output"].map(
    (part) => `job:unowned:package:${part}`,
  );
  for (const key of names) await store.putBlob(key, new Blob([key]));
  const legacy = {
    ...(await store.getJob(value.id)),
    files: [{ blob: names[0] }],
    modelSource: { blob: names[1] },
    runtimeBlobs: [names[2]],
    checkpoints: [{ blob: names[3] }],
    output: { parts: [names[4]], size: 42 },
  };
  await raw(["jobs"], (tx) => tx.objectStore("jobs").put(legacy));
  await store.cleanup();
  for (const key of names) {
    assert.ok(await store.getBlob(key));
    await store.deleteBlob(key);
    assert.ok(await store.getBlob(key));
  }
  await store.removeJob(value.id);
  await store.cleanup();
  for (const key of names) assert.equal(await store.getBlob(key), undefined);
  assert.ok(await readMeta(`deleted:${value.id}`));
});

test("cached pin restoration preserves an existing job when quota rejects an edit", async () => {
  const key = "asset:old-pin";
  const blob = new Blob(["verified old engine"]);
  const value = job("existing-pinned");
  value.runtimeBlobs = [key];
  value.manifest.assets.wasm = { sha256: "old-pin", size: blob.size };
  const cache = new Map([[key, blob]]);
  await store.saveJob(value, null, cache);
  const old = await store.getJob(value.id);
  // Simulate a legacy tab removing persisted bytes while RAM holds the
  // verified immutable Blob. The job, snapshot and pinned manifest remain.
  await raw(["blobs", "meta"], (tx) => {
    tx.objectStore("blobs").delete(key);
    tx.objectStore("meta").put({ id: "usage", bytes: store.BUDGET - 1 });
  });
  const events = [];
  const listener = (event) => events.push(event.detail);
  store.storageEvents.addEventListener("blobs-deleted", listener);
  try {
    await assert.rejects(
      store.saveJob({ ...old, revision: 2, label: "Failed edit" }, null, cache),
      /2 GiB/,
    );
    assert.deepEqual(await store.getJob(value.id), old);
    assert.equal(
      (await store.listJobSummaries("tattoo")).items[0].label,
      old.label,
    );
    assert.equal(await store.getBlob(key), undefined);
    assert.deepEqual(events, []);
    await raw(["meta"], (tx) =>
      tx.objectStore("meta").put({ id: "usage", bytes: bytes(old) }),
    );
    await store.saveJob(
      { ...old, revision: 2, label: "Restored edit" },
      null,
      cache,
    );
    assert.equal(
      await (await store.getBlob(key)).text(),
      "verified old engine",
    );
    assert.deepEqual((await store.getJob(value.id)).snapshot, old.snapshot);
    assert.equal(
      (await readMeta("usage")).bytes,
      bytes(await store.getJob(value.id)) + blob.size,
    );
    assert.deepEqual(events, []);
  } finally {
    store.storageEvents.removeEventListener("blobs-deleted", listener);
  }
});

test("replacements invalidate only their committed key, metadata saves keep warm pins", async () => {
  const events = [];
  const listener = (event) => events.push(event.detail);
  store.storageEvents.addEventListener("blobs-deleted", listener);
  try {
    await store.putBlob("asset:replace", new Blob(["original"]));
    await store.putBlob("asset:other", new Blob(["unaffected"]));
    assert.deepEqual(events, []);
    // Same key and same byte length still require a fresh integrity check.
    await store.putBlob("asset:replace", new Blob(["tampered"]));
    assert.deepEqual(events, [["asset:replace"]]);
    assert.equal(
      await (await store.getBlob("asset:replace")).text(),
      "tampered",
    );
    const value = job("metadata-only");
    await store.saveJob(value);
    await store.saveJob({ ...value, revision: 2 }, null, new Map());
    assert.deepEqual(events, [["asset:replace"]]);
    await raw(["meta"], (tx) =>
      tx.objectStore("meta").put({ id: "usage", bytes: store.BUDGET }),
    );
    await assert.rejects(
      store.putBlob("asset:replace", new Blob(["oversized replacement"])),
      /2 GiB/,
    );
    assert.deepEqual(events, [["asset:replace"]]);
    assert.equal(
      await (await store.getBlob("asset:replace")).text(),
      "tampered",
    );
  } finally {
    store.storageEvents.removeEventListener("blobs-deleted", listener);
  }
});

test("cleanup has its own cross-tab lease and protects live attempts until expiry", async () => {
  const otherTab = await source("package-runtime/store.ts");
  const value = job("active");
  await store.saveJob(value);
  const token = await store.acquire(value.id);
  const key = `job:${value.id}:package:${token}:0`;
  await store.putBlob(key, new Blob(["active bytes"]), token);
  const active = await store.lease();
  const observed = reads();
  try {
    await Promise.all([store.cleanup(), otherTab.cleanup()]);
    assert.equal(
      observed.calls.filter(
        (call) => call.store === "blobs" && call.method === "openCursor",
      ).length,
      1,
    );
  } finally {
    observed.stop();
  }
  assert.ok(await store.getBlob(key));
  assert.deepEqual(await store.lease(), active);
  await assert.rejects(store.removeJob(value.id), /Cancel/);
  await raw(["meta"], (tx) =>
    tx.objectStore("meta").put({
      id: "maintenance:lease",
      token: "other-maintainer",
      until: Date.now() + 120000,
    }),
  );
  const blocked = reads();
  try {
    await store.cleanup({ reconcile: true });
    assert.ok(blocked.calls.every((call) => call.store === "meta"));
  } finally {
    blocked.stop();
  }
  await raw(["meta"], (tx) =>
    tx.objectStore("meta").delete("maintenance:lease"),
  );
  const now = Date.now;
  Date.now = () => active.until + 1;
  try {
    await store.cleanup();
  } finally {
    Date.now = now;
  }
  assert.equal(await store.getBlob(key), undefined);
  assert.deepEqual(await store.lease(), active);
  await store.release(token);
});

test("grace expiry and idle cleanup avoid repeated scans, reconciliation repairs legacy accounting", async () => {
  await store.cleanup();
  await store.putBlob("grace", new Blob(["1234"]));
  const observed = reads();
  try {
    await store.cleanup();
    await store.cleanup();
    assert.ok(observed.calls.every((call) => call.store === "meta"));
  } finally {
    observed.stop();
  }
  const now = Date.now;
  const future = now() + 600001;
  Date.now = () => future;
  try {
    await store.cleanup();
  } finally {
    Date.now = now;
  }
  assert.equal(await store.getBlob("grace"), undefined);
  const legacy = job("legacy-accounted");
  await raw(["jobs", "blobs", "meta"], (tx) => {
    tx.objectStore("jobs").put(legacy);
    tx.objectStore("blobs").put({
      id: "old-orphan",
      blob: new Blob(["x"]),
      created: 1,
    });
    tx.objectStore("meta").put({ id: "usage", bytes: store.BUDGET });
  });
  await store.cleanup();
  assert.equal(await store.countJobSummaries("tattoo"), 1);
  assert.equal((await readMeta("usage")).bytes, bytes(legacy));
  assert.equal(await store.getBlob("old-orphan"), undefined);
});

test("only committed blob deletions invalidate caches, and cached pins can restore them", async () => {
  await store.cleanup();
  const key = "asset:cache";
  const blob = new Blob(["immutable"]);
  await store.putBlob(key, blob);
  const events = [];
  const checked = [];
  const onDeleted = (event) => {
    events.push(event.detail);
    checked.push(store.getBlob(key));
  };
  store.storageEvents.addEventListener("blobs-deleted", onDeleted);
  const original = IDBObjectStore.prototype.put;
  IDBObjectStore.prototype.put = function (value, ...keys) {
    if (value.id === "usage") throw new Error("abort deletion");
    return original.call(this, value, ...keys);
  };
  try {
    await assert.rejects(store.deleteBlob(key), /abort deletion/);
  } finally {
    IDBObjectStore.prototype.put = original;
  }
  assert.deepEqual(events, []);
  assert.ok(await store.getBlob(key));
  try {
    await store.deleteBlob(key);
    assert.deepEqual(events, [[key]]);
    assert.deepEqual(await Promise.all(checked), [undefined]);
    const value = job("cache-restored");
    value.runtimeBlobs = [key];
    value.manifest.assets.wasm = { sha256: "cache", size: blob.size };
    await store.saveJob(value, null, new Map([[key, blob]]));
    assert.equal(await (await store.getBlob(key)).text(), "immutable");
  } finally {
    store.storageEvents.removeEventListener("blobs-deleted", onDeleted);
  }
});

test("cleanup scheduler is opt-in, uses idle callbacks and can be stopped", async () => {
  const originalIdle = globalThis.requestIdleCallback;
  const originalCancel = globalThis.cancelIdleCallback;
  let idle;
  let cancelled = false;
  globalThis.requestIdleCallback = (callback) => {
    idle = callback;
    return 9;
  };
  globalThis.cancelIdleCallback = (id) => {
    assert.equal(id, 9);
    cancelled = true;
  };
  const observed = reads();
  let stop;
  try {
    await wait(5);
    assert.equal(idle, undefined);
    stop = store.startCleanupScheduler();
    assert.equal(store.startCleanupScheduler(), stop);
    await wait(280);
    assert.equal(typeof idle, "function");
    assert.deepEqual(observed.calls, []);
    stop();
    assert.equal(cancelled, true);
    idle();
    await wait(5);
    assert.deepEqual(observed.calls, []);
  } finally {
    stop?.();
    observed.stop();
    globalThis.requestIdleCallback = originalIdle;
    globalThis.cancelIdleCallback = originalCancel;
  }
});

async function until(predicate) {
  const deadline = Date.now() + 4000;
  while (!(await predicate())) {
    assert.ok(
      Date.now() < deadline,
      "Timed out waiting for storage maintenance",
    );
    await wait(10);
  }
}

test("startup repairs offline legacy changes despite recent completed backfill and cleanup", async () => {
  const originals = Array.from({ length: 205 }, (_, n) =>
    job(`offline-${String(n).padStart(3, "0")}`),
  );
  await raw(["jobs"], (tx) => {
    for (const value of originals) tx.objectStore("jobs").put(value);
  });
  await store.cleanup({ reconcile: true });
  const lastCleanup = await readMeta("maintenance:reconciled");
  const oldStartup = Date.now() - 1;
  const edited = {
    ...originals[0],
    kind: "hair",
    label: "Edited in the old app",
    revision: 2,
  };
  const created = job("offline-new", "sim");
  await raw(["jobs", "meta"], (tx) => {
    tx.objectStore("meta").put({
      id: "maintenance:startup-reconciled",
      at: oldStartup,
    });
    tx.objectStore("jobs").put(edited);
    tx.objectStore("jobs").delete(originals[1].id);
    tx.objectStore("jobs").put(created);
  });
  assert.equal((await readMeta("job-summary-backfill")).complete, true);
  assert.equal(await store.countJobSummaries("tattoo"), 205);
  const observed = reads();
  const written = new Map();
  const put = IDBObjectStore.prototype.put;
  IDBObjectStore.prototype.put = function (value, ...keys) {
    if (this.name === "meta" && value.id[0] === "job-summary") {
      const count = written.get(this.transaction) || 0;
      written.set(this.transaction, count + 1);
    }
    return put.call(this, value, ...keys);
  };
  let notifications = 0;
  const listener = () => notifications++;
  store.storageEvents.addEventListener("summaries-reconciled", listener);
  let stop;
  try {
    stop = store.startCleanupScheduler();
    await until(() => notifications === 1);
    await wait(30);
    stop();
    assert.equal(await store.countJobSummaries("tattoo"), 203);
    assert.equal(await store.countJobSummaries("hair"), 1);
    assert.equal(await store.countJobSummaries("sim"), 1);
    assert.equal(
      (await store.listJobSummaries("hair")).items[0].label,
      edited.label,
    );
    assert.ok([...written.values()].every((count) => count <= 100));
    assert.equal(
      observed.calls.filter(
        (call) => call.store === "jobs" && call.method === "openCursor",
      ).length,
      3,
    );
    assert.ok(
      observed.calls.every(
        (call) => call.store !== "blobs" && call.method !== "getAll",
      ),
    );
    assert.deepEqual(await readMeta("maintenance:reconciled"), lastCleanup);
    assert.ok(
      (await readMeta("maintenance:startup-reconciled")).at > oldStartup,
    );
    assert.equal(await readMeta("maintenance:lease"), undefined);
    observed.stop();
    assert.deepEqual(await store.getJob(edited.id), edited);
    assert.deepEqual(await store.getJob(created.id), created);

    // An immediate close, legacy edit and reopen must not be skipped by a
    // completion cooldown from the preceding startup.
    await raw(["jobs"], (tx) =>
      tx
        .objectStore("jobs")
        .put({ ...edited, label: "Edited again while closed" }),
    );
    stop = store.startCleanupScheduler();
    await until(() => notifications === 2);
    await wait(30);
    stop();
    assert.equal(
      (await store.listJobSummaries("hair")).items[0].label,
      "Edited again while closed",
    );
  } finally {
    stop?.();
    observed.stop();
    IDBObjectStore.prototype.put = put;
    store.storageEvents.removeEventListener("summaries-reconciled", listener);
  }
});

test("concurrent scheduler startups share the summary pass and do not rescan on ordinary wakes", async () => {
  const otherTab = await source("package-runtime/store.ts");
  await raw(["jobs"], (tx) => {
    for (let n = 0; n < 205; n++)
      tx.objectStore("jobs").put(job(`startup-${n}`));
  });
  await store.cleanup({ reconcile: true });
  await raw(["jobs"], (tx) =>
    tx.objectStore("jobs").put(job("new-offline", "hair")),
  );
  const observed = reads();
  let localNotifications = 0;
  let otherNotifications = 0;
  const localListener = () => localNotifications++;
  const otherListener = () => otherNotifications++;
  store.storageEvents.addEventListener("summaries-reconciled", localListener);
  otherTab.storageEvents.addEventListener(
    "summaries-reconciled",
    otherListener,
  );
  const stop = store.startCleanupScheduler();
  const stopOther = otherTab.startCleanupScheduler();
  try {
    await until(
      async () =>
        (await readMeta("maintenance:startup-reconciled")) !== undefined,
    );
    // Node has no storage BroadcastChannel in this harness. The waiting
    // scheduler must also discover completion through its bounded retry.
    await wait(1200);
    assert.equal(localNotifications, 1);
    assert.equal(otherNotifications, 1);
    assert.equal(await otherTab.countJobSummaries("hair"), 1);
    assert.equal(
      observed.calls.filter(
        (call) => call.store === "jobs" && call.method === "openCursor",
      ).length,
      3,
    );
    assert.ok(observed.calls.every((call) => call.store !== "blobs"));
    observed.calls.length = 0;
    await store.putBlob("ordinary-grace", new Blob(["recent input"]));
    observed.calls.length = 0;
    await wait(350);
    assert.ok(observed.calls.every((call) => call.store === "meta"));
  } finally {
    stop();
    stopOther();
    observed.stop();
    store.storageEvents.removeEventListener(
      "summaries-reconciled",
      localListener,
    );
    otherTab.storageEvents.removeEventListener(
      "summaries-reconciled",
      otherListener,
    );
  }
});

class Element extends EventTarget {
  nodes = [];
  parent = null;
  open = false;
  hidden = false;
  disabled = false;
  value = "";
  get textContent() {
    return this.value;
  }
  set textContent(value) {
    this.replaceChildren();
    this.value = value;
  }
  get children() {
    return { item: (index) => this.nodes[index] || null };
  }
  append(...nodes) {
    for (const node of nodes) this.insertBefore(node, null);
  }
  insertBefore(node, before) {
    node.remove();
    node.parent = this;
    const index = before ? this.nodes.indexOf(before) : this.nodes.length;
    this.nodes.splice(index, 0, node);
  }
  replaceChildren(...nodes) {
    for (const child of this.nodes) child.parent = null;
    this.nodes = [];
    this.append(...nodes);
  }
  remove() {
    if (this.parent)
      this.parent.nodes.splice(this.parent.nodes.indexOf(this), 1);
    this.parent = null;
  }
}
test("saved panels ignore progress, defer collapsed rows, coalesce and load 25 at a time", async () => {
  await raw(["jobs"], (tx) => {
    for (let n = 0; n < 61; n++)
      tx.objectStore("jobs").put(job(`panel-${String(n).padStart(3, "0")}`));
  });
  await store.countJobSummaries("tattoo");
  const originalDocument = globalThis.document;
  globalThis.document = {
    getElementById: () => ({}),
    createElement: () => new Element(),
  };
  const { createSavedPanel } = await source("package-runtime/saved-panel.ts");
  const events = new EventTarget();
  const container = new Element();
  const refresh = createSavedPanel(
    {
      restore: store.getJob,
      download: async () => {},
      events,
      onDeleted: (id) =>
        events.dispatchEvent(new CustomEvent("deleted", { detail: id })),
    },
    container,
    "tattoo",
    () => {},
  );
  const [details] = container.nodes;
  const [summary, , list, more] = details.nodes;
  try {
    await refresh();
    assert.equal(summary.textContent, "Saved batches (61)");
    assert.equal(list.nodes.length, 0);
    const progress = reads();
    try {
      for (let n = 0; n < 25; n++)
        events.dispatchEvent(
          new CustomEvent("job", { detail: job("transient") }),
        );
      events.dispatchEvent(
        new CustomEvent("persisted", { detail: job("other", "hair") }),
      );
      await wait(5);
      assert.deepEqual(progress.calls, []);
      for (let n = 0; n < 25; n++)
        events.dispatchEvent(
          new CustomEvent("persisted", { detail: job("panel-000") }),
        );
      await wait(10);
      assert.ok(
        progress.calls.every(
          (call) => call.store === "meta" && call.method !== "openCursor",
        ),
      );
      assert.ok(progress.calls.length <= 4);
      assert.equal(list.nodes.length, 0);
    } finally {
      progress.stop();
    }
    details.open = true;
    details.dispatchEvent(new Event("toggle"));
    await refresh();
    assert.equal(list.nodes.length, 25);
    assert.equal(more.hidden, false);
    const first = list.nodes[0];
    more.dispatchEvent(new Event("click"));
    await refresh();
    assert.equal(list.nodes.length, 50);
    assert.equal(list.nodes[0], first);
    more.dispatchEvent(new Event("click"));
    await refresh();
    assert.equal(list.nodes.length, 61);
    assert.equal(more.hidden, true);
    const edited = {
      ...(await store.getJob("panel-000")),
      label: "Legacy changed label",
    };
    await raw(["jobs"], (tx) => tx.objectStore("jobs").put(edited));
    events.dispatchEvent(new CustomEvent("refresh", { detail: edited.id }));
    await refresh();
    assert.equal(list.nodes[0], first);
    assert.match(first.nodes[0].textContent, /Legacy changed label/);
    await raw(["jobs"], (tx) => tx.objectStore("jobs").delete(edited.id));
    events.dispatchEvent(new Event("refresh"));
    await refresh();
    assert.equal(list.nodes.length, 60);
    assert.equal(summary.textContent, "Saved batches (60)");
  } finally {
    globalThis.document = originalDocument;
  }
});
