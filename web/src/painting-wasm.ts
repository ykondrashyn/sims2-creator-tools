import { createAutosaveQueue } from "./shared/autosave.js";
import {
  errorMessage,
  type Metadata,
  type RuntimeManifest,
  type SavedJob,
} from "./package-runtime/types.js";
import type { PaintingPreview } from "./painting-preview.js";
import { prefixedControls, required } from "./shared/dom.js";
import type { Appearance, ReferenceAssets } from "./shared/scene-types.js";
import { TextureCompression } from "./texture-compression.js";
/* Painting source images, previews and packages remain in this browser. */
(() => {
  const $ = prefixedControls("painting-");
  let runtime: typeof import("./package-runtime/client.js"),
    manifest: RuntimeManifest,
    record: SavedJob | null,
    imageInfo: Metadata | null,
    selected: Metadata,
    viewer: PaintingPreview,
    refs: ReferenceAssets | null,
    busy = false,
    initialized = false;
  let revision = 0,
    previewSequence = 0,
    timer: number | undefined,
    previewTimer: number | undefined,
    saving = Promise.resolve(),
    manualFrame = false,
    priceEdited = false,
    autoDownload: string | null = null;
  let crop = {
      mode: "fill",
      x: 0.5,
      y: 0.5,
      zoom: 1,
      background: [255, 255, 255],
    },
    drag: { x: any; y: any } | null;
  const api = async () =>
    runtime || (runtime = await import("./package-runtime/client.js"));
  const compression = TextureCompression.mount(
    "painting",
    $("build").parentElement,
    (kind) => changed(kind !== "refpack"),
  );
  const frozen = () => !!record?.snapshotHash || record?.state === "complete";
  const msg = (text: string | null, id = "status") => {
    $(id).textContent = text;
  };
  const filename = () =>
    `${$("creator").value || "Creator"}_${
      $("title")
        .value.replace(/[^a-zA-Z0-9_-]/g, "_")
        .replace(/^[_-]+|[_-]+$/g, "")
        .slice(0, 48) || "Painting"
    }.package`;
  function controls() {
    for (const id of [
      "image",
      "creator",
      "title",
      "description",
      "price",
      "fit",
      "zoom",
      "background",
      "reset-crop",
    ])
      $(id).disabled = busy || frozen();
    for (const b of $("frames").querySelectorAll<HTMLButtonElement>("button"))
      b.disabled = busy || frozen();
    $("build").hidden = frozen();
    $("build").disabled = busy || !imageInfo || !$("form").checkValidity();
    $("retry").hidden = !(
      record?.snapshotHash &&
      ["failed", "cancelled", "interrupted"].includes(record.state)
    );
    $("retry").disabled = busy;
    $("retry").textContent =
      record?.state === "interrupted" ? "Resume build" : "Retry";
    $("cancel").hidden = record?.state !== "building";
    $("new").disabled = busy;
    $("filename").textContent = `Download: ${filename()}`;
    compression.update({
      manifest,
      record,
      formats: selected ? [selected.format] : [],
      locked: busy || frozen(),
    });
    $("crop-stage").setAttribute("aria-disabled", String(busy || frozen()));
  }
  function paintCrop() {
    if (!selected || !imageInfo) return;
    const stage = $("crop-stage"),
      img = $("crop-image");
    stage.style.aspectRatio = String(selected.aspect);
    stage.style.setProperty("--painting-aspect", String(selected.aspect));
    stage.style.backgroundColor = $("background").value;
    const w = stage.clientWidth,
      h = stage.clientHeight;
    const scale =
      crop.mode === "fill"
        ? Math.max(w / imageInfo.width, h / imageInfo.height) * crop.zoom
        : Math.min(w / imageInfo.width, h / imageInfo.height);
    const iw = imageInfo.width * scale,
      ih = imageInfo.height * scale;
    img.style.width = `${iw}px`;
    img.style.height = `${ih}px`;
    img.style.left = `${crop.mode === "fill" ? (w - iw) * crop.x : (w - iw) / 2}px`;
    img.style.top = `${crop.mode === "fill" ? (h - ih) * crop.y : (h - ih) / 2}px`;
    $("zoom-label").hidden = crop.mode !== "fill";
    $("background-label").hidden = crop.mode !== "contain" && !imageInfo.alpha;
  }
  function parameters() {
    return {
      ...compression.payload(),
      creator: $("creator").value,
      title: $("title").value,
      description: $("description").value,
      price: Number($("price").value),
      crop: structuredClone(crop),
      mode: "clone",
    };
  }
  const autosaves = createAutosaveQueue<void>();
  async function save() {
    if (!record || frozen()) return;
    const candidate = {
      ...record,
      revision: ++revision,
      template: selected,
      parameters: parameters(),
      ui: { manualFrame, priceEdited, mannequin: $("mannequin").value },
    };
    record = candidate;
    saving = autosaves.enqueue(async () => {
      const current = () =>
        record?.id === candidate.id && record.revision === candidate.revision;
      if (!current()) return;
      try {
        const saved = await runtime.saveDraft(candidate);
        if (current()) {
          record = saved;
          msg("Saved in this browser.", "save-status");
        }
      } catch (e) {
        if (current()) msg(`Could not save: ${errorMessage(e)}`, "save-status");
        throw e;
      }
    });
    return saving;
  }
  function changed(recompose = false) {
    controls();
    clearTimeout(timer);
    timer = setTimeout(() => save().catch(() => {}), 250);
    if (recompose) {
      paintCrop();
      clearTimeout(previewTimer);
      if (imageInfo) msg("Updating preview…", "preview-status");
      const seq = ++previewSequence;
      previewTimer = setTimeout(() => preview(seq), 300);
    }
  }
  async function preview(seq = ++previewSequence, forBuild = false) {
    if (!record || !imageInfo || (busy && !forBuild)) return;
    const current = {
      ...record,
      revision: ++revision,
      parameters: frozen() ? record.parameters : parameters(),
      template: selected,
    };
    msg("Updating preview…", "preview-status");
    try {
      const result = await runtime.paintingPreview(current);
      if (seq !== previewSequence || record?.id !== current.id) return;
      msg(
        result.report.low_resolution
          ? "This crop is smaller than the artwork texture and may look soft."
          : "The frame keeps its original game size.",
        "crop-note",
      );
      try {
        if (!viewer) {
          const { PaintingPreview } = await import("./painting-preview.js");
          viewer = new PaintingPreview($("viewer"));
        }
        refs ||= await runtime.paintingReferences(record.manifest);
        if (seq !== previewSequence) return;
        viewer.setOptions({ mannequin: $("mannequin").value });
        await viewer.show(result.scene as Appearance, selected.id, refs);
        if (seq !== previewSequence) return;
        msg("Preview ready.", "preview-status");
      } catch (e) {
        msg(
          `3D preview unavailable. You can still create and download the painting. ${errorMessage(e)}`,
          "preview-status",
        );
      }
    } catch (e) {
      if (seq === previewSequence) msg(errorMessage(e), "preview-status");
    }
  }
  function choose(item: Metadata, manual = false) {
    selected = item;
    if (manual) manualFrame = true;
    for (const b of $("frames").querySelectorAll<HTMLButtonElement>("button"))
      b.setAttribute("aria-pressed", String(b.dataset.id === item.id));
    if (!priceEdited) $("price").value = String(item.price);
    msg(
      `${item.label} · ${item.dimensions.width.toFixed(2)} × ${item.dimensions.height.toFixed(2)} game units · ${item.placement.tile_count} wall tile${item.placement.tile_count === 1 ? "" : "s"} · Requires The Sims 2 Legacy Collection`,
      "template-info",
    );
    crop.x = crop.y = 0.5;
    crop.zoom = 1;
    $("zoom").value = "1";
    changed(true);
  }
  async function frames(m: RuntimeManifest) {
    for (const img of $("frames").querySelectorAll<HTMLImageElement>("img"))
      if (img.src.startsWith("blob:")) URL.revokeObjectURL(img.src);
    $("frames").replaceChildren();
    for (const item of m.paintings.items) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "painting-frame";
      b.dataset.id = item.id;
      b.setAttribute("aria-pressed", String(item.id === selected?.id));
      const img = document.createElement("img");
      img.alt = "";
      img.width = 160;
      img.height = 128;
      const label = document.createElement("strong");
      label.textContent = item.label;
      const shape = document.createElement("span");
      shape.textContent = `${item.shape} · ${item.dimensions.width.toFixed(2)} × ${item.dimensions.height.toFixed(2)}`;
      b.append(img, label, shape);
      b.addEventListener("click", () => choose(item, true));
      $("frames").append(b);
      runtime
        .paintingThumbnail(m, item)
        .then((url) => {
          if (img.isConnected) img.src = url;
          else URL.revokeObjectURL(url);
        })
        .catch(() => {
          img.hidden = true;
        });
    }
  }
  async function newPainting() {
    clearTimeout(timer);
    clearTimeout(previewTimer);
    previewSequence++;
    await saving.catch(() => {});
    record = null;
    imageInfo = null;
    manualFrame = false;
    priceEdited = false;
    crop = {
      mode: "fill",
      x: 0.5,
      y: 0.5,
      zoom: 1,
      background: [255, 255, 255],
    };
    $("form").reset();
    compression.reset();
    $("editor").hidden = true;
    $("download").hidden = true;
    $("installation").hidden = true;
    $("progress").hidden = true;
    if ($("download").href.startsWith("blob:"))
      URL.revokeObjectURL($("download").href);
    for (const id of [
      "status",
      "image-status",
      "save-status",
      "preview-status",
    ])
      msg("", id);
    manifest = await runtime.manifest();
    await frames(manifest);
    choose(manifest.paintings.items[0]);
    controls();
  }
  async function open(saved: SavedJob) {
    clearTimeout(timer);
    clearTimeout(previewTimer);
    previewSequence++;
    await saving.catch(() => {});
    record = saved;
    compression.restore(
      saved.parameters.texture_encoder,
      saved.parameters.refpack_compression,
    );
    revision = Math.max(revision, saved.revision || 0);
    manualFrame = !!saved.ui?.manualFrame;
    priceEdited = !!saved.ui?.priceEdited;
    manifest = saved.manifest;
    selected = required(saved.template, "painting template");
    crop = structuredClone(
      saved.parameters.crop || {
        mode: "fill",
        x: 0.5,
        y: 0.5,
        zoom: 1,
        background: [255, 255, 255],
      },
    );
    refs = null;
    for (const id of ["creator", "title", "description", "price"])
      $(id).value = saved.parameters[id] ?? "";
    $("fit").value = crop.mode;
    $("zoom").value = String(crop.zoom);
    $("background").value =
      "#" +
      crop.background.map((c) => c.toString(16).padStart(2, "0")).join("");
    $("mannequin").value = saved.ui?.mannequin || "am";
    $("image").value = "";
    await frames(manifest);
    imageInfo = await runtime.paintingImage(saved);
    $("crop-image").src = imageInfo.preview;
    $("editor").hidden = false;
    paintCrop();
    msg(
      `${saved.files?.[0].filename} · ${imageInfo.width} × ${imageInfo.height}`,
      "image-status",
    );
    msg(
      `${selected.label} · Requires The Sims 2 Legacy Collection`,
      "template-info",
    );
    $("download").hidden = true;
    $("installation").hidden = saved.state !== "complete";
    if (saved.state === "complete")
      await runtime.bindDownload(
        $("download") as unknown as HTMLAnchorElement,
        saved.id,
      );
    msg(
      saved.state === "complete"
        ? "Completed painting. Start a new painting to make changes."
        : saved.snapshotHash
          ? "Saved build ready to resume."
          : "Saved painting opened.",
    );
    controls();
    await preview();
  }
  async function build() {
    if (busy || !record || !imageInfo) return;
    if (!frozen() && !$("form").reportValidity()) return;
    clearTimeout(timer);
    clearTimeout(previewTimer);
    previewSequence++;
    busy = true;
    controls();
    try {
      await save();
      await saving;
      $("progress").hidden = false;
      $("progress").value = 5;
      msg("Creating your painting in this browser…");
      await preview(++previewSequence, true);
      autoDownload = record.id;
      record = await runtime.start(record);
    } catch (e) {
      autoDownload = null;
      msg(errorMessage(e));
    } finally {
      busy = record?.state === "building";
      controls();
    }
  }
  async function init() {
    if (initialized) return;
    initialized = true;
    try {
      await api();
      manifest = await runtime.manifest();
      runtime.savedPanel($("saved"), "painting", open);
      runtime.events.addEventListener("job", ({ detail: j }) => {
        if (j.id !== record?.id) return;
        record = j;
        if (j.state !== "draft") busy = j.state === "building";
        controls();
        if (j.state === "building") {
          $("progress").value = Number(
            typeof j.progress === "number" ? j.progress : 15,
          );
          msg(j.message || "Creating and checking your painting…");
        } else if (j.state === "complete") {
          $("progress").value = 100;
          $("installation").hidden = false;
          msg(
            "Painting ready. Install the .package in your game's Downloads folder.",
          );
          runtime
            .bindDownload($("download") as unknown as HTMLAnchorElement, j.id)
            .then(() => {
              if (autoDownload === j.id) {
                autoDownload = null;
                return runtime.download(j.id);
              }
            })
            .catch((e) => msg(errorMessage(e)));
        } else if (j.error) {
          autoDownload = null;
          msg(j.error.message);
        }
      });
      runtime.events.addEventListener("deleted", ({ detail: id }) => {
        if (record?.id === id) newPainting().catch((e) => msg(errorMessage(e)));
      });
      await frames(manifest);
      choose(manifest.paintings.items[0]);
    } catch (e) {
      initialized = false;
      msg(errorMessage(e));
    }
  }
  $("image").addEventListener("change", async () => {
    const file = $("image").files?.[0];
    if (!file || busy) return;
    clearTimeout(timer);
    clearTimeout(previewTimer);
    previewSequence++;
    busy = true;
    controls();
    msg("Reading image in your browser…", "image-status");
    try {
      const info = await runtime.validatePainting(file, manifest);
      const files = await runtime.saveFiles([{ name: "painting-input", file }]);
      imageInfo = info;
      $("crop-image").src = info.preview;
      $("editor").hidden = false;
      if (!$("title").value)
        $("title").value = file.name.replace(/\.[^.]+$/, "").slice(0, 100);
      if (!manualFrame) {
        const aspect = info.width / info.height;
        choose(
          [...manifest.paintings.items].sort(
            (a, b) =>
              Math.abs(Math.log(a.aspect / aspect)) -
              Math.abs(Math.log(b.aspect / aspect)),
          )[0],
        );
      }
      record = await runtime.saveDraft({
        ...record,
        kind: "painting",
        manifest,
        template: selected,
        files,
        parameters: parameters(),
        ui: { manualFrame, priceEdited, mannequin: $("mannequin").value },
      });
      msg(`${file.name} · ${info.width} × ${info.height}`, "image-status");
      msg("Saved in this browser.", "save-status");
    } catch (e) {
      $("image").value = "";
      msg(
        `${errorMessage(e)}${record?.files?.[0] ? ` Current image: ${record.files?.[0].filename}.` : ""}`,
        "image-status",
      );
    } finally {
      busy = false;
      controls();
      paintCrop();
      if (imageInfo) void preview();
    }
  });
  for (const id of ["creator", "title", "description", "price"])
    $(id).addEventListener("input", () => {
      if (id === "price") priceEdited = true;
      changed();
    });
  $("fit").addEventListener("change", () => {
    crop.mode = $("fit").value;
    changed(true);
  });
  $("zoom").addEventListener("input", () => {
    crop.zoom = Number($("zoom").value);
    changed(true);
  });
  $("background").addEventListener("input", () => {
    crop.background = required(
      $("background").value.slice(1).match(/../g),
      '$("background").value.slice(1).match(/../g)',
    ).map((s) => parseInt(s, 16));
    changed(true);
  });
  $("reset-crop").addEventListener("click", () => {
    crop.x = crop.y = 0.5;
    crop.zoom = 1;
    $("zoom").value = "1";
    changed(true);
  });
  $("crop-stage").addEventListener("pointerdown", (e) => {
    if (busy || frozen() || crop.mode !== "fill") return;
    drag = { x: e.clientX, y: e.clientY };
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    (e.currentTarget as HTMLElement).focus();
  });
  $("crop-stage").addEventListener("pointermove", (e) => {
    if (!drag) return;
    const img = $("crop-image"),
      stage = $("crop-stage");
    const dx = img.offsetWidth - stage.clientWidth,
      dy = img.offsetHeight - stage.clientHeight;
    if (dx > 0)
      crop.x = Math.max(0, Math.min(1, crop.x - (e.clientX - drag.x) / dx));
    if (dy > 0)
      crop.y = Math.max(0, Math.min(1, crop.y - (e.clientY - drag.y) / dy));
    drag = { x: e.clientX, y: e.clientY };
    changed(true);
  });
  for (const event of ["pointerup", "pointercancel", "lostpointercapture"])
    $("crop-stage").addEventListener(event, () => {
      drag = null;
    });
  $("crop-stage").addEventListener("keydown", (e) => {
    if (busy || frozen() || crop.mode !== "fill") return;
    if (["ArrowLeft", "ArrowRight"].includes(e.key))
      crop.x = Math.max(
        0,
        Math.min(1, crop.x + (e.key === "ArrowLeft" ? 0.02 : -0.02)),
      );
    else if (["ArrowUp", "ArrowDown"].includes(e.key))
      crop.y = Math.max(
        0,
        Math.min(1, crop.y + (e.key === "ArrowUp" ? 0.02 : -0.02)),
      );
    else if (["+", "=", "-"].includes(e.key)) {
      crop.zoom = Math.max(
        1,
        Math.min(8, crop.zoom + (e.key === "-" ? -0.1 : 0.1)),
      );
      $("zoom").value = String(crop.zoom);
    } else return;
    e.preventDefault();
    changed(true);
  });
  new ResizeObserver(paintCrop).observe($("crop-stage"));
  $("mannequin").addEventListener("change", () => {
    viewer?.setOptions({ mannequin: $("mannequin").value });
    if (!frozen()) changed();
  });
  $("front").addEventListener("click", () => viewer?.resetView(true));
  $("reset-view").addEventListener("click", () => viewer?.resetView());
  $("form").addEventListener("submit", (e) => {
    e.preventDefault();
    void build();
  });
  $("retry").addEventListener("click", build);
  $("cancel").addEventListener("click", () => {
    previewSequence++;
    autoDownload = null;
    runtime
      ?.cancel(required(record, "batch").id)
      .catch((e) => msg(errorMessage(e)));
  });
  $("new").addEventListener("click", () =>
    newPainting().catch((e) => msg(errorMessage(e))),
  );
  document
    .querySelector<HTMLElement>('[data-tab="painting"]')!
    .addEventListener("click", init);
  window.addEventListener("pagehide", () => viewer?.dispose());
})();
