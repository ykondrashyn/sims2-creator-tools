import { runtimeManifestUrl } from "../deployment.js";
import { hashBlob } from "./hashing.js";
import * as store from "./store.js";
import type { RuntimeManifest } from "./types.js";
let currentManifest: RuntimeManifest | undefined;
let pendingManifest: Promise<RuntimeManifest> | undefined;
const CACHE_BYTES = 64 * 1024 ** 2;
const verified = new Map<string, Blob>();
const loading = new Map<string, Promise<Blob>>();
let cachedBytes = 0;
let generation = 0;

export function clearAssetCache() {
  generation++;
  verified.clear();
  loading.clear();
  cachedBytes = 0;
}

store.storageEvents.addEventListener("blobs-deleted", (event) => {
  const keys = new Set((event as CustomEvent<string[]>).detail);
  if (![...keys].some((key) => key.startsWith("asset:"))) return;
  generation++;
  for (const [key, blob] of verified) {
    if (!keys.has(key.slice(0, key.lastIndexOf(":")))) continue;
    verified.delete(key);
    cachedBytes -= blob.size;
  }
  for (const key of loading.keys())
    if (keys.has(key.slice(0, key.lastIndexOf(":")))) loading.delete(key);
});

function remember(key: string, blob: Blob) {
  const previous = verified.get(key);
  if (previous) cachedBytes -= previous.size;
  verified.delete(key);
  if (blob.size <= CACHE_BYTES) {
    verified.set(key, blob);
    cachedBytes += blob.size;
  }
  while (cachedBytes > CACHE_BYTES) {
    const oldest = verified.entries().next().value!;
    verified.delete(oldest[0]);
    cachedBytes -= oldest[1].size;
  }
}
export async function fetchFile(url: string) {
  const r = await fetch(url);
  if (!r.ok)
    throw new Error("Package assets could not be loaded. Reconnect and retry.");
  return r;
}
export async function manifest({
  refresh = false,
} = {}): Promise<RuntimeManifest> {
  if (pendingManifest) return pendingManifest;
  if (currentManifest && !refresh) return currentManifest;
  pendingManifest = (async () => {
    const next: RuntimeManifest = await (
      await fetchFile(runtimeManifestUrl())
    ).json();
    if (next?.schema_version !== 1 || next.protocol_version !== 1)
      throw new Error(
        "Unsupported package engine release. Reload the website.",
      );
    currentManifest = next;
    return next;
  })().finally(() => {
    pendingManifest = undefined;
  });
  return pendingManifest;
}
export async function asset(m: RuntimeManifest, name: string) {
  const a = m.assets[name];
  if (!a) throw new Error(`Saved engine asset ${name} is unavailable.`);
  const key = `asset:${a.sha256}`;
  const cacheKey = `${key}:${a.size}`;
  const cached = verified.get(cacheKey);
  if (cached) {
    remember(cacheKey, cached);
    return cached;
  }
  const existing = loading.get(cacheKey);
  if (existing) return existing;
  const started = generation;
  const request = (async () => {
    const stored = await store.getBlob(key);
    const blob = stored || (await (await fetchFile(a.url)).blob());
    if (blob.size !== a.size || (await hashBlob(blob)) !== a.sha256) {
      clearAssetCache();
      throw new Error(
        stored
          ? "Saved engine assets are damaged. This batch has been kept. Restore a browser backup or start a new batch from your original inputs."
          : "Package asset integrity check failed. Reload the website.",
      );
    }
    if (!stored) await store.putBlob(key, blob);
    if (started === generation) remember(cacheKey, blob);
    return blob;
  })();
  loading.set(cacheKey, request);
  try {
    return await request;
  } finally {
    if (loading.get(cacheKey) === request) loading.delete(cacheKey);
  }
}
