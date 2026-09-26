import { createAutosaveQueue } from "./shared/autosave.js";
import { HairCurveEditor } from "./hair-curves.js";
import type {
  Metadata,
  RuntimeManifest,
  SavedJob,
  StoredFile,
} from "./package-runtime/types.js";
import { control, required } from "./shared/dom.js";
import { TextureCompression } from "./texture-compression.js";

(() => {
  const $ = control;
  const selection = new Set<string>();
  const bases = new Map<string, Metadata>();
  let installed: Metadata | null = null,
    encoderManifest: RuntimeManifest | null = null;
  const compression = TextureCompression.mount(
    "hair",
    $("hair-primary").parentElement,
    () => {
      scheduleSave();
      controls();
    },
  );
  let palette: any[] = [];
  const colorKey = (color: { id: any; name: any }) => color.id || color.name;
  let curveEditor: HairCurveEditor | null = null;
  let selectedTemplate: Metadata | null = null;
  let job: SavedJob | null = null;
  const runtime = import("./package-runtime/client.js");
  let uploadedFiles: StoredFile[] = [],
    importedItems: Metadata[] = [],
    savedRecord: SavedJob | null = null,
    saveTimer: number | undefined,
    saveChain: Promise<unknown> = Promise.resolve(),
    restoring = false;
  let sourceEpoch = 0;
  const downloadAnchor = document.createElement("a");
  downloadAnchor.id = "hair-download";
  downloadAnchor.className = "download";
  downloadAnchor.textContent = "Download ZIP";
  downloadAnchor.hidden = true;
  $("hair-primary").after(downloadAnchor);
  const saveNote = document.createElement("p");
  saveNote.className = "build-note";
  $("hair-export-section").append(saveNote);
  let previewResult: { previews: any } | null = null;
  let busy = false;
  let activity = "";
  let importFailed = false;
  const uploadSource = () => $("hair-mesh-source").value === "upload";
  const running = () => !!job && ["queued", "building"].includes(job.state);
  const finished = () => job?.state === "complete";
  const previewKey = (id: string) =>
    JSON.stringify([bases.get(id), [...selection], curveEditor?.payload()]);
  const current = () =>
    previewResult?.previews.some(
      (p: { slot: string; key: string }) =>
        p.slot === $("hair-preview-slot").value && p.key === previewKey(p.slot),
    );
  const colorAllowed = (color: { kind: any }) =>
    color.kind !== "grey" ||
    !selectedTemplate ||
    selectedTemplate.inspection.ages.some((a: { age: number }) => a.age & 16);
  const slot = () =>
    selectedTemplate?.inspection.textures.find(
      (s: { id: string }) => s.id === $("hair-preview-slot").value,
    );
  const defaults = () => ({
    base: required(selectedTemplate, "selectedTemplate").input_base,
    black: 0,
    white: 255,
    gamma: 1,
  });
  const uploadFields = ["hair-mesh-files", "hair-recolor-files"];
  const sourcePrompt = () =>
    uploadSource()
      ? "Packages are checked automatically once both fields have files."
      : "Choose a hairstyle to see its colors.";

  function element<K extends keyof HTMLElementTagNameMap>(
    tag: K,
    text?: string,
    className?: string,
  ) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }
  function option(value: string, label: string) {
    const node = element("option", label);
    node.value = value;
    return node;
  }
  function message(text: string | null, error = false, source = false) {
    const node = $(source ? "hair-source-status" : "hair-status");
    node.textContent = text;
    node.hidden = !text;
    node.classList.toggle("error", error);
  }
  function controls() {
    const locked =
      busy || running() || finished() || !!savedRecord?.snapshotHash;
    $("hair-form")
      .querySelectorAll<HTMLInputElement>("input, select")
      .forEach((input) => {
        input.disabled = input.hasAttribute("data-review-control")
          ? busy
          : locked || input.dataset.unsupported === "true";
      });
    const retry =
      job && ["failed", "cancelled", "interrupted"].includes(job.state);
    $("hair-primary").textContent = busy
      ? activity
      : running()
        ? "Creating packages…"
        : finished()
          ? "Download ZIP"
          : retry || current()
            ? retry
              ? "Resume build"
              : "Create ZIP"
            : previewResult
              ? "Update previews"
              : "Preview colors";
    $("hair-primary").hidden = !!finished();
    if (!finished()) {
      downloadAnchor.hidden = true;
      downloadAnchor.dataset.job = "";
    }
    if (finished() && downloadAnchor.dataset.job !== required(job, "job").id)
      (async () => {
        try {
          await (
            await runtime
          ).bindDownload(downloadAnchor, required(job, "job").id);
        } catch (eCause) {
          const e =
            eCause instanceof Error ? eCause : new Error(String(eCause));
          message(e.message, true);
        }
      })();
    $("hair-primary").disabled =
      busy || running() || !installed || !selectedTemplate || !selection.size;
    $("hair-cancel").hidden = !running();
    $("hair-cancel").disabled = busy;
    $("hair-new").hidden = !savedRecord || running();
    $("hair-new").disabled = busy;
    $("hair-new").textContent = "New batch";
    $("hair-import-retry").hidden = !importFailed;
    $("hair-import-retry").disabled = busy;
    $("hair-base-reset").disabled = locked;
    const selectedColors = palette.filter((c) => selection.has(colorKey(c)));
    const greyOnly =
      selectedColors.length > 0 &&
      selectedColors.every((c) => c.kind === "grey");
    const generatedSlots = (
      selectedTemplate?.inspection?.textures || []
    ).filter(
      (t: Metadata) =>
        selectedColors.length && (!greyOnly || t.ages.includes(16)),
    );
    compression.update({
      manifest: encoderManifest,
      record: savedRecord,
      formats: generatedSlots.map((t: Metadata) => t.format),
      locked,
    });
    curveEditor?.setLocked(locked);
    $("hair-own-palette")
      .querySelectorAll<HTMLButtonElement>("button")
      .forEach((button) => {
        button.disabled = locked;
      });
    $("hair-progress").hidden = !running();
    if (running() && typeof job?.progress === "object" && job.progress.total) {
      $("hair-progress").max = Number((job!.progress as Metadata).total);
      $("hair-progress").value = Number((job!.progress as Metadata).completed);
    } else $("hair-progress").removeAttribute("value");
    $("hair-form").setAttribute("aria-busy", String(busy || !!running()));
  }
  async function api(
    path: string,
    options?: { body: FormData; method: string },
  ) {
    if (path !== "/curves/parse")
      throw new Error("This package operation now runs in your browser.");
    const result = await (
      await runtime
    ).parseCurve(options?.body.get("file") as File);
    return { json: async () => ({ curve: result.curve }) };
  }
  async function action(
    label: string,
    callback: () => Promise<void>,
    source = false,
  ) {
    if (busy) return;
    busy = true;
    activity = label;
    controls();
    try {
      await callback();
    } catch (errorCause) {
      const error =
        errorCause instanceof Error
          ? errorCause
          : new Error(String(errorCause));
      message(error.message, true, source);
    } finally {
      busy = false;
      controls();
    }
  }
  function filenames() {
    const prefix = `${$("hair-creator").value || "Creator"}_${$("hair-name").value || "HairName"}`;
    const meshes = selectedTemplate?.meshes || [];
    $("hair-zip-name").textContent = `${prefix}_Recolors.zip`;
    $("hair-zip-detail").textContent =
      `${selection.size} recolor ${selection.size === 1 ? "package" : "packages"}${meshes.length ? ` + ${meshes.length} ${meshes.length === 1 ? "mesh" : "meshes"}` : ""} + installation instructions`;
    $("hair-filenames").replaceChildren(
      ...palette
        .filter((c) => selection.has(colorKey(c)))
        .map((c) =>
          element("li", `${prefix}_${c.name.replaceAll(" ", "")}.package`),
        ),
      ...meshes.map((mesh: { filename: any }) =>
        element("li", `Meshes/${mesh.filename}`),
      ),
      element("li", "README.txt"),
    );
    $("hair-selected-count").textContent =
      `${selection.size} / ${palette.filter(colorAllowed).length} selected`;
  }
  function filter() {
    const value = $("hair-filter").value;
    let shown = 0;
    $("hair-colors-section")
      .querySelectorAll<HTMLElement>(".hair-color")
      .forEach((card) => {
        card.hidden =
          value === "selected"
            ? !selection.has(card.dataset.color || "")
            : value !== "all" && card.dataset.kind !== value;
        if (
          required(card.parentElement, "card.parentElement").classList.contains(
            "hair-own-color",
          )
        )
          required(card.parentElement, "card.parentElement").hidden =
            card.hidden;
        if (!card.hidden) shown++;
      });
    $("hair-filter-empty").hidden = shown > 0;
  }
  function showPalette() {
    $("hair-palette").replaceChildren();
    $("hair-own-palette").replaceChildren();
    $("hair-own-colors").hidden = !curveEditor?.colors.length;
    palette.forEach((color) => {
      const card = element("label", undefined, "hair-color");
      card.dataset.color = colorKey(color);
      card.dataset.kind = color.kind;
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = selection.has(colorKey(color));
      checkbox.dataset.unsupported = String(!colorAllowed(color));
      checkbox.setAttribute("aria-label", color.name);
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) selection.add(colorKey(color));
        else selection.delete(colorKey(color));
        changed();
      });
      const swatch = element("span", undefined, "hair-swatch");
      swatch.style.backgroundColor = color.swatch;
      swatch.setAttribute("aria-hidden", "true");
      card.append(checkbox, swatch, element("span", color.name));
      if (!colorAllowed(color)) {
        card.title = "This hairstyle has no Elder age";
        card.append(element("small", "No Elder", "hair-unavailable"));
      } else if (color.family) card.title = `Natural family ${color.family}`;
      else if (color.kind === "grey") card.title = "Elder-only package";
      if (color.id) {
        const row = element("div", undefined, "hair-own-color");
        const category = element(
          "small",
          ["Custom", "Black", "Brown", "Blond", "Red"][color.bin],
        );
        const edit = element("button", "Edit", "text-button");
        const remove = element("button", "Remove", "text-button");
        edit.type = remove.type = "button";
        edit.setAttribute("aria-label", `Edit ${color.name}`);
        remove.setAttribute("aria-label", `Remove ${color.name}`);
        edit.addEventListener("click", () =>
          required(curveEditor, "curveEditor").edit(color.id),
        );
        remove.addEventListener("click", () =>
          required(curveEditor, "curveEditor").remove(color.id),
        );
        row.append(card, category, edit, remove);
        $("hair-own-palette").append(row);
      } else $("hair-palette").append(card);
    });
    filter();
    filenames();
  }
  function changed() {
    scheduleSave();
    filenames();
    filter();
    showPreview();
    controls();
    if (!selection.size) message("Select at least one color to continue.");
    else if (previewResult)
      message(
        "Selection or adjustments changed. Update previews before creating the ZIP.",
      );
    else message("");
  }
  function resetJob() {
    job = null;
    savedRecord = null;
    compression.reset();
    clearTimeout(saveTimer);
    sourceEpoch++;
    previewResult = null;
    message("");
    showPreview();
    controls();
  }
  function slotTitle(texture: Metadata, index: number) {
    return `Texture ${index + 1}: ${texture.supported_ages.join(", ")}`;
  }
  function setupSource() {
    $("hair-standard-options").hidden = uploadSource();
    $("hair-upload-options").hidden = !uploadSource();
    uploadFields.forEach((id: string) => {
      $(id).required = uploadSource();
    });
    $("hair-selection").querySelector<HTMLOptionElement>(
      '[value="grey"]',
    )!.disabled = !colorAllowed({ kind: "grey" });
    for (const id of [
      "hair-template-summary",
      "hair-colors-section",
      "hair-export-section",
    ])
      $(id).hidden = !selectedTemplate;
    if (selectedTemplate) {
      const info = selectedTemplate;
      $("hair-template-label").textContent = info.label;
      $("hair-template-detail").textContent =
        `${info.gender} · ${info.ages.join(", ")} · ${info.inspection.textures.length} embedded ${info.inspection.textures.length === 1 ? "texture" : "textures"}`;
      $("hair-requirements").textContent =
        info.requirements +
        (info.meshes?.length
          ? ` The ZIP includes ${info.meshes.length} unchanged mesh ${info.meshes.length === 1 ? "package" : "packages"}.`
          : "");
      $("hair-family-note").textContent = info.inspection.ages.some(
        (a: { age: number }) => a.age & 16,
      )
        ? "Natural families link four colors for in-game switching. Natural elders turn Mail Bomb grey. Custom colors stay colored. Standalone greys are Elder only."
        : "Natural families link four colors for in-game switching. This hairstyle has no Elder age, so standalone greys are unavailable.";
      $("hair-preview-slot").replaceChildren(
        ...info.inspection.textures.map((texture: Metadata, index: number) =>
          option(texture.id, slotTitle(texture, index)),
        ),
      );
      $("hair-preview-slot-field").hidden =
        info.inspection.textures.length === 1;
      $("hair-editor-count").textContent =
        `${info.inspection.textures.length} ${info.inspection.textures.length === 1 ? "texture" : "textures"}`;
      $("hair-base").replaceChildren(
        ...required(installed, "installed").bases.map((base: string) =>
          option(
            base,
            base === "Arbitrary texture"
              ? "Unknown / other color (approximate)"
              : base,
          ),
        ),
      );
      palette
        .filter((c) => !colorAllowed(c))
        .forEach((c) => selection.delete(colorKey(c)));
      loadSlotSettings();
    }
    showPalette();
    controls();
  }
  async function selectTemplate(id: string) {
    selectedTemplate = null;
    bases.clear();
    resetJob();
    setupSource();
    if (!id) {
      message(sourcePrompt(), false, true);
      return;
    }
    message("Loading hairstyle…", false, true);
    const entry = [
      ...required(installed, "installed").items,
      ...importedItems,
    ].find((item) => item.id === id);
    if (!entry) throw new Error("Choose a hairstyle again.");
    const r = await runtime;
    const item = {
      ...entry,
      inspection: await r.openHair(entry, uploadSource() ? uploadedFiles : []),
    };
    if ((item.kind === "custom") !== uploadSource())
      throw new Error("Choose a hairstyle from the selected source.");
    selectedTemplate = item;
    item.inspection.textures.forEach((texture: { id: any }) =>
      bases.set(texture.id, defaults()),
    );
    $("hair-editor").open = false;
    $("hair-adjustments").open = false;
    setupSource();
    message("", false, true);
    scheduleSave();
  }
  function figure(source: string, caption: string) {
    const image = document.createElement("img");
    image.src = source;
    image.alt = caption;
    image.className = "preview hair-base-image";
    const item = document.createElement("figure");
    item.append(image, element("figcaption", caption));
    return item;
  }
  function loadSlotSettings() {
    const texture = slot();
    if (!texture) return;
    const config = bases.get(texture.id);
    $("hair-base").value = required(config, "config").base;
    for (const key of ["black", "white", "gamma"])
      $("hair-" + key).value = Number.isFinite(required(config, "config")[key])
        ? required(config, "config")[key]
        : "";
    showPreview();
  }
  function showPreview() {
    const texture = slot();
    if (!texture) return;
    const config = bases.get(texture.id);
    const preview = previewResult?.previews.find(
      (p: { slot: string; key: string }) =>
        p.slot === texture.id && p.key === previewKey(p.slot),
    );
    const previousColor = $("hair-preview-color").value;
    const colors = palette.filter((c) => selection.has(colorKey(c)));
    $("hair-preview-color").replaceChildren(
      ...colors.map((c) => option(colorKey(c), c.name)),
    );
    if (colors.some((c) => colorKey(c) === previousColor))
      $("hair-preview-color").value = previousColor;
    $("hair-preview-color-field").hidden = !preview;
    const target = preview?.targets.find(
      (t: { color: string }) => t.color === $("hair-preview-color").value,
    );
    const index = required(
      selectedTemplate,
      "selectedTemplate",
    ).inspection.textures.indexOf(texture);
    const stages = [
      figure(preview?.original || texture.preview, "Original texture"),
    ];
    if (preview) {
      stages.push(figure(preview.base, "Prepared base"));
      if (target?.active !== false)
        stages.push(
          figure(
            target.image,
            `${target.rendered_color}${texture.ages.every((a: number) => a === 16) ? " (Elder)" : ""}`,
          ),
        );
      if (target?.elder_image)
        stages.push(figure(target.elder_image, "Elder: Mail Bomb"));
    }
    $("hair-base-previews").replaceChildren(...stages);
    $("hair-preview-note").textContent =
      `${slotTitle(texture, index)} · ${texture.width} × ${texture.height}. Transparency is preserved.` +
      (target?.active === false
        ? " This Elder-only color does not use this texture. Choose an Elder texture to preview it."
        : "");
    $("hair-base-note").textContent =
      required(config, "config").base === "Arbitrary texture"
        ? "This source color is unknown. Base preparation is approximate. Adjust it if the preview looks too light or dark."
        : `Input base: ${required(config, "config").base}.`;
    $("hair-levels").hidden =
      required(config, "config").base !== "Arbitrary texture";
  }
  function validate() {
    for (const id of ["hair-creator", "hair-name"]) {
      const input = $(id);
      if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,47}$/.test(input.value)) {
        input.focus();
        throw new Error(
          `Enter ${id === "hair-creator" ? "a creator" : "a hair name"} using letters, numbers, underscores or hyphens.`,
        );
      }
    }
    for (const [id, config] of bases) {
      if (config.base !== "Arbitrary texture") continue;
      if (
        !Number.isInteger(config.black) ||
        !Number.isInteger(config.white) ||
        config.black < 0 ||
        config.white > 255 ||
        config.black >= config.white ||
        !Number.isFinite(config.gamma) ||
        config.gamma < 0.1 ||
        config.gamma > 5
      ) {
        $("hair-preview-slot").value = id;
        $("hair-editor").open = true;
        $("hair-adjustments").open = true;
        loadSlotSettings();
        $("hair-black").focus();
        throw new Error(
          "Use black and white points between 0 and 255, with black below white, and gamma between 0.1 and 5.",
        );
      }
    }
  }
  function capture() {
    return {
      ...compression.payload(),
      creator: $("hair-creator").value,
      hair_name: $("hair-name").value,
      colors: palette.filter((c) => selection.has(colorKey(c))).map(colorKey),
      custom_colors: required(curveEditor, "curveEditor").payload(),
      texture_settings: Object.fromEntries(
        [...bases].map(([id, c]) => [
          id,
          c.base === "Arbitrary texture" ? { ...c } : { base: c.base },
        ]),
      ),
      requirements: required(selectedTemplate, "selectedTemplate").requirements,
      template_label: required(selectedTemplate, "selectedTemplate").label,
      template_credit: required(selectedTemplate, "selectedTemplate")
        .template_credit,
    };
  }
  function scheduleSave() {
    if (
      restoring ||
      !selectedTemplate ||
      savedRecord?.snapshotHash ||
      running() ||
      finished()
    )
      return;
    clearTimeout(saveTimer);
    saveTimer = setTimeout(
      () =>
        persist().catch((e) => {
          saveNote.textContent = `Could not save: ${e.message}`;
        }),
      350,
    );
  }
  const autosaves = createAutosaveQueue<SavedJob | null>();
  function persist() {
    clearTimeout(saveTimer);
    const epoch = sourceEpoch,
      template = selectedTemplate,
      files = uploadSource() ? [...uploadedFiles] : [],
      parameters = capture();
    const ui = {
      customColors: structuredClone(
        required(curveEditor, "curveEditor").colors,
      ),
      bases: [...bases].map(([k, v]) => [k, { ...v }]),
    };
    saveChain = autosaves.enqueue(async () => {
      if (epoch !== sourceEpoch) return savedRecord;
      const r = await runtime;
      const next = {
        ...savedRecord,
        id: savedRecord?.id || r.store.id(),
        kind: "hair",
        revision: (savedRecord?.revision || 0) + 1,
        state: "draft",
        template,
        files,
        parameters,
        ui,
        checkpoints: [],
        snapshotHash: null,
      };
      saveNote.textContent = "Saving in this browser…";
      const saved = await r.saveDraft(next);
      if (epoch === sourceEpoch) {
        savedRecord = saved;
        saveNote.textContent = "Saved in this browser.";
        controls();
      }
      return saved;
    });
    return saveChain;
  }
  async function prepare(onlySlot = false) {
    validate();
    if (!onlySlot) await persist();
    message("Preparing color previews in this browser…");
    const r = await runtime;
    const result = await r.preview(required(savedRecord, "batch"), slot().id);
    savedRecord = result.record;
    job = result.record;
    if (!previewResult) previewResult = { previews: [] };
    result.preview.key = previewKey(result.preview.slot);
    previewResult.previews = [
      ...previewResult.previews.filter(
        (p: { slot: any }) => p.slot !== result.preview.slot,
      ),
      result.preview,
    ];
    showPreview();
    $("hair-editor").open = true;
    message(
      finished()
        ? "Your completed batch is read-only. Download its checked ZIP or start a new batch."
        : "Previews ready. Check your colors, then create the ZIP. Accessories sharing a texture are recolored too.",
    );
  }
  function report(status: SavedJob) {
    job = savedRecord = status;
    const progress =
      typeof job.progress === "object" ? job.progress : undefined;
    message(
      finished()
        ? "Your ZIP is checked and ready to install. It is saved in this browser."
        : running()
          ? progress
            ? `Creating packages: ${progress.completed} of ${progress.total}, ${progress.color || ""}.`
            : "Starting browser package engine…"
          : required(job, "job").error?.message ||
            "Build interrupted. Resume to keep the same identities.",
      required(job, "job").state === "failed",
    );
    controls();
  }
  async function build() {
    if (!savedRecord?.snapshotHash) await persist();
    report(await (await runtime).start(required(savedRecord, "batch")));
  }
  async function download() {
    await (await runtime).download(required(savedRecord, "savedRecord").id);
  }
  async function importPackages() {
    importFailed = false;
    try {
      const meshes = [...($("hair-mesh-files").files || [])];
      const recolors = [...($("hair-recolor-files").files || [])];
      if (!meshes.length || !recolors.length) {
        message(
          !meshes.length && !recolors.length
            ? sourcePrompt()
            : !meshes.length
              ? "Add the mesh package to continue. Both mesh and recolor packages are required."
              : "Add a matching recolor package to continue. Both mesh and recolor packages are required.",
          false,
          true,
        );
        return;
      }
      const files = [...meshes, ...recolors];
      if (files.some((file) => !file.name.toLowerCase().endsWith(".package")))
        throw new Error(
          "Choose .package files. Extract them from any ZIP first.",
        );
      if (
        files.length > 64 ||
        files.some((file) => file.size > 64 * 1024 * 1024) ||
        files.reduce((total, file) => total + file.size, 0) > 128 * 1024 * 1024
      )
        throw new Error(
          "Choose up to 64 packages, 64 MiB each and 128 MiB total.",
        );
      message("Checking packages…", false, true);
      const imported = await (await runtime).inspectPackages(files);
      uploadedFiles = imported.files;
      importedItems = imported.items;
      $("hair-upload-recolor").replaceChildren(
        option("", "Choose a recolor"),
        ...imported.items.map((item: { id: any; recolor_filename: any }) =>
          option(item.id, item.recolor_filename),
        ),
      );
      $("hair-upload-recolor-field").hidden = imported.items.length < 2;
      if (imported.items.length === 1)
        await selectTemplate(imported.items[0].id);
      else
        message(
          "Choose the recolor whose textures you want to use.",
          false,
          true,
        );
    } catch (errorCause) {
      const error =
        errorCause instanceof Error
          ? errorCause
          : new Error(String(errorCause));
      importFailed = true;
      throw error;
    }
  }

  $("hair-form").addEventListener("submit", (event) => {
    event.preventDefault();
    if (!selectedTemplate || !selection.size || running()) return;
    if (finished()) void action("Downloading…", download);
    else if (current() || savedRecord?.snapshotHash)
      void action("Starting build…", build);
    else {
      try {
        validate();
      } catch (errorCause) {
        const error =
          errorCause instanceof Error
            ? errorCause
            : new Error(String(errorCause));
        message(error.message, true);
        return;
      }
      void action("Preparing previews…", prepare);
    }
  });
  $("hair-cancel").addEventListener("click", () =>
    action("Cancelling…", async () =>
      report(
        await (await runtime).cancel(required(savedRecord, "savedRecord").id),
      ),
    ),
  );
  $("hair-new").addEventListener("click", async () => {
    clearTimeout(saveTimer);
    await saveChain.catch(() => {});
    required(curveEditor, "curveEditor").colors.forEach((c: { id: string }) =>
      selection.delete(c.id),
    );
    required(curveEditor, "curveEditor").clear();
    palette = [...required(installed, "installed").palette];
    resetJob();
    showPalette();
    $("hair-editor").open = false;
    saveNote.textContent =
      "Start a new batch. Earlier batches remain in Saved batches.";
    message("New batch. Custom colors from the previous batch were cleared.");
    $("hair-creator").focus();
  });
  $("hair-import-retry").addEventListener("click", () =>
    action("Checking packages…", importPackages, true),
  );
  $("hair-mesh-source").addEventListener("change", () => {
    selectedTemplate = null;
    bases.clear();
    importFailed = false;
    $("hair-template-select").value = "";
    $("hair-upload-recolor-field").hidden = true;
    uploadFields.forEach((id: string) => {
      $(id).value = "";
    });
    resetJob();
    setupSource();
    message(sourcePrompt(), false, true);
  });
  $("hair-template-select").addEventListener("change", () =>
    action(
      "Loading hairstyle…",
      () => selectTemplate($("hair-template-select").value),
      true,
    ),
  );
  $("hair-upload-recolor").addEventListener("change", () =>
    action(
      "Loading hairstyle…",
      () => selectTemplate($("hair-upload-recolor").value),
      true,
    ),
  );
  uploadFields.forEach((id: string) =>
    $(id).addEventListener("change", () => {
      selectedTemplate = null;
      bases.clear();
      $("hair-upload-recolor-field").hidden = true;
      resetJob();
      setupSource();
      void action("Checking packages…", importPackages, true);
    }),
  );
  $("hair-selection").addEventListener("change", () => {
    const value = $("hair-selection").value;
    if (!value) return;
    selection.clear();
    palette
      .filter(colorAllowed)
      .filter(
        (c) =>
          value === "all" || c.kind === value || String(c.family) === value,
      )
      .forEach((c) => selection.add(colorKey(c)));
    $("hair-selection").value = "";
    $("hair-filter").value = "all";
    showPalette();
    changed();
  });
  $("hair-filter").addEventListener("change", filter);
  ["hair-creator", "hair-name"].forEach((id: string) =>
    $(id).addEventListener("input", changed),
  );
  $("hair-preview-slot").addEventListener("change", () => {
    loadSlotSettings();
    if (previewResult && !current())
      void action("Preparing texture…", () => prepare(true));
  });
  $("hair-preview-color").addEventListener("change", showPreview);
  $("hair-base").addEventListener("change", () => {
    required(bases.get(slot().id), "bases.get(slot().id)").base =
      $("hair-base").value;
    changed();
  });
  ["black", "white", "gamma"].forEach((key) =>
    $("hair-" + key).addEventListener("input", () => {
      required(bases.get(slot().id), "bases.get(slot().id)")[key] = $(
        "hair-" + key,
      ).valueAsNumber;
      changed();
    }),
  );
  $("hair-base-reset").addEventListener("click", () => {
    bases.set(slot().id, defaults());
    changed();
    loadSlotSettings();
  });

  curveEditor = new HairCurveEditor(api, action, (added, removed) => {
    palette = [
      ...(installed?.palette || []),
      ...required(curveEditor, "curveEditor").colors,
    ];
    if (added) selection.add(added);
    if (removed) selection.delete(removed);
    showPalette();
    changed();
  });
  curveEditor.builtinNames = [];

  runtime
    .then((r) => r.manifest())
    .then((m: RuntimeManifest) => {
      encoderManifest = m;
      const value = m.hair;
      installed = value;
      palette = [...value.palette];
      curveEditor.builtinNames = value.palette.map(
        (c: { name: any }) => c.name,
      );
      const groups = new Map();
      value.items
        .filter((item: { kind: string }) => item.kind !== "custom")
        .forEach(
          (item: { game_content: any; gender: any; id: any; label: any }) => {
            const title = `${item.game_content || "Standard in-game"}, ${item.gender}`;
            if (!groups.has(title)) {
              const group = document.createElement("optgroup");
              group.label = title;
              groups.set(title, group);
            }
            groups.get(title).append(option(item.id, item.label));
          },
        );
      $("hair-template-select").replaceChildren(
        option("", "Choose a hairstyle"),
        ...groups.values(),
      );
      palette.forEach((c) => selection.add(colorKey(c)));
      setupSource();
      message(sourcePrompt(), false, true);
    })
    .catch((error) => message(error.message, true, true));

  runtime
    .then((r) => {
      r.events.addEventListener("job", (event) => {
        if (
          event.detail.id === savedRecord?.id &&
          event.detail.state !== "draft"
        )
          report(event.detail);
      });
      r.events.addEventListener("deleted", (event) => {
        if (event.detail === savedRecord?.id) {
          resetJob();
          saveNote.textContent = "Batch deleted.";
        }
      });
      r.savedPanel($("hair-form"), "hair", async (record: SavedJob) => {
        restoring = true;
        try {
          clearTimeout(saveTimer);
          await saveChain.catch(() => {});
          resetJob();
          savedRecord = record;
          compression.restore(
            record.parameters.texture_encoder,
            record.parameters.refpack_compression,
          );
          job = record;
          uploadedFiles = record.files;
          selectedTemplate = {
            ...record.template,
            inspection: await r.openHair(
              required(record.template, "hair template"),
              record.files,
              record.manifest,
            ),
          };
          $("hair-mesh-source").value =
            required(record.template, "record.template").kind === "custom"
              ? "upload"
              : "standard";
          $("hair-template-select").value = required(
            record.template,
            "record.template",
          ).id;
          uploadFields.forEach((id: string) => {
            $(id).required = false;
          });
          $("hair-creator").value = record.parameters.creator;
          $("hair-name").value = record.parameters.hair_name;
          curveEditor.clear();
          curveEditor.colors = structuredClone(record.ui.customColors || []);
          palette = [...record.manifest.hair.palette, ...curveEditor.colors];
          selection.clear();
          record.parameters.colors.forEach((c: string) => selection.add(c));
          bases.clear();
          record.ui.bases.forEach(([k, v]: [string, Metadata]) =>
            bases.set(k, v),
          );
          installed = record.manifest.hair;
          setupSource();
          // Saved files have already been revalidated. File inputs cannot be restored.
          uploadFields.forEach((id: string) => {
            $(id).required = false;
          });
          saveNote.textContent =
            "Restored from this browser. Source files are saved with the batch.";
          if (record.state !== "draft") {
            report(record);
            await prepare(true);
          } else message("Saved batch opened. Preview colors to continue.");
        } finally {
          restoring = false;
        }
      });
    })
    .catch((e) => {
      saveNote.textContent = e.message;
    });
  setupSource();
})();
