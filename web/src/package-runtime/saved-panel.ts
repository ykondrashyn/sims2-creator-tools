import { required } from "../shared/dom.js";
import { asset, fetchFile, manifest } from "./assets.js";
import { hashBlob } from "./hashing.js";
import * as store from "./store.js";
import type { JobEvents, JobKind, SavedJob } from "./types.js";
export interface SavedPanelApi {
  restore: (id: string) => Promise<SavedJob>;
  download: (id: string) => Promise<void>;
  events: JobEvents;
  onDeleted: (id: string) => void;
}
export function createSavedPanel(
  api: SavedPanelApi,
  container: HTMLElement,
  kind: JobKind,
  onOpen: (job: SavedJob) => unknown,
) {
  const { restore, download, events, onDeleted } = api;
  if (!document.getElementById("package-license-notices")) {
    const details = document.createElement("details");
    details.id = "package-license-notices";
    details.className = "saved-batches";
    const summary = document.createElement("summary");
    summary.textContent = "Package engine source and licenses";
    const notices = document.createElement("pre");
    notices.style.whiteSpace = "pre-wrap";
    const button = document.createElement("button");
    button.type = "button";
    button.className = "secondary";
    button.textContent = "Download corresponding source";
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        const m = await manifest();
        const blob = await (await fetchFile(m.assets.source.url)).blob();
        if ((await hashBlob(blob)) !== m.assets.source.sha256)
          throw new Error(
            "Source archive integrity check failed. Reload and retry.",
          );
        const link = document.createElement("a");
        link.href = URL.createObjectURL(blob);
        link.download = "sims2-package-engine-source.tar.gz";
        details.append(link);
        link.click();
        setTimeout(() => {
          URL.revokeObjectURL(link.href);
          link.remove();
        }, 30000);
      } catch (errorCause) {
        const error =
          errorCause instanceof Error
            ? errorCause
            : new Error(String(errorCause));
        notices.textContent = error.message;
      } finally {
        button.disabled = false;
      }
    });
    details.addEventListener("toggle", async () => {
      if (!details.open || notices.textContent) return;
      try {
        const m = await manifest();
        notices.textContent = await (await asset(m, "licenses")).text();
      } catch (errorCause) {
        const error =
          errorCause instanceof Error
            ? errorCause
            : new Error(String(errorCause));
        notices.textContent = error.message;
      }
    });
    details.append(summary, button, notices);
    required(
      document.querySelector("main"),
      'document.querySelector("main")',
    ).append(details);
  }
  const details = document.createElement("details");
  details.className = "saved-batches";
  const summary = document.createElement("summary");
  const savedLabel =
    kind === "sim"
      ? "Saved Sims"
      : kind === "painting"
        ? "Saved paintings"
        : kind === "conversion"
          ? "Saved conversions"
          : "Saved batches";
  summary.textContent = savedLabel;
  const note = document.createElement("p");
  note.className = "build-note";
  note.textContent =
    "Saved in this browser at this address until you delete them. Clearing site data, browser eviction or private browsing can remove saved work. Close the browser to stop a build, then reopen the batch to resume.";
  const list = document.createElement("div");
  const more = document.createElement("button");
  more.type = "button";
  more.className = "secondary";
  more.textContent = "Load more";
  more.hidden = true;
  details.append(summary, note, list, more);
  container.append(details);
  const rows = new Map<
    string,
    { element: HTMLElement; label: HTMLElement; download: HTMLButtonElement }
  >();
  let pageSize = 25;
  let cursor: store.JobSummaryCursor | undefined;
  let invalidated = true;
  let scheduled: Promise<void> | undefined;
  let rerun = false;
  let reconcileAll = false;
  const reconcileIds = new Set<string>();

  function makeRow(id: string) {
    const element = document.createElement("div");
    element.className = "saved-batch-row";
    const label = document.createElement("span");
    element.append(label);
    let downloadButton!: HTMLButtonElement;
    for (const action of ["Open", "Download", "Delete"]) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "secondary";
      button.textContent = action;
      if (action === "Download") downloadButton = button;
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          if (action === "Open") await onOpen(await restore(id));
          else if (action === "Download") await download(id);
          else {
            await store.removeJob(id);
            onDeleted(id);
          }
          await refresh();
        } catch (error) {
          showError(error);
        } finally {
          button.disabled = false;
        }
      });
      element.append(button);
    }
    return { element, label, download: downloadButton };
  }
  function showError(error: unknown) {
    note.textContent = error instanceof Error ? error.message : String(error);
  }
  function render(jobs: store.JobSummary[]) {
    const keep = new Set(jobs.map((job) => job.id));
    for (const [id, row] of rows) {
      if (!keep.has(id)) {
        row.element.remove();
        rows.delete(id);
      }
    }
    if (!jobs.length) {
      list.textContent =
        kind === "conversion"
          ? "No saved conversions yet."
          : "No saved batches yet.";
      return;
    }
    if (!rows.size) list.replaceChildren();
    jobs.forEach((job, index) => {
      let row = rows.get(job.id);
      if (!row) {
        row = makeRow(job.id);
        rows.set(job.id, row);
      }
      const text = `${job.label} · ${job.state}${job.outputSize !== undefined ? ` · ${(job.outputSize / 1024 ** 2).toFixed(1)} MiB` : ""}`;
      if (row.label.textContent !== text) row.label.textContent = text;
      row.download.hidden = job.state !== "complete";
      const current = list.children.item(index);
      if (current !== row.element) list.insertBefore(row.element, current);
    });
  }
  async function update() {
    const ids = [...reconcileIds];
    reconcileIds.clear();
    if (reconcileAll) {
      reconcileAll = false;
      await store.reconcileJobSummaries();
    } else {
      for (const id of ids) await store.reconcileJobSummary(id);
    }
    const count = await store.countJobSummaries(kind);
    summary.textContent = `${savedLabel} (${count})`;
    if (!details.open) return;
    // Keep already loaded pages visible, with a stable id tie-breaker when
    // timestamps match. Reloading summaries never clones full saved jobs.
    const jobs: store.JobSummary[] = [];
    let next: store.JobSummaryCursor | undefined;
    do {
      const page = await store.listJobSummaries(kind, {
        cursor: next,
        limit: Math.min(25, pageSize - jobs.length),
      });
      jobs.push(...page.items);
      next = page.cursor;
    } while (next && jobs.length < pageSize);
    if (!details.open || rerun) return;
    cursor = next;
    render(jobs);
    more.hidden = !cursor;
    invalidated = false;
  }
  function refresh() {
    invalidated = true;
    if (scheduled) {
      rerun = true;
      return scheduled;
    }
    scheduled = new Promise<void>((resolve) => setTimeout(resolve, 0))
      .then(async () => {
        do {
          rerun = false;
          await update();
        } while (rerun);
      })
      .catch(showError)
      .finally(() => {
        scheduled = undefined;
      });
    return scheduled;
  }
  more.addEventListener("click", async () => {
    if (!cursor || more.disabled) return;
    more.disabled = true;
    pageSize += 25;
    try {
      await refresh();
    } finally {
      more.disabled = false;
    }
  });
  details.addEventListener("toggle", () => {
    if (details.open && invalidated) void refresh();
  });
  events.addEventListener("persisted", (event: Event) => {
    const job = (event as CustomEvent<SavedJob>).detail;
    if (job?.kind === kind) void refresh();
  });
  events.addEventListener("deleted", () => {
    void refresh();
  });
  store.storageEvents.addEventListener("summaries-reconciled", () => {
    void refresh();
  });
  events.addEventListener("refresh", (event) => {
    const id = (event as CustomEvent<unknown>).detail;
    if (typeof id === "string") reconcileIds.add(id);
    else reconcileAll = true;
    void refresh();
  });
  void refresh();
  return refresh;
}
