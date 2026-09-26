import { hashBlob } from "./hashing.js";
import * as store from "./store.js";
import { validateSavedJob } from "./validation.js";
export async function prepareDownload(id: string) {
  const job = await store.getJob(id);
  validateSavedJob(job);
  if (job.state !== "complete" || !job.output)
    throw new Error("This batch has no complete validated download.");
  const blobs = [];
  for (const [index, key] of job.output.parts.entries()) {
    const b = await store.getBlob(key);
    if (!b)
      throw new Error(
        "Saved download is missing. Browser storage may have been cleared.",
      );
    if (
      !job.output.hashes?.[index] ||
      (await hashBlob(b)) !== job.output.hashes[index]
    )
      throw new Error(
        "Saved download is damaged. Your source files and validation report are retained.",
      );
    blobs.push(b);
  }
  const blob = new Blob(blobs, { type: job.output.type });
  if (blob.size !== job.output.size)
    throw new Error("Saved download is incomplete.");
  return { url: URL.createObjectURL(blob), filename: job.output.filename };
}
export async function download(id: string) {
  const ready = await prepareDownload(id);
  const a = document.createElement("a");
  a.href = ready.url;
  a.download = ready.filename;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(ready.url), 30000);
}
export async function bindDownload(anchor: HTMLAnchorElement, id: string) {
  anchor.dataset.job = id;
  anchor.hidden = true;
  if (anchor.href.startsWith("blob:")) URL.revokeObjectURL(anchor.href);
  let ready;
  try {
    ready = await prepareDownload(id);
  } catch (errorCause) {
    const error =
      errorCause instanceof Error ? errorCause : new Error(String(errorCause));
    if (anchor.dataset.job === id) anchor.dataset.job = "";
    throw error;
  }
  if (anchor.dataset.job !== id) {
    URL.revokeObjectURL(ready.url);
    return;
  }
  anchor.href = ready.url;
  anchor.download = ready.filename;
  anchor.hidden = false;
}
