import { createAutosaveQueue } from "./shared/autosave.js";
import type { BodyPreview } from "./body-preview.js";
import type { RuntimeManifest, SavedJob } from "./package-runtime/types.js";
import { required } from "./shared/dom.js";
import { TextureCompression } from "./texture-compression.js";

const tattoos: any[] = [];
const list = document.querySelector<HTMLElement>("#tattoo-list")!;
const form = document.querySelector<HTMLFormElement>("#build-form")!;
const buildButton = document.querySelector<HTMLButtonElement>("#build-button")!;
const addButton = document.querySelector<HTMLButtonElement>("#add-tattoo")!;
const statusBox = document.querySelector<HTMLElement>("#build-status")!;
const downloadLink =
  document.querySelector<HTMLAnchorElement>("#download-link")!;
const retryButton = document.querySelector<HTMLButtonElement>("#retry-build")!;
const cancelButton =
  document.querySelector<HTMLButtonElement>("#cancel-build")!;
const statusMessage = document.querySelector<HTMLElement>("#status-message")!;
let tattooViewer: BodyPreview | null = null;
let tattooViewerLoading: Promise<void> | null = null;
let convertedViewer: BodyPreview | null = null;
let convertedViewerLoading: Promise<void> | null = null;
let convertedPreviewResult = null;

function previewEntries() {
  return tattoos.map((tattoo, index) => ({
    ...tattoo,
    label: tattoo.label || `Tattoo ${index + 1}`,
  }));
}

function syncTattooPreview() {
  if (tattooViewer) void tattooViewer.setEntries(previewEntries());
}

async function openTattooPreview() {
  if (tattooViewer || tattooViewerLoading) return;
  const button = document.querySelector<HTMLButtonElement>(
    "#open-tattoo-preview",
  )!;
  const errorBox = document.querySelector<HTMLElement>(
    "#tattoo-preview-error",
  )!;
  button.disabled = true;
  button.textContent = "Loading 3D preview…";
  errorBox.hidden = true;
  tattooViewerLoading = (async () => {
    try {
      const { BodyPreview } = await import("./body-preview.js");
      tattooViewer = new BodyPreview(
        document.querySelector<HTMLElement>("#tattoo-body-preview")!,
      );
      button.hidden = true;
      syncTattooPreview();
    } catch (errorCause) {
      const error =
        errorCause instanceof Error
          ? errorCause
          : new Error(String(errorCause));
      errorBox.textContent =
        error.message ||
        "Could not load the 3D viewer. Reload the page to retry.";
      errorBox.hidden = false;
    } finally {
      button.disabled = false;
      button.textContent = "Show 3D preview";
      tattooViewerLoading = null;
    }
  })();
  await tattooViewerLoading;
}

document
  .querySelector<HTMLButtonElement>("#open-tattoo-preview")!
  .addEventListener("click", openTattooPreview);

async function showConvertedPreview(blob: any, gender: string, filename: any) {
  convertedPreviewResult = { blob, gender, filename };
  document.querySelector<HTMLElement>("#converted-preview-panel")!.hidden =
    false;
  const errorBox = document.querySelector<HTMLElement>(
    "#converted-preview-error",
  )!;
  errorBox.hidden = true;
  document.querySelector<HTMLElement>(
    "#converted-preview-caption",
  )!.textContent =
    `Last converted result: ${filename} · ${gender === "am" ? "Male (AM)" : "Female (AF)"}`;
  try {
    if (!convertedViewerLoading && !convertedViewer) {
      convertedViewerLoading = import("./body-preview.js")
        .then(({ BodyPreview }) => {
          convertedViewer = new BodyPreview(
            document.querySelector<HTMLElement>("#converted-body-preview")!,
          );
        })
        .finally(() => {
          convertedViewerLoading = null;
        });
    }
    if (convertedViewerLoading) await convertedViewerLoading;
    const result = convertedPreviewResult;
    await convertedViewer!.setEntries(
      [
        {
          id: "converted",
          label: result.filename,
          layer: 0,
          [result.gender]: result.blob,
        },
      ],
      { gender: result.gender, lockGender: true },
    );
  } catch (errorCause) {
    const error =
      errorCause instanceof Error ? errorCause : new Error(String(errorCause));
    errorBox.textContent =
      error.message ||
      "Could not load the 3D viewer. Your converted PNG is still available.";
    errorBox.hidden = false;
  }
}

window.addEventListener("pagehide", (event) => {
  if (event.persisted) return;
  tattooViewer?.dispose();
  convertedViewer?.dispose();
});

let conversionReady: Promise<void> | null = null;
export async function activateAppTool(tool: "texture" | "package") {
  if (tool === "package") {
    await openTattooPreview();
    return;
  }
  conversionReady ||= import("./conversion-wasm.js")
    .then((m) => m.attachConversion(showConvertedPreview))
    .catch((error: unknown) => {
      conversionReady = null;
      throw error;
    });
  await conversionReady;
}

function slugify(value: string) {
  return value
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 32);
}

function createTattooId() {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  // getRandomValues remains available on LAN HTTP origins.
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const hex = Array.from(bytes, (value) =>
    value.toString(16).padStart(2, "0"),
  ).join("");
  return [
    hex.slice(0, 8),
    hex.slice(8, 12),
    hex.slice(12, 16),
    hex.slice(16, 20),
    hex.slice(20),
  ].join("-");
}

function addTattoo() {
  if (tattoos.length >= 20) return;
  tattoos.push({
    id: createTattooId(),
    label: "",
    layer: tattoos.length,
    am: null,
    af: null,
    previews: {},
  });
  render();
}

function swapMenu(index: number, offset: number) {
  const target = index + offset;
  if (target < 0 || target >= tattoos.length) return;
  [tattoos[index], tattoos[target]] = [tattoos[target], tattoos[index]];
  render();
}

function swapLayer(index: number, offset: number) {
  const tattoo = tattoos[index];
  const targetLayer = tattoo.layer + offset;
  if (targetLayer < 0 || targetLayer >= tattoos.length) return;
  const other = tattoos.find((item) => item.layer === targetLayer);
  other.layer = tattoo.layer;
  tattoo.layer = targetLayer;
  render();
}

function removeTattoo(index: number) {
  const removedLayer = tattoos[index].layer;
  Object.values(tattoos[index].previews).forEach((url) =>
    URL.revokeObjectURL(String(url)),
  );
  tattoos.splice(index, 1);
  tattoos.forEach((item) => {
    if (item.layer > removedLayer) item.layer -= 1;
  });
  if (!tattoos.length) addTattoo();
  else render();
}

function assetSlot(
  tattoo: { [x: string]: any; previews: { [x: string]: string }; id: any },
  gender: string,
) {
  const file = tattoo[gender];
  const preview =
    tattoo.previews[gender] ||
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='96' height='96'%3E%3C/svg%3E";
  const title = gender === "am" ? "Male" : "Female";
  return `<div class="asset-slot">
    <img class="preview" src="${preview}" alt="${title} texture preview">
    <div class="asset-copy">
      <strong>${title}</strong>
      <span>${file ? escapeHtml(file.name) : "No texture selected"}</span>
      <label for="file-${tattoo.id}-${gender}">${file ? "Replace PNG" : "Choose PNG"}</label>
      <input id="file-${tattoo.id}-${gender}" data-action="file" data-gender="${gender}" accept="image/png" type="file">
    </div>
  </div>`;
}

function escapeHtml(value: string | null) {
  const node = document.createElement("span");
  node.textContent = value;
  return node.innerHTML;
}

function render() {
  list.innerHTML = tattoos
    .map(
      (tattoo, index) => `<article class="tattoo-card" data-index="${index}">
    <div class="card-main">
      <div class="card-head">
        <h3>${escapeHtml(tattoo.label || `Tattoo ${index + 1}`)}</h3>
        <div class="card-actions">
          <span class="order-label">Menu</span>
          <button class="icon-button" type="button" data-action="menu-up" aria-label="Move earlier in menu" ${index === 0 ? "disabled" : ""}>↑</button>
          <button class="icon-button" type="button" data-action="menu-down" aria-label="Move later in menu" ${index === tattoos.length - 1 ? "disabled" : ""}>↓</button>
          <span class="order-label">Layer ${tattoo.layer + 1}</span>
          <button class="icon-button" type="button" data-action="layer-down" aria-label="Move layer down" ${tattoo.layer === 0 ? "disabled" : ""}>↓</button>
          <button class="icon-button" type="button" data-action="layer-up" aria-label="Move layer up" ${tattoo.layer === tattoos.length - 1 ? "disabled" : ""}>↑</button>
          <button class="icon-button danger" type="button" data-action="delete" aria-label="Delete tattoo">×</button>
        </div>
      </div>
      <label>Tattoo name<input data-field="label" maxlength="64" required value="${escapeHtml(tattoo.label)}" placeholder="Kiryu Dragon"></label>
      <div class="asset-grid">${assetSlot(tattoo, "am")}${assetSlot(tattoo, "af")}</div>
    </div>
  </article>`,
    )
    .join("");
  document.querySelector<HTMLElement>("#tattoo-count")!.textContent =
    `${tattoos.length} / 20`;
  addButton.disabled = tattoos.length >= 20;
  syncTattooPreview();
  scheduleTattooSave();
}

list.addEventListener("input", (event) => {
  const card = (event.target as HTMLInputElement).closest<HTMLElement>(
    ".tattoo-card",
  );
  if (!card) return;
  const tattoo = tattoos[Number(card.dataset.index)];
  if ((event.target as HTMLInputElement).dataset.field) {
    tattoo[(event.target as HTMLInputElement).dataset.field!] = (
      event.target as HTMLInputElement
    ).value;
    if ((event.target as HTMLInputElement).dataset.field === "label")
      card.querySelector<HTMLElement>("h3")!.textContent =
        (event.target as HTMLInputElement).value ||
        `Tattoo ${Number(card.dataset.index) + 1}`;
    syncTattooPreview();
  }
  if ((event.target as HTMLInputElement).dataset.action === "file") {
    const gender = (event.target as HTMLInputElement).dataset.gender;
    if (!gender) return;
    const file = (event.target as HTMLInputElement).files?.[0] || null;
    tattoo[gender] = file;
    if (tattoo.previews[gender]) URL.revokeObjectURL(tattoo.previews[gender]);
    tattoo.previews[gender] = file ? URL.createObjectURL(file) : null;
    render();
  }
});

list.addEventListener("click", (event) => {
  const button = (event.target as HTMLInputElement).closest<HTMLButtonElement>(
    "button[data-action]",
  );
  if (!button) return;
  const index = Number(
    button.closest<HTMLElement>(".tattoo-card")!.dataset.index,
  );
  const actions: Record<string, () => void> = {
    "menu-up": () => swapMenu(index, -1),
    "menu-down": () => swapMenu(index, 1),
    "layer-down": () => swapLayer(index, -1),
    "layer-up": () => swapLayer(index, 1),
    delete: () => removeTattoo(index),
  };
  actions[button.dataset.action || ""]?.();
});

document
  .querySelector<HTMLInputElement>("#catalog-name")!
  .addEventListener("input", (event) => {
    const slug = document.querySelector<HTMLInputElement>("#bundle-slug")!;
    slug.value =
      slugify((event.target as HTMLInputElement).value).slice(0, 48) ||
      "tattoo-package";
  });
addButton.addEventListener("click", addTattoo);

function uniqueTattooKey(label: any, index: number, usedKeys: Set<unknown>) {
  const base = slugify(label) || `tattoo-${index + 1}`;
  let key = base;
  let suffix = 2;
  while (usedKeys.has(key)) {
    const ending = `-${suffix}`;
    key = `${base.slice(0, 32 - ending.length)}${ending}`;
    suffix += 1;
  }
  usedKeys.add(key);
  return key;
}

const localRuntime = import("./package-runtime/client.js");
let tattooDraft: SavedJob | null = null,
  tattooSaveTimer: number | undefined,
  tattooSaving: Promise<SavedJob | null> = Promise.resolve(null),
  restoringTattoo = false;
let tattooEncoderManifest: RuntimeManifest;
const tattooCompression = TextureCompression.mount(
  "tattoo",
  buildButton,
  scheduleTattooSave,
);
function tattooCompressionControls() {
  tattooCompression.update({
    manifest: tattooEncoderManifest,
    record: tattooDraft,
    formats: ["DXT5"],
    locked:
      !!tattooDraft?.snapshotHash ||
      ["building", "complete"].includes(tattooDraft?.state || ""),
  });
}
localRuntime
  .then((r) => r.manifest())
  .then((m: RuntimeManifest) => {
    tattooEncoderManifest = m;
    tattooCompressionControls();
  })
  .catch(() => {});
const tattooSaveStatus = document.createElement("p");
tattooSaveStatus.className = "build-note";
form.querySelector<HTMLElement>(".build-panel")!.append(tattooSaveStatus);
const newTattooBatch = document.createElement("button");
newTattooBatch.type = "button";
newTattooBatch.className = "secondary";
newTattooBatch.textContent = "New batch";
form.querySelector<HTMLElement>(".build-panel")!.append(newTattooBatch);
function tattooUi() {
  return {
    catalogName:
      document.querySelector<HTMLInputElement>("#catalog-name")!.value,
    catalogDescription: document.querySelector<HTMLTextAreaElement>(
      "#catalog-description",
    )!.value,
    tattoos: tattoos.map((t) => ({
      id: t.id,
      label: t.label,
      layer: t.layer,
      am: t.am ? `${t.id}-am` : null,
      af: t.af ? `${t.id}-af` : null,
    })),
  };
}
function scheduleTattooSave() {
  if (
    restoringTattoo ||
    ["building", "complete"].includes(tattooDraft?.state || "")
  )
    return;
  clearTimeout(tattooSaveTimer);
  tattooSaveTimer = setTimeout(
    () =>
      saveTattooDraft().catch((e) => {
        tattooSaveStatus.textContent = `Could not save: ${e.message}`;
      }),
    350,
  );
}
const tattooAutosaves = createAutosaveQueue<SavedJob | null>();
function saveTattooDraft() {
  clearTimeout(tattooSaveTimer);
  const ui = tattooUi();
  if (!ui.catalogName && !tattoos.some((t) => t.am || t.af || t.label))
    return Promise.resolve(tattooDraft);
  const uploads = tattoos.flatMap((t) =>
    ["am", "af"]
      .filter((g) => t[g])
      .map((g) => ({ name: `${t.id}-${g}`, file: t[g] })),
  );
  const compression = tattooCompression.payload();
  tattooSaving = tattooAutosaves.enqueue(async () => {
    const r = await localRuntime;
    tattooSaveStatus.textContent = "Saving in this browser…";
    if (!tattooDraft)
      tattooDraft = await r.saveDraft({
        id: r.store.id(),
        kind: "tattoo",
        revision: 0,
        state: "draft",
        checkpoints: [],
      });
    const files = await r.saveFiles(uploads);
    tattooDraft = await r.saveDraft({
      ...tattooDraft,
      revision: tattooDraft.revision + 1,
      ui,
      parameters: {
        ...tattooDraft.parameters,
        ...compression,
      },
      files,
    });
    tattooSaveStatus.textContent = "Saved in this browser.";
    return tattooDraft;
  });
  return tattooSaving;
}
function showLocalTattoo(job: SavedJob) {
  tattooDraft = job;
  tattooCompressionControls();
  statusBox.hidden = false;
  const progress =
    typeof job.progress === "number"
      ? job.progress
      : job.state === "complete"
        ? 100
        : job.state === "building"
          ? 20
          : 0;
  document.querySelector<HTMLElement>("#status-label")!.textContent = job.state;
  document.querySelector<HTMLElement>("#status-percent")!.textContent =
    `${progress}%`;
  document.querySelector<HTMLElement>("#progress-bar")!.style.width =
    `${progress}%`;
  statusMessage.textContent =
    job.error?.message ||
    job.message ||
    (job.state === "complete"
      ? "Your package is checked and ready to install."
      : job.state === "building"
        ? "Creating package in this browser…"
        : "Your batch is saved locally.");
  if (job.state !== "complete") {
    downloadLink.hidden = true;
    downloadLink.dataset.job = "";
  } else if (downloadLink.dataset.job !== job.id) {
    localRuntime
      .then((r) => r.bindDownload(downloadLink, job.id))
      .catch((e) => {
        statusMessage.textContent = e.message;
      });
  }
  retryButton.hidden = !["failed", "cancelled", "interrupted"].includes(
    job.state,
  );
  retryButton.textContent = "Resume build";
  cancelButton.hidden = job.state !== "building";
  cancelButton.textContent = "Cancel build";
  const locked = ["building", "complete"].includes(job.state);
  form
    .querySelectorAll<HTMLInputElement>(
      "#tattoo-list input,#tattoo-list button,#catalog-name,#catalog-description,#add-tattoo",
    )
    .forEach((n) => (n.disabled = locked));
  buildButton.disabled = locked;
  newTattooBatch.disabled = job.state === "building";
}
function tattooSpec() {
  const used = new Set();
  return {
    schema_version: 1,
    bundle: {
      slug:
        slugify(
          document.querySelector<HTMLInputElement>("#catalog-name")!.value,
        ) || "tattoo-package",
      catalog_name: document
        .querySelector<HTMLInputElement>("#catalog-name")!
        .value.trim(),
      catalog_description:
        document
          .querySelector<HTMLTextAreaElement>("#catalog-description")!
          .value.trim() ||
        document.querySelector<HTMLInputElement>("#catalog-name")!.value.trim(),
    },
    tattoos: tattoos.map((t, i) => ({
      key: uniqueTattooKey(t.label, i, used),
      menu_label: t.label,
      menu_order: i,
      layer_order: t.layer,
      assets: Object.fromEntries(
        ["am", "af"].filter((g) => t[g]).map((g) => [g, `${t.id}-${g}`]),
      ),
    })),
  };
}
form.addEventListener("input", scheduleTattooSave);
form.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    if (tattoos.some((t) => !t.am && !t.af))
      throw new Error("Each tattoo needs a male or female PNG.");
    buildButton.disabled = true;
    await saveTattooDraft();
    const r = await localRuntime;
    if (!tattooDraft)
      throw new Error("Add a tattoo texture before creating a package.");
    tattooDraft = { ...tattooDraft, spec: tattooSpec() };
    showLocalTattoo(await r.start(tattooDraft));
  } catch (eCause) {
    const e = eCause instanceof Error ? eCause : new Error(String(eCause));
    buildButton.disabled = false;
    statusBox.hidden = false;
    statusMessage.textContent = e.message;
  }
});
downloadLink.addEventListener("click", (event) => {
  if (!downloadLink.href.startsWith("blob:")) event.preventDefault();
});
cancelButton.addEventListener("click", async () => {
  try {
    if (!tattooDraft) return;
    showLocalTattoo(await (await localRuntime).cancel(tattooDraft.id));
  } catch (eCause) {
    const e = eCause instanceof Error ? eCause : new Error(String(eCause));
    statusMessage.textContent = e.message;
  }
});
retryButton.addEventListener("click", async () => {
  try {
    if (!tattooDraft) return;
    showLocalTattoo(await (await localRuntime).start(tattooDraft));
  } catch (eCause) {
    const e = eCause instanceof Error ? eCause : new Error(String(eCause));
    statusMessage.textContent = e.message;
  }
});
newTattooBatch.addEventListener("click", async () => {
  clearTimeout(tattooSaveTimer);
  await tattooSaving.catch(() => {});
  tattooDraft = null;
  tattooCompression.reset();
  tattooCompressionControls();
  restoringTattoo = true;
  tattoos.splice(0);
  document.querySelector<HTMLInputElement>("#catalog-name")!.value = "";
  document.querySelector<HTMLTextAreaElement>("#catalog-description")!.value =
    "";
  addTattoo();
  restoringTattoo = false;
  form
    .querySelectorAll<HTMLInputElement>("input,textarea,button")
    .forEach((n) => (n.disabled = false));
  statusBox.hidden = true;
  tattooSaveStatus.textContent =
    "Start a new batch. Earlier batches remain in Saved batches.";
});
localRuntime
  .then((r) => {
    r.events.addEventListener("job", (event) => {
      if (event.detail.id === tattooDraft?.id && event.detail.state !== "draft")
        showLocalTattoo(event.detail);
    });
    r.events.addEventListener("deleted", (event) => {
      if (event.detail === tattooDraft?.id) newTattooBatch.click();
    });
    r.savedPanel(
      form.querySelector<HTMLElement>(".build-panel")!,
      "tattoo",
      async (job: SavedJob) => {
        restoringTattoo = true;
        try {
          clearTimeout(tattooSaveTimer);
          await tattooSaving;
          tattooDraft = job;
          tattooCompression.restore(
            job.parameters?.texture_encoder,
            job.parameters?.refpack_compression,
          );
          tattoos.splice(0);
          document.querySelector<HTMLInputElement>("#catalog-name")!.value =
            job.ui.catalogName;
          document.querySelector<HTMLTextAreaElement>(
            "#catalog-description",
          )!.value = job.ui.catalogDescription;
          for (const row of job.ui.tattoos) {
            const t = { ...row, previews: {} };
            for (const g of ["am", "af"]) {
              const descriptor =
                row[g] && job.files.find((f) => f.name === row[g]);
              t[g] = descriptor
                ? new File(
                    [
                      required(
                        await r.store.getBlob(descriptor.blob),
                        "saved tattoo texture",
                      ),
                    ],
                    descriptor.filename,
                    { type: "image/png" },
                  )
                : null;
              if (t[g]) t.previews[g] = URL.createObjectURL(t[g]);
            }
            tattoos.push(t);
          }
          render();
          showLocalTattoo(job);
          tattooSaveStatus.textContent = "Restored from this browser.";
        } finally {
          restoringTattoo = false;
        }
      },
    );
  })
  .catch((e) => {
    tattooSaveStatus.textContent = e.message;
  });

addTattoo();
