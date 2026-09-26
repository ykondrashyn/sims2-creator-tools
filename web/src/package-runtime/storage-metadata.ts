import type { JobKind, JobState, SavedJob } from "./types.js";

// Compound primary keys provide ordered metadata queries in the existing v1
// meta store. Older clients can continue opening the same database and jobs.
export interface JobSummary {
  id: string;
  kind: JobKind;
  revision: number;
  state: JobState;
  label?: string;
  updated: number;
  outputSize?: number;
}
export type JobSummaryCursor = [number, string];
export interface JobSummaryPage {
  items: JobSummary[];
  cursor?: JobSummaryCursor;
}
export const request = <T>(req: IDBRequest<T>) =>
  new Promise<T>((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
export const range = (prefix: IDBValidKey[]) =>
  IDBKeyRange.bound(prefix, [...prefix, []]);
export const recordBytes = (value: unknown) =>
  value ? new TextEncoder().encode(JSON.stringify(value)).length : 0;
export function references(job?: SavedJob): Set<string> {
  const refs = new Set<string>();
  const add = (value: unknown) => {
    if (typeof value === "string") refs.add(value);
  };
  for (const file of job?.files || []) add(file.blob);
  add(job?.modelSource?.blob);
  for (const ref of job?.runtimeBlobs || []) add(ref);
  for (const checkpoint of job?.checkpoints || []) add(checkpoint.blob);
  for (const part of job?.output?.parts || []) add(part);
  return refs;
}
export function project(job: SavedJob): JobSummary {
  return {
    id: job.id,
    kind: job.kind,
    revision: job.revision,
    state: job.state,
    label: job.label,
    updated: Number.isFinite(job.updated) ? job.updated : 0,
    ...(job.output ? { outputSize: job.output.size } : {}),
  };
}
export const summaryKey = (summary: JobSummary): IDBValidKey[] => [
  "job-summary",
  summary.kind,
  -summary.updated,
  summary.id,
];
export const blobKey = (id: string): IDBValidKey[] => ["blob-info", id];
export const expiryKey = (id: string, due: number): IDBValidKey[] => [
  "blob-expiry",
  due,
  id,
];
export const temporaryBlob = (id: string) =>
  id.startsWith("job:") && (id.includes(":zip:") || id.includes(":package:"));
export const expires = (id: string, created: number) =>
  temporaryBlob(id) ? created : created + 600000;

export async function trackBlob(
  meta: IDBObjectStore,
  id: string,
  size: number,
  created: number,
  due: number | undefined,
) {
  const key = blobKey(id);
  const old = await request(meta.get(key));
  if (old?.due !== undefined) meta.delete(expiryKey(id, old.due));
  meta.put({ id: key, size, created, due });
  if (due !== undefined) meta.put({ id: expiryKey(id, due), blob: id });
}
export async function forgetBlob(meta: IDBObjectStore, id: string) {
  const old = await request(meta.get(blobKey(id)));
  if (old?.due !== undefined) meta.delete(expiryKey(id, old.due));
  meta.delete(blobKey(id));
}
export async function dirtyReferences(
  meta: IDBObjectStore,
  oldRefs: Iterable<string>,
  nextRefs: Set<string>,
) {
  for (const ref of oldRefs) {
    if (nextRefs.has(ref)) continue;
    const old = await request(meta.get(blobKey(ref)));
    // Legacy blobs may have no metadata yet. Cleanup always reads the actual
    // blob and actual job references before deciding whether to delete it.
    await trackBlob(meta, ref, old?.size || 0, old?.created || 0, Date.now());
  }
}
async function changeCount(meta: IDBObjectStore, kind: JobKind, delta: number) {
  const id = ["job-summary-count", kind];
  const old = await request(meta.get(id));
  meta.put({ id, count: Math.max(0, (old?.count || 0) + delta) });
}
export async function syncSummary(
  meta: IDBObjectStore,
  id: string,
  job?: SavedJob,
): Promise<JobSummary | undefined> {
  const key = ["job-summary-id", id];
  const old = await request(meta.get(key));
  const summary = job ? project(job) : undefined;
  const refs = references(job);
  await dirtyReferences(meta, old?.refs || [], refs);
  if (old?.summary && old.summary.kind !== summary?.kind) {
    meta.delete(summaryKey(old.summary));
    await changeCount(meta, old.summary.kind, -1);
  }
  if (!summary) {
    meta.delete(key);
    return undefined;
  }
  if (old?.summary?.kind !== summary.kind)
    await changeCount(meta, summary.kind, 1);
  if (old?.summary && old.summary.kind === summary.kind)
    meta.delete(summaryKey(old.summary));
  meta.put({ id: summaryKey(summary), summary });
  meta.put({ id: key, summary, refs: [...refs] });
  return summary;
}

// Requests are issued from cursor callbacks, with no timer/network awaits
// inside a transaction. Only the requested batch is cloned into memory.
export function rows<T>(
  store: IDBObjectStore,
  query: IDBKeyRange | undefined,
  limit: number,
): Promise<{ key: IDBValidKey; value: T }[]> {
  return new Promise((resolve, reject) => {
    const values: { key: IDBValidKey; value: T }[] = [];
    const cursor = store.openCursor(query);
    cursor.onerror = () => reject(cursor.error);
    cursor.onsuccess = () => {
      const c = cursor.result;
      if (!c || values.length === limit) return resolve(values);
      values.push({ key: c.key, value: c.value });
      c.continue();
    };
  });
}

export function visitJobs(
  jobs: IDBObjectStore,
  visit: (job: SavedJob) => void | Promise<void>,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const cursor = jobs.openCursor();
    cursor.onerror = () => reject(cursor.error);
    cursor.onsuccess = () => {
      const c = cursor.result;
      if (!c) return resolve();
      try {
        void Promise.resolve(visit(c.value))
          .then(() => c.continue())
          .catch(reject);
      } catch (error) {
        reject(error);
      }
    };
  });
}
