import { hashBlob } from "./hashing.js";
import { put } from "./session.js";
import * as store from "./store.js";
import type { FileInput, StoredFile } from "./types.js";
const fileHashes = new WeakMap<File, string>();
export async function saveFiles(files: (File | FileInput)[]) {
  if (
    files.length > 64 ||
    files.reduce((n, f) => n + ("file" in f ? f.file : f).size, 0) >
      128 * 1024 ** 2 ||
    files.some((f) => ("file" in f ? f.file : f).size > 64 * 1024 ** 2)
  )
    throw new Error("Choose up to 64 files, 64 MiB each and 128 MiB total.");
  const result: StoredFile[] = [];
  for (const entry of files) {
    const file = "file" in entry ? entry.file : entry,
      name = entry.name || file.name;
    let hash = fileHashes.get(file);
    if (!hash) {
      hash = await hashBlob(file);
      fileHashes.set(file, hash);
    }
    const key = `input:${hash}`;
    if (!(await store.getBlob(key))) await store.putBlob(key, file);
    result.push({
      name,
      filename: file.name || name,
      blob: key,
      sha256: hash,
      size: file.size,
    });
  }
  return result;
}
export async function loadFiles(record: {
  files: StoredFile[];
  id?: string;
  revision?: number;
}) {
  for (const f of record.files || []) {
    const blob = await store.getBlob(f.blob);
    if (!blob || blob.size !== f.size || (await hashBlob(blob)) !== f.sha256)
      throw new Error(
        `Saved input ${f.filename} is missing or damaged. Select the source files in a new batch.`,
      );
    await put(f.name, blob, record);
  }
}
