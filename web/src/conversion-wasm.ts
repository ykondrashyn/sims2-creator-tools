import { createAutosaveQueue } from "./shared/autosave.js";
import * as runtime from "./package-runtime/client.js";
import { errorMessage, type SavedJob } from "./package-runtime/types.js";

export function attachConversion(
  showPreview: (arg0: Blob | undefined, arg1: any, arg2: any) => any,
) {
  const form = document.querySelector<HTMLFormElement>("#converter-form")!,
    input = document.querySelector<HTMLInputElement>("#converter-texture")!,
    button = document.querySelector<HTMLButtonElement>("#convert-button")!,
    status = document.querySelector<HTMLElement>("#conversion-status")!,
    progress = document.querySelector<HTMLProgressElement>(
      "#conversion-progress",
    )!,
    cancel = document.querySelector<HTMLButtonElement>("#conversion-cancel")!,
    retry = document.querySelector<HTMLButtonElement>("#conversion-retry")!,
    fresh = document.querySelector<HTMLButtonElement>("#conversion-new")!,
    download = document.querySelector<HTMLAnchorElement>(
      "#conversion-download",
    )!,
    saved = document.querySelector<HTMLElement>("#conversion-file-name")!;
  let record: SavedJob | null = null,
    saving = Promise.resolve(),
    edit = 0,
    autoDownload: string | null = null,
    rendered: string | null = null,
    downloadRevision = 0;
  const body = () =>
    form.querySelector<HTMLInputElement>(
      'input[name="converter-preset"]:checked',
    )!.value;
  function message(text: string | null, error = false) {
    status.hidden = false;
    status.textContent = text;
    status.classList.toggle("error", error);
  }
  function lock(yes: boolean) {
    input.disabled = yes;
    form
      .querySelectorAll<HTMLInputElement>('input[name="converter-preset"]')
      .forEach((n) => (n.disabled = yes));
  }
  function filename(file: File, gender: string) {
    const basename = file.name.replaceAll("\\", "/").split("/").pop() || "";
    const dot = basename.lastIndexOf(".");
    const stem =
      (dot > 0 ? basename.slice(0, dot) : basename)
        .replace(/[^A-Za-z0-9._-]+/g, "_")
        .replace(/^[._]+|[._]+$/g, "") || "texture";
    return `${stem}_${gender}_ts2.png`;
  }
  async function show(job: SavedJob) {
    record = job;
    const running = job.state === "building",
      frozen = !!job.snapshotHash || running || job.state === "complete";
    lock(frozen);
    button.hidden = frozen;
    button.disabled = running;
    fresh.disabled = running;
    cancel.hidden = !running;
    retry.hidden = !["failed", "cancelled", "interrupted"].includes(job.state);
    retry.textContent =
      job.state === "interrupted" ? "Resume conversion" : "Retry";
    progress.hidden = !running;
    progress.value = typeof job.progress === "number" ? job.progress : 0;
    input.required = !job.files?.length;
    input.closest("label")!.hidden = frozen;
    saved.textContent = job.files?.[0]?.filename || "";
    saved.hidden =
      !saved.textContent || (!frozen && (input.files?.length || 0) > 0);
    if (running) message(`Converting in your browser: ${progress.value}%`);
    else if (job.state === "complete") {
      message("Conversion ready. Saved in this browser.");
      const id = job.id,
        revision = ++downloadRevision;
      try {
        const result = await runtime.prepareDownload(id);
        if (revision !== downloadRevision || record?.id !== id) {
          URL.revokeObjectURL(result.url);
          return;
        }
        if (download.dataset.url) URL.revokeObjectURL(download.dataset.url);
        download.dataset.url = result.url;
        download.href = result.url;
        download.download = result.filename;
        download.hidden = false;
        if (autoDownload === id) {
          autoDownload = null;
          download.click();
        }
        if (rendered !== id) {
          const blob = await runtime.store.getBlob(job.output!.parts[0]);
          if (revision !== downloadRevision || record?.id !== id) return;
          rendered = id;
          await showPreview(blob, job.parameters.body, result.filename);
        }
      } catch (e) {
        message(errorMessage(e), true);
      }
    } else
      message(
        job.error?.message || "Saved in this browser. Ready to convert.",
        !!job.error,
      );
  }
  const autosaves = createAutosaveQueue<void>();
  function saveEdit() {
    const revision = ++edit,
      file = input.files?.[0],
      gender = body();
    if (record?.snapshotHash || record?.state === "complete") return saving;
    button.disabled = true;
    message("Checking and saving locally…");
    saving = autosaves
      .enqueue(async () => {
        if (revision !== edit) return;
        const existing = record;
        const blob =
          file ||
          (existing?.files[0] &&
            (await runtime.store.getBlob(existing.files[0].blob)));
        if (!blob) {
          message("Choose a texture PNG.");
          return;
        }
        const selected =
          file ||
          new File([blob], existing!.files[0].filename, { type: "image/png" });
        const manifest = existing?.manifest || (await runtime.manifest());
        await runtime.validateConversion(selected, manifest);
        if (revision !== edit) return;
        const files = await runtime.saveFiles([
          { name: "conversion-input", file: selected },
        ]);
        const next = await runtime.saveDraft({
          ...existing,
          kind: "conversion",
          manifest,
          revision: (existing?.revision || 0) + 1,
          state: "draft",
          files,
          template: {
            id: gender,
            asset: manifest.conversion.profiles[gender].asset,
          },
          parameters: { body: gender, filename: filename(selected, gender) },
        });
        if (revision !== edit) return;
        await show(next);
      })
      .catch((e) => {
        if (revision === edit)
          message(`Could not save conversion: ${errorMessage(e)}`, true);
        throw e;
      })
      .finally(() => {
        if (revision === edit) button.disabled = false;
      });
    saving.catch(() => {});
    return saving;
  }
  input.addEventListener("change", () => {
    void saveEdit();
  });
  form
    .querySelectorAll<HTMLInputElement>('input[name="converter-preset"]')
    .forEach((n) =>
      n.addEventListener("change", () => {
        if (input.files?.[0] || record) void saveEdit();
      }),
    );
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    button.disabled = true;
    try {
      await saveEdit();
      if (!record) throw new Error("Choose a texture PNG first.");
      autoDownload = record.id;
      await runtime.start(record);
    } catch (e) {
      message(errorMessage(e), true);
      button.disabled = false;
    }
  });
  retry.addEventListener("click", async () => {
    retry.disabled = true;
    try {
      await saving;
      if (!record) return;
      autoDownload = record.id;
      await runtime.start(record);
    } catch (e) {
      message(errorMessage(e), true);
    } finally {
      retry.disabled = false;
    }
  });
  cancel.addEventListener("click", async () => {
    try {
      if (!record) return;
      await runtime.cancel(
        record.id,
        "Conversion cancelled. Your input is saved. Retry when ready.",
      );
    } catch (e) {
      message(errorMessage(e), true);
    }
  });
  fresh.addEventListener("click", () => {
    if (record?.state === "building") return;
    ++edit;
    ++downloadRevision;
    record = null;
    rendered = null;
    autoDownload = null;
    form.reset();
    input.required = true;
    input.closest("label")!.hidden = false;
    lock(false);
    button.hidden = false;
    button.disabled = false;
    retry.hidden = true;
    cancel.hidden = true;
    download.hidden = true;
    progress.hidden = true;
    saved.hidden = true;
    document.querySelector<HTMLElement>("#converted-preview-panel")!.hidden =
      true;
    if (download.dataset.url) {
      URL.revokeObjectURL(download.dataset.url);
      delete download.dataset.url;
      download.removeAttribute("href");
    }
    message("Choose a texture PNG for a new conversion.");
  });
  runtime.events.addEventListener("job", (e) => {
    if (e.detail.id === record?.id && e.detail.state !== "draft")
      void show(e.detail);
  });
  runtime.events.addEventListener("deleted", (e) => {
    if (e.detail === record?.id) fresh.click();
  });
  runtime.savedPanel(
    document.querySelector<HTMLElement>("#converter-saved")!,
    "conversion",
    async (job: SavedJob) => {
      ++edit;
      await saving.catch(() => {});
      form.reset();
      form.querySelector<HTMLInputElement>(
        `input[value="${job.parameters.body}"]`,
      )!.checked = true;
      download.hidden = true;
      ++downloadRevision;
      rendered = null;
      await show(job);
    },
  );
}
