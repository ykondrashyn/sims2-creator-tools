import type { JobKind, SavedJob } from "./types.js";
import {
  blobKey,
  dirtyReferences,
  expires,
  forgetBlob,
  range,
  recordBytes,
  references,
  request as result,
  rows,
  syncSummary,
  trackBlob,
  visitJobs,
} from "./storage-metadata.js";
import type {
  JobSummary,
  JobSummaryCursor,
  JobSummaryPage,
} from "./storage-metadata.js";
export type {
  JobSummary,
  JobSummaryCursor,
  JobSummaryPage,
} from "./storage-metadata.js";
// Local files are stored as blobs. No job data is sent to the service.
export const BUDGET = 2 * 1024 ** 3;
export const storageEvents = new EventTarget();
const RECONCILE_INTERVAL = 24 * 60 * 60 * 1000;
const MAINTENANCE_LEASE = "maintenance:lease";
const STARTUP_RECONCILED = "maintenance:startup-reconciled";
const SUMMARY_BACKFILL = "job-summary-backfill";
const batchLimit = (limit = 100) =>
  Math.max(1, Math.min(250, Math.floor(limit) || 100));
let wakeMaintenance: (() => void) | undefined;
// Do not create a channel in Node test processes. In the app it also delivers
// committed deletions to verified Blob caches in the other open tabs.
const storageChannel =
  typeof window !== "undefined" && typeof BroadcastChannel === "function"
    ? new BroadcastChannel("sims2-package-storage")
    : null;
storageChannel?.addEventListener("message", (event) => {
  if (
    Array.isArray(event.data?.deleted) &&
    event.data.deleted.every((key: unknown) => typeof key === "string")
  )
    storageEvents.dispatchEvent(
      new CustomEvent<string[]>("blobs-deleted", {
        detail: event.data.deleted,
      }),
    );
  if (event.data?.summaries)
    storageEvents.dispatchEvent(new Event("summaries-reconciled"));
  wakeMaintenance?.();
});
function summariesReconciled() {
  storageEvents.dispatchEvent(new Event("summaries-reconciled"));
  storageChannel?.postMessage({ summaries: true });
}
function changed(deleted: string[] = []) {
  if (deleted.length)
    storageEvents.dispatchEvent(
      new CustomEvent<string[]>("blobs-deleted", { detail: deleted }),
    );
  storageChannel?.postMessage(deleted.length ? { deleted } : { dirty: true });
  wakeMaintenance?.();
}
function checkBudget(bytes: number) {
  if (bytes > BUDGET)
    throw new Error(
      "Saved data has reached the 2 GiB limit. Delete saved batches to free space.",
    );
}
const dbReady = new Promise<IDBDatabase>((resolve, reject) => {
  const req = indexedDB.open("sims2-creator-packages", 1);
  req.onupgradeneeded = () => {
    for (const name of ["jobs", "blobs", "meta"])
      req.result.createObjectStore(name, { keyPath: "id" });
  };
  req.onerror = () =>
    reject(
      new Error(
        "Local storage is unavailable. Enable site storage to create and restore package batches.",
      ),
    );
  req.onblocked = () =>
    reject(
      new Error(
        "Local storage is blocked by another open version. Close other tool tabs and reload. Saved batches have been kept.",
      ),
    );
  req.onsuccess = () => {
    req.result.onversionchange = () => req.result.close();
    resolve(req.result);
  };
});
dbReady.catch(() => {});
async function transaction<T>(
  names: string[],
  mode: IDBTransactionMode,
  fn: (tx: IDBTransaction) => Promise<T>,
): Promise<T> {
  const db = await dbReady;
  const tx = db.transaction(names, mode);
  const done = new Promise<void>((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
    tx.onabort = () =>
      reject(tx.error || new Error("Local transaction cancelled"));
  });
  void done.catch(() => {});
  try {
    const value = await fn(tx);
    await done;
    return value;
  } catch (error) {
    try {
      tx.abort();
    } catch {}
    await done.catch(() => {});
    if (error instanceof Error && error.name === "QuotaExceededError")
      throw new Error(
        "Browser storage is full. Delete saved batches or free disk space, then resume.",
      );
    throw error;
  }
}
export const getJob = (recordId: string): Promise<SavedJob | undefined> =>
  transaction(["jobs"], "readonly", (tx) =>
    result(tx.objectStore("jobs").get(recordId)),
  );
export const listJobs = (): Promise<SavedJob[]> =>
  transaction(["jobs"], "readonly", (tx) =>
    result(tx.objectStore("jobs").getAll()),
  );

/** Backfill a bounded batch without modifying any full job or snapshot. */
export async function backfillJobSummaries({
  limit = 100,
  reset = false,
} = {}) {
  return backfillSummaryBatch(limit, reset);
}
async function renewMaintenance(tx: IDBTransaction, token: string) {
  const meta = tx.objectStore("meta");
  const active = await result(meta.get(MAINTENANCE_LEASE));
  if (active?.token !== token || active.until <= Date.now())
    throw new Error("Storage maintenance moved to another tab.");
  meta.put({ ...active, until: Date.now() + 120000 });
}
async function backfillSummaryBatch(
  limit: number,
  reset: boolean,
  token?: string,
) {
  return transaction(["jobs", "meta"], "readwrite", async (tx) => {
    if (token) await renewMaintenance(tx, token);
    const meta = tx.objectStore("meta");
    const state = reset ? undefined : await result(meta.get(SUMMARY_BACKFILL));
    if (state?.complete) return { complete: true, processed: 0 };
    const jobs = await rows<SavedJob>(
      tx.objectStore("jobs"),
      state?.cursor === undefined
        ? undefined
        : IDBKeyRange.lowerBound(state.cursor, true),
      batchLimit(limit),
    );
    for (const { value } of jobs) await syncSummary(meta, value.id, value);
    const complete = jobs.length < batchLimit(limit);
    meta.put({
      id: SUMMARY_BACKFILL,
      cursor: jobs.at(-1)?.key ?? state?.cursor,
      complete,
    });
    return { complete, processed: jobs.length };
  });
}
let backfilling: Promise<void> | undefined;
async function ensureSummaries() {
  if (!backfilling)
    backfilling = (async () => {
      const complete = await transaction(
        ["meta"],
        "readonly",
        async (tx) =>
          (await result(tx.objectStore("meta").get(SUMMARY_BACKFILL)))
            ?.complete,
      );
      if (complete) return;
      while (!(await backfillJobSummaries()).complete)
        await new Promise<void>((resolve) => setTimeout(resolve, 0));
    })().finally(() => {
      backfilling = undefined;
    });
  await backfilling;
}
export async function reconcileJobSummary(
  recordId: string,
): Promise<JobSummary | undefined> {
  const summary = await transaction(["jobs", "meta"], "readwrite", async (tx) =>
    syncSummary(
      tx.objectStore("meta"),
      recordId,
      await result(tx.objectStore("jobs").get(recordId)),
    ),
  );
  wakeMaintenance?.();
  return summary;
}
let reconcilingSummaries: Promise<void> | undefined;
/** Fallback for legacy refresh notifications that do not identify a job. */
export async function reconcileJobSummaries() {
  if (!reconcilingSummaries)
    reconcilingSummaries = reconcileSummaryRecords().finally(() => {
      reconcilingSummaries = undefined;
    });
  await reconcilingSummaries;
  wakeMaintenance?.();
}
async function reconcileSummaryRecords(token?: string) {
  let batch = await backfillSummaryBatch(100, true, token);
  while (!batch.complete) {
    await new Promise<void>((resolve) => setTimeout(resolve, 0));
    batch = await backfillSummaryBatch(100, false, token);
  }
  let after: IDBValidKey | undefined;
  for (;;) {
    const next = await transaction(
      ["jobs", "meta"],
      "readwrite",
      async (tx) => {
        if (token) await renewMaintenance(tx, token);
        const meta = tx.objectStore("meta");
        const page = await rows<{ summary: JobSummary }>(
          meta,
          IDBKeyRange.bound(
            after ?? ["job-summary-id"],
            ["job-summary-id", []],
            after !== undefined,
          ),
          100,
        );
        for (const { value } of page) {
          if (
            (await result(tx.objectStore("jobs").getKey(value.summary.id))) ===
            undefined
          )
            await syncSummary(meta, value.summary.id);
        }
        return page.length === 100 ? page.at(-1)!.key : undefined;
      },
    );
    if (next === undefined) break;
    after = next;
    await new Promise<void>((resolve) => setTimeout(resolve, 0));
  }
}

async function reconcileStartup(since: number): Promise<boolean> {
  const token = id();
  const claim = await transaction(["meta"], "readwrite", async (tx) => {
    const meta = tx.objectStore("meta");
    const completed = await result(meta.get(STARTUP_RECONCILED));
    // Share work completed after this startup began, never skip a later
    // reopen merely because the preceding app session ended recently.
    if (completed?.at > since) return "complete";
    const active = await result(meta.get(MAINTENANCE_LEASE));
    if (active?.until > Date.now()) return "pending";
    meta.put({ id: MAINTENANCE_LEASE, token, until: Date.now() + 120000 });
    return "claimed";
  });
  if (claim !== "claimed") {
    // A waiting tab may have no BroadcastChannel, or may have mounted its
    // panel after the owner's notification. Refresh its local view as well.
    if (claim === "complete")
      storageEvents.dispatchEvent(new Event("summaries-reconciled"));
    return claim === "complete";
  }
  try {
    await reconcileSummaryRecords(token);
    await transaction(["meta"], "readwrite", async (tx) => {
      await renewMaintenance(tx, token);
      tx.objectStore("meta").put({ id: STARTUP_RECONCILED, at: Date.now() });
      tx.objectStore("meta").delete(MAINTENANCE_LEASE);
    });
    summariesReconciled();
    return true;
  } finally {
    await transaction(["meta"], "readwrite", async (tx) => {
      const meta = tx.objectStore("meta");
      if ((await result(meta.get(MAINTENANCE_LEASE)))?.token === token)
        meta.delete(MAINTENANCE_LEASE);
    });
  }
}
export async function countJobSummaries(kind: JobKind): Promise<number> {
  await ensureSummaries();
  return transaction(
    ["meta"],
    "readonly",
    async (tx) =>
      (await result(tx.objectStore("meta").get(["job-summary-count", kind])))
        ?.count || 0,
  );
}
export async function listJobSummaries(
  kind: JobKind,
  { cursor, limit = 25 }: { cursor?: JobSummaryCursor; limit?: number } = {},
): Promise<JobSummaryPage> {
  await ensureSummaries();
  const count = batchLimit(limit);
  return transaction(["meta"], "readonly", async (tx) => {
    const page = await rows<{ summary: JobSummary }>(
      tx.objectStore("meta"),
      IDBKeyRange.bound(
        cursor
          ? ["job-summary", kind, -cursor[0], cursor[1]]
          : ["job-summary", kind],
        ["job-summary", kind, []],
        !!cursor,
      ),
      count + 1,
    );
    const items = page.slice(0, count).map(({ value }) => value.summary);
    const last = items.at(-1);
    return {
      items,
      ...(page.length > count && last
        ? { cursor: [last.updated, last.id] as JobSummaryCursor }
        : {}),
    };
  });
}
export const getBlob = (recordId: string): Promise<Blob | undefined> =>
  transaction(
    ["blobs"],
    "readonly",
    async (tx) => (await result(tx.objectStore("blobs").get(recordId)))?.blob,
  );
export async function putBlob(
  recordId: string,
  blob: Blob,
  attempt: string | null = null,
) {
  const replaced = await transaction(
    ["blobs", "meta"],
    "readwrite",
    async (tx) => {
      await fence(tx, attempt);
      const b = tx.objectStore("blobs"),
        meta = tx.objectStore("meta");
      const old = await result(b.get(recordId));
      const usage = (await result(meta.get("usage")))?.bytes || 0;
      const bytes = usage - (old?.blob.size || 0) + blob.size;
      checkBudget(bytes);
      const created = Date.now();
      b.put({ id: recordId, blob, created });
      await trackBlob(
        meta,
        recordId,
        blob.size,
        created,
        expires(recordId, created),
      );
      meta.put({ id: "usage", bytes });
      return !!old;
    },
  );
  // Replacing bytes under an existing key also invalidates that cached
  // identity. Fresh writes and atomic verified pins do not flush warm assets.
  changed(replaced ? [recordId] : []);
}
export async function deleteBlob(
  recordId: string,
  attempt: string | null = null,
) {
  const deleted = await transaction(
    ["jobs", "blobs", "meta"],
    "readwrite",
    async (tx) => {
      await fence(tx, attempt);
      const b = tx.objectStore("blobs"),
        meta = tx.objectStore("meta");
      const old = await result(b.get(recordId));
      if (old) {
        const active = await result(meta.get("lease"));
        if (active?.token !== attempt && protectedAttempt(recordId, active))
          return [];
        const keep = await referencedCandidates(tx, new Set([recordId]));
        if (keep.has(recordId)) return [];
        const usage = (await result(meta.get("usage")))?.bytes || 0;
        b.delete(recordId);
        await forgetBlob(meta, recordId);
        meta.put({ id: "usage", bytes: Math.max(0, usage - old.blob.size) });
        return [recordId];
      }
      return [];
    },
  );
  if (deleted.length) changed(deleted);
}
export async function saveJob(
  job: SavedJob,
  attempt: string | null = null,
  pinnedAssets?: ReadonlyMap<string, Blob>,
) {
  await transaction(
    pinnedAssets ? ["jobs", "blobs", "meta"] : ["jobs", "meta"],
    "readwrite",
    async (tx) => {
      await fence(tx, attempt);
      const old = await result(tx.objectStore("jobs").get(job.id));
      if (await result(tx.objectStore("meta").get(`deleted:${job.id}`)))
        throw new Error("This batch was deleted.");
      const lease = await result(tx.objectStore("meta").get("lease"));
      if (
        !attempt &&
        ((lease?.job === job.id && lease.until > Date.now()) ||
          old?.state === "complete")
      )
        throw new Error(
          "This batch is building or complete and is read-only. Start a new batch to edit it.",
        );
      if (
        old &&
        (old.revision > job.revision ||
          (!attempt && job.state === "draft" && old.revision === job.revision))
      )
        throw new Error(
          "This batch changed in another tab. Open the saved version before editing.",
        );
      const saved = { ...job, updated: Date.now() };
      const usage =
        (await result(tx.objectStore("meta").get("usage")))?.bytes || 0;
      let bytes = usage - recordBytes(old) + recordBytes(saved);
      if (pinnedAssets) {
        const blobs = tx.objectStore("blobs");
        for (const key of new Set<string>(job.runtimeBlobs || [])) {
          if ((await result(blobs.getKey(key))) !== undefined) continue;
          const blob = pinnedAssets.get(key);
          const expected = Object.values(job.manifest.assets || {}).find(
            (asset) => `asset:${asset.sha256}` === key,
          );
          if (!blob || !expected || expected.size !== blob.size)
            throw new Error(
              "A pinned package engine asset is missing. Reload and retry the save.",
            );
          bytes += blob.size;
          checkBudget(bytes);
          const created = Date.now();
          blobs.put({ id: key, blob, created });
          await trackBlob(
            tx.objectStore("meta"),
            key,
            blob.size,
            created,
            undefined,
          );
        }
      }
      checkBudget(bytes);
      await dirtyReferences(
        tx.objectStore("meta"),
        references(old),
        references(saved),
      );
      await syncSummary(tx.objectStore("meta"), saved.id, saved);
      tx.objectStore("meta").put({ id: "usage", bytes });
      tx.objectStore("jobs").put(saved);
    },
  );
  changed();
}
export function id() {
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) =>
    b.toString(16).padStart(2, "0"),
  ).join("");
}
async function fence(tx: IDBTransaction, token: string | null) {
  if (!token) return;
  const lease = await result(tx.objectStore("meta").get("lease"));
  if (!lease || lease.token !== token || lease.until < Date.now())
    throw new Error(
      "This build attempt was interrupted or replaced. Resume the saved batch.",
    );
}
export async function acquire(job: string) {
  const token = id();
  await transaction(["meta"], "readwrite", async (tx) => {
    const s = tx.objectStore("meta"),
      old = await result(s.get("lease"));
    if (old && old.until > Date.now())
      throw new Error(
        "Another package build is running in this browser. Finish or cancel it first.",
      );
    s.put({ id: "lease", job, token, until: Date.now() + 120000 });
  });
  return token;
}
export async function heartbeat(token: string) {
  return transaction(["meta"], "readwrite", async (tx) => {
    await fence(tx, token);
    const s = tx.objectStore("meta"),
      lease = await result(s.get("lease"));
    s.put({ ...lease, until: Date.now() + 120000 });
  });
}
export async function release(token: string) {
  await transaction(["meta"], "readwrite", async (tx) => {
    const s = tx.objectStore("meta");
    if ((await result(s.get("lease")))?.token === token) s.delete("lease");
  });
  changed();
}
export const lease = () =>
  transaction(["meta"], "readonly", (tx) =>
    result(tx.objectStore("meta").get("lease")),
  );
export async function removeJob(recordId: string) {
  await transaction(["jobs", "meta"], "readwrite", async (tx) => {
    const active = await result(tx.objectStore("meta").get("lease"));
    if (active?.job === recordId && active.until > Date.now())
      throw new Error("Cancel this build before deleting it.");
    const meta = tx.objectStore("meta");
    const old = await result(tx.objectStore("jobs").get(recordId));
    const usage = (await result(meta.get("usage")))?.bytes || 0;
    await dirtyReferences(meta, references(old), new Set());
    await syncSummary(meta, recordId);
    tx.objectStore("jobs").delete(recordId);
    meta.put({ id: `deleted:${recordId}` });
    meta.put({ id: "usage", bytes: Math.max(0, usage - recordBytes(old)) });
  });
  changed();
}

function protectedAttempt(key: string, active: any) {
  return (
    active?.until > Date.now() &&
    key.startsWith(`job:${active.job}:`) &&
    key.includes(active.token)
  );
}
async function referencedCandidates(
  tx: IDBTransaction,
  candidates: Set<string>,
) {
  const keep = new Set<string>();
  // Summary reference lists are advisory. This scan shares the deletion
  // transaction so old clients and concurrent saves cannot race the check.
  await visitJobs(tx.objectStore("jobs"), (job) => {
    for (const ref of references(job)) if (candidates.has(ref)) keep.add(ref);
  });
  return keep;
}

async function cleanupDue(tx: IDBTransaction, now: number) {
  const meta = tx.objectStore("meta");
  const due = await rows<{ blob: string }>(
    meta,
    IDBKeyRange.bound(["blob-expiry"], ["blob-expiry", now, []]),
    100,
  );
  if (!due.length) return [];
  const candidates = new Set(due.map(({ value }) => value.blob));
  const keep = await referencedCandidates(tx, candidates);
  const active = await result(meta.get("lease"));
  const blobs = tx.objectStore("blobs");
  const deleted: string[] = [];
  let usage = (await result(meta.get("usage")))?.bytes || 0;
  for (const key of candidates) {
    const value = await result(blobs.get(key));
    if (!value) {
      await forgetBlob(meta, key);
      continue;
    }
    if (keep.has(key)) {
      await trackBlob(meta, key, value.blob.size, value.created, undefined);
      continue;
    }
    const next = Math.max(
      expires(key, value.created || 0),
      protectedAttempt(key, active) ? active.until : 0,
    );
    if (next > now) {
      await trackBlob(meta, key, value.blob.size, value.created, next);
      continue;
    }
    blobs.delete(key);
    await forgetBlob(meta, key);
    usage -= value.blob.size;
    deleted.push(key);
  }
  meta.put({ id: "usage", bytes: Math.max(0, usage) });
  return deleted;
}

async function reconcileStorage(tx: IDBTransaction, now: number) {
  const meta = tx.objectStore("meta");
  const keep = new Set<string>();
  let usage = 0;
  await visitJobs(tx.objectStore("jobs"), async (job) => {
    usage += recordBytes(job);
    for (const ref of references(job)) keep.add(ref);
    await syncSummary(meta, job.id, job);
  });
  const summaries = await rows<{ summary: JobSummary }>(
    meta,
    range(["job-summary-id"]),
    Infinity,
  );
  for (const { value } of summaries) {
    if (
      (await result(tx.objectStore("jobs").getKey(value.summary.id))) ===
      undefined
    )
      await syncSummary(meta, value.summary.id);
  }
  meta.put({ id: SUMMARY_BACKFILL, complete: true });
  const active = await result(meta.get("lease"));
  // Rebuild only cleanup metadata. No database upgrade and no changes to
  // jobs, manifests, snapshots, tombstones, or the build lease.
  meta.delete(range(["blob-info"]));
  meta.delete(range(["blob-expiry"]));
  const deleted: string[] = [];
  await new Promise<void>((resolve, reject) => {
    const cursor = tx.objectStore("blobs").openCursor();
    cursor.onerror = () => reject(cursor.error);
    cursor.onsuccess = () => {
      const c = cursor.result;
      if (!c) return resolve();
      try {
        const key = String(c.key);
        const value = c.value;
        const due = keep.has(key)
          ? undefined
          : Math.max(
              expires(key, value.created || 0),
              protectedAttempt(key, active) ? active.until : 0,
            );
        if (due !== undefined && due <= now) {
          c.delete();
          deleted.push(key);
        } else {
          usage += value.blob.size;
          meta.put({
            id: blobKey(key),
            size: value.blob.size,
            created: value.created,
            due,
          });
          if (due !== undefined)
            meta.put({ id: ["blob-expiry", due, key], blob: key });
        }
        c.continue();
      } catch (error) {
        reject(error);
      }
    };
  });
  meta.put({ id: "usage", bytes: usage });
  meta.put({ id: "maintenance:reconciled", at: now });
  return deleted;
}

let cleaning: Promise<void> | undefined;
/** Incremental dirty/expiry work, with an occasional authoritative sweep. */
export async function cleanup({ reconcile = false } = {}) {
  if (cleaning) return cleaning;
  cleaning = (async () => {
    const token = id();
    const claimed = await transaction(["meta"], "readwrite", async (tx) => {
      const meta = tx.objectStore("meta");
      const active = await result(meta.get(MAINTENANCE_LEASE));
      if (active?.until > Date.now()) return false;
      const last = await result(meta.get("maintenance:reconciled"));
      const usage = await result(meta.get("usage"));
      const now = Date.now();
      if (
        !reconcile &&
        last?.at + RECONCILE_INTERVAL > now &&
        usage?.bytes < BUDGET
      ) {
        const due = await result(
          meta.getKey(
            IDBKeyRange.bound(["blob-expiry"], ["blob-expiry", now, []]),
          ),
        );
        if (due === undefined) return false;
      }
      meta.put({ id: MAINTENANCE_LEASE, token, until: Date.now() + 120000 });
      return true;
    });
    if (!claimed) return;
    try {
      const deleted = await transaction(
        ["jobs", "blobs", "meta"],
        "readwrite",
        async (tx) => {
          const meta = tx.objectStore("meta");
          const active = await result(meta.get(MAINTENANCE_LEASE));
          if (active?.token !== token || active.until <= Date.now()) return [];
          const now = Date.now();
          const last = await result(meta.get("maintenance:reconciled"));
          const usage = await result(meta.get("usage"));
          const keys =
            reconcile ||
            !last ||
            last.at + RECONCILE_INTERVAL <= now ||
            usage?.bytes >= BUDGET
              ? await reconcileStorage(tx, now)
              : await cleanupDue(tx, now);
          meta.delete(MAINTENANCE_LEASE);
          return keys;
        },
      );
      if (deleted.length) changed(deleted);
    } finally {
      await transaction(["meta"], "readwrite", async (tx) => {
        const meta = tx.objectStore("meta");
        if ((await result(meta.get(MAINTENANCE_LEASE)))?.token === token)
          meta.delete(MAINTENANCE_LEASE);
      });
    }
  })().finally(() => {
    cleaning = undefined;
  });
  return cleaning;
}

async function nextMaintenance() {
  return transaction(["meta"], "readonly", async (tx) => {
    const meta = tx.objectStore("meta");
    const active = await result(meta.get(MAINTENANCE_LEASE));
    if (active?.until > Date.now()) return active.until;
    const last = await result(meta.get("maintenance:reconciled"));
    const [first] = await rows<{ id: [string, number, string] }>(
      meta,
      range(["blob-expiry"]),
      1,
    );
    return Math.min(
      last ? last.at + RECONCILE_INTERVAL : 0,
      first?.value.id[1] ?? Infinity,
    );
  });
}
let stopScheduler: (() => void) | undefined;
/** Opt in from lifecycle. Importing this module never starts maintenance. */
export function startCleanupScheduler(): () => void {
  if (stopScheduler) return stopScheduler;
  const started = Date.now();
  let startupPending = true;
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let idleCallback: number | undefined;
  let running = false;
  let dirty = false;
  const cancelScheduled = () => {
    clearTimeout(timer);
    timer = undefined;
    if (idleCallback !== undefined)
      globalThis.cancelIdleCallback?.(idleCallback);
    idleCallback = undefined;
  };
  const schedule = (delay: number) => {
    if (stopped) return;
    cancelScheduled();
    timer = setTimeout(
      () => {
        timer = undefined;
        if (typeof globalThis.requestIdleCallback === "function")
          idleCallback = globalThis.requestIdleCallback(
            () => {
              idleCallback = undefined;
              void run();
            },
            { timeout: 5000 },
          );
        else void run();
      },
      Math.max(250, delay),
    );
  };
  const run = async () => {
    if (stopped || running) return;
    running = true;
    dirty = false;
    let next = Date.now() + 60000;
    try {
      if (startupPending) startupPending = !(await reconcileStartup(started));
      if (!startupPending) await cleanup();
      next = await nextMaintenance();
      // Also retry if the owner closed without broadcasting completion.
      if (startupPending) next = Math.min(next, Date.now() + 1000);
    } catch {
      /* Retry storage failures without interrupting an edit. */
    } finally {
      running = false;
    }
    schedule(dirty ? 250 : next - Date.now());
  };
  wakeMaintenance = () => {
    if (running) dirty = true;
    else schedule(250);
  };
  const onVisible = () => {
    if (document.visibilityState === "visible") wakeMaintenance?.();
  };
  if (typeof document !== "undefined")
    document.addEventListener("visibilitychange", onVisible);
  const stop = () => {
    if (stopped) return;
    stopped = true;
    cancelScheduled();
    wakeMaintenance = undefined;
    stopScheduler = undefined;
    if (typeof document !== "undefined")
      document.removeEventListener("visibilitychange", onVisible);
  };
  stopScheduler = stop;
  schedule(250);
  return stop;
}
export { sha256 } from "./sha256.js";

export async function interrupt(token: string) {
  await transaction(["jobs", "meta"], "readwrite", async (tx) => {
    const meta = tx.objectStore("meta");
    const activeLease = await result(meta.get("lease"));
    if (!activeLease || activeLease.token !== token) return;
    const jobs = tx.objectStore("jobs");
    const job = await result(jobs.get(activeLease.job));
    if (job?.state === "building") {
      const saved = { ...job, state: "interrupted" };
      jobs.put(saved);
      await syncSummary(meta, job.id, saved);
      const usage = (await result(meta.get("usage")))?.bytes || 0;
      const bytes = usage - recordBytes(job) + recordBytes(saved);
      checkBudget(bytes);
      meta.put({ id: "usage", bytes });
    }
    meta.delete("lease");
  });
  changed();
}
