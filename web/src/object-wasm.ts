import { createAutosaveQueue } from "./shared/autosave.js";
import type { ObjectPreview } from "./object-preview.js";
import type {
  Metadata,
  RuntimeManifest,
  SavedJob,
} from "./package-runtime/types.js";
import { prefixedControls, required } from "./shared/dom.js";
import type { Layout } from "./shared/scene-types.js";
import { TextureCompression } from "./texture-compression.js";
export let activateObject: () => Promise<void>;

/* Object inputs are sent only to the local WASM worker. */
(() => {
  const $ = prefixedControls("object-");
  let runtime: typeof import("./package-runtime/client.js"),
    manifest: RuntimeManifest,
    record: SavedJob | null,
    inspection: Metadata | null,
    viewer: ObjectPreview,
    busy = false,
    timer: number | undefined,
    revision = 0;
  let saving: Promise<unknown> = Promise.resolve(),
    priceEdited = false,
    heightEdited = false,
    initialized = false;
  let exactHeight: number | null = null,
    heightFromFit = false;
  let optimizationController: AbortController | null,
    layout: Metadata | null,
    layoutTimer: number | undefined,
    layoutSequence = 0;
  const compression = TextureCompression.mount(
    "object",
    $("build").parentElement,
    (kind) => {
      inputChanged(
        kind === "refpack" ? "refpack-compression" : "texture-encoder",
      );
      if (kind !== "refpack")
        $("preview-status").textContent =
          "Compression changed. Update preview to see the package texture.";
    },
  );
  const valueIds = [
    "source",
    "standard",
    "creator",
    "name",
    "title",
    "price",
    "description",
    "height",
    "rotation",
    "environment",
    "mannequin",
    "target-triangles",
    "detail-protection",
  ];
  const checkIds = ["footprint", "dimension-labels", "placement-ack"];
  const api = async () =>
    runtime || (runtime = await import("./package-runtime/client.js"));
  const message = (text: string | null, target = "status") => {
    $(target).textContent = text;
  };
  const savedModel = () =>
    record?.files?.find(
      (f) => f.name === required(record, "record").parameters?.model_file,
    );
  const hasModel = () => !!$("model").files?.[0] || !!savedModel();
  const selectedTemplate = () =>
    $("source").value === "standard"
      ? manifest?.objects?.items.find(
          (i: Metadata) => i.id === $("standard").value,
        )
      : inspection;
  function originalHeight() {
    const height = selectedTemplate()?.dimensions?.height;
    return Number.isFinite(height) && height > 0 ? height : null;
  }
  function fitTemplateHeight() {
    const height = originalHeight();
    if (height === null) return false;
    exactHeight = height;
    heightEdited = false;
    $("height").value = (
      (height / manifest.objects.reference.reference_height) *
      100
    ).toFixed(2);
    return true;
  }
  function invalidatePreview() {
    layoutSequence++;
    clearTimeout(layoutTimer);
    layout = null;
    $("placement-ack").checked = false;
    $("placement-warning").hidden = true;
    $("dimensions").textContent = "";
    viewer?.clear();
    $("viewer").hidden = true;
    $("preview-empty").hidden = false;
    $("preview-status").textContent = "";
  }
  function modelControlsValid() {
    if (!hasModel()) {
      $("model").reportValidity();
      return false;
    }
    return ["height", "rotation"].every((id) => $(id).reportValidity());
  }
  const templateKind = (kind: string) =>
    kind === "floor-decor" ? "Floor decoration" : "Tabletop decoration";
  const templateDescriptions: Record<string, string> = {
    urn: "A compact urn with a one-tile base. Useful for statues and floor props with a small base.",
    "fruit-bowl":
      "A small tabletop decoration. Useful for bowls, ornaments and other props displayed on a surface.",
    venus:
      "A tall pedestal sculpture with a one-tile base. Useful for narrow statues and standing decorations.",
    chimes:
      "A wide sculpture with a two-tile base. Useful for models that need more floor space in one direction.",
    chi: "A large sculpture with a square, four-tile base. Useful for broad statues and bulky floor decorations.",
  };
  let templateInfoKey: string;
  function templateInfo() {
    const standard = $("source").value === "standard";
    const selected = selectedTemplate();
    const key = JSON.stringify([standard, selected || null]);
    if (key === templateInfoKey) return;
    templateInfoKey = key;
    $("template-info").hidden = !selected;
    $("template-note").hidden = !!selected;
    $("template-note").textContent = standard
      ? "Loading template details…"
      : "Choose your packages, then check the template to see its placement and behavior.";
    if (!selected) return;
    const tabletop = selected.kind === "tabletop-decor",
      placement = selected.placement;
    const tiles = placement?.tiles || [],
      count = placement?.tile_count;
    let footprint = "Unknown. Review the placement warning before building.";
    if (placement?.known && count > 0) {
      const width = tiles.length
        ? Math.max(...tiles.map((t: number[]) => t[0])) -
          Math.min(...tiles.map((t: number[]) => t[0])) +
          1
        : 0;
      const depth = tiles.length
        ? Math.max(...tiles.map((t: number[]) => t[1])) -
          Math.min(...tiles.map((t: number[]) => t[1])) +
          1
        : 0;
      const rectangle = width * depth === count;
      footprint = `${rectangle ? `${width} × ${depth} tiles, ` : ""}${count} occupied tile${count === 1 ? "" : "s"}${tabletop ? " when on the floor" : ""}`;
    }
    $("template-title").textContent = standard
      ? selected.label
      : "Uploaded template";
    $("template-summary").textContent = standard
      ? templateDescriptions[selected.id] ||
        `A ${templateKind(selected.kind).toLowerCase()} template for your imported model.`
      : `Uses the ${selected.label} decoration behavior detected in your package.`;
    const facts = [
      [
        "In-game placement",
        tabletop ? "Tables, counters or the floor" : "On the floor",
      ],
      ["Occupied area", footprint],
      [
        "Original object height",
        originalHeight() === null
          ? "Available after template inspection"
          : `${originalHeight().toFixed(3)} game units (${((originalHeight() / manifest.objects.reference.reference_height) * 100).toFixed(2)}% of the reference Sim)`,
      ],
      [
        "Requires",
        selected.requirements?.replace(" (EP9 behavior profile)", "") ||
          "Check the original creator's game requirements.",
      ],
    ];
    $("template-facts").replaceChildren(
      ...facts.map(([label, text]) => {
        const row = document.createElement("div"),
          term = document.createElement("dt"),
          detail = document.createElement("dd");
        term.textContent = label;
        detail.textContent = text;
        row.append(term, detail);
        return row;
      }),
    );
    $("template-behavior").textContent = tabletop
      ? "The preview table is a scale reference and is not included in your package. Place your object on a surface in the game. Some surface slots make decorations smaller."
      : "Your model keeps this template's decoration behavior and occupied tiles. Set its height independently below. Making it wider does not increase the space Sims avoid.";
  }
  function controls() {
    const frozen = !!record?.snapshotHash || record?.state === "building";
    for (const id of [...valueIds, ...checkIds, "packages", "model"])
      $(id).disabled = busy || frozen;
    for (const b of document.querySelectorAll<HTMLButtonElement>(
      "[data-object-height]",
    ))
      b.disabled = busy || frozen;
    $("fit").disabled =
      busy || frozen || !hasModel() || originalHeight() === null;
    const fittedPercent =
      (heightFromFit || !heightEdited) && exactHeight !== null
        ? Number($("height").value)
        : 100;
    $("height").min = String(Math.min(10, fittedPercent));
    $("height").max = String(Math.max(300, fittedPercent));
    // Display controls remain usable for completed batches.
    for (const id of [
      "environment",
      "mannequin",
      "footprint",
      "dimension-labels",
    ])
      $(id).disabled = busy;
    $("inspect").disabled = busy || frozen;
    $("build").disabled =
      busy ||
      record?.state === "building" ||
      record?.state === "complete" ||
      !hasModel();
    $("preview").disabled = busy || record?.state === "building" || !hasModel();
    $("cancel").hidden = record?.state !== "building";
    $("build").textContent =
      frozen && record?.state !== "complete"
        ? "Resume build"
        : "Create package";
    $("standard-label").hidden = $("source").value !== "standard";
    $("upload").hidden = $("source").value !== "upload";
    $("model").required = !savedModel();
    $("optimize").disabled =
      busy || frozen || !hasModel() || !manifest?.assets?.["object-optimizer"];
    $("optimize-cancel").hidden = !optimizationController;
    $("use-original").hidden = !record?.optimization;
    $("use-original").disabled = busy || frozen;
    compression.update({
      manifest,
      record,
      formats: hasModel() ? ["DXT3"] : [],
      locked: busy || frozen,
    });
    templateInfo();
    const model = $("model").files?.[0] || savedModel();
    $("model-note").textContent = model
      ? `Using ${"filename" in model ? model.filename : model.name}. Your model stays in this browser.`
      : "Choose a GLB, or a ZIP containing one glTF scene and its textures.";
    $("filename").textContent =
      `${$("creator").value || "Creator"}_${$("name").value || "Object"}.package · Place the downloaded file in your Sims 2 Downloads folder.`;
  }
  function clearOptimization() {
    const link = $("optimized-download");
    if (link.href.startsWith("blob:")) URL.revokeObjectURL(link.href);
    link.removeAttribute("href");
    link.hidden = true;
    $("optimization-result").replaceChildren();
    $("optimization-result").hidden = true;
    $("optimization-status").textContent = "";
  }
  async function showOptimization(report = record?.optimization) {
    clearOptimization();
    if (!report) return;
    const table = document.createElement("table");
    const caption = document.createElement("caption");
    caption.textContent = "Model optimization";
    table.append(caption);
    for (const [label, a, b] of [
      ["", "Original", "Optimized"],
      [
        "Triangles",
        report.before.triangles.toLocaleString(),
        report.after.triangles.toLocaleString(),
      ],
      ["Material groups", report.before.groups, report.after.groups],
      [
        "File size",
        `${(report.before.bytes / 1024 ** 2).toFixed(2)} MiB`,
        `${(report.after.bytes / 1024 ** 2).toFixed(2)} MiB`,
      ],
    ]) {
      const row = table.insertRow();
      for (const [i, text] of [label, a, b].entries()) {
        const cell = document.createElement(
          i === 0 || label === "" ? "th" : "td",
        );
        if (cell.tagName === "TH")
          (cell as HTMLTableCellElement).scope = label === "" ? "col" : "row";
        cell.textContent = text;
        row.append(cell);
      }
    }
    $("optimization-result").append(table);
    $("optimization-result").hidden = false;
    $("optimization-status").textContent = report.compatible
      ? `Using the optimized model for preview and package creation.${report.targetReached ? "" : " Detail protection kept more triangles than the target."}`
      : `${report.issues.join(" ")} The current model was kept.`;
    if (report.file) {
      const blob = await (await api()).store.getBlob(report.file.blob);
      if (!blob)
        throw new Error(
          "The saved optimized model is missing. The original file is kept.",
        );
      const link = $("optimized-download");
      link.href = URL.createObjectURL(blob);
      link.download = report.file.filename;
      link.hidden = false;
    }
  }
  function parameters() {
    return {
      ...compression.payload(),
      creator: $("creator").value.trim(),
      object_name: $("name").value.trim(),
      title: $("title").value.trim(),
      description: $("description").value,
      price: Number($("price").value),
      mode: "model",
      sizing_version: 2,
      target_height:
        exactHeight ??
        (Number($("height").value) / 100) *
          manifest.objects.reference.reference_height,
      fit_to_template: false,
      height_from_fit: heightFromFit,
      placement_ack: $("placement-ack").checked ? layout?.signature : null,
      rotation: Number($("rotation").value),
      model_file: $("model").files?.[0]
        ? $("model").files?.[0]?.name.toLowerCase().endsWith(".zip")
          ? "model.zip"
          : "model.glb"
        : record?.parameters?.model_file,
    };
  }
  function items() {
    const previous = $("standard").value;
    $("standard").replaceChildren();
    for (const item of manifest?.objects?.items || []) {
      if (!["floor-decor", "tabletop-decor"].includes(item.kind)) continue;
      $("standard").add(new Option(item.label, item.id));
    }
    if ([...$("standard").options].some((o) => o.value === previous))
      $("standard").value = previous;
  }
  async function draft({ save = false, requireModel = false } = {}) {
    const settings = parameters(),
      ui = Object.fromEntries([
        ...valueIds.map((id) => [id, $(id).value]),
        ...checkIds.map((id) => [id, $(id).checked]),
      ]);
    ui.height_mode = heightFromFit
      ? "fitted"
      : heightEdited
        ? "custom"
        : "template";
    const picked = [...($("packages").files || [])],
      model = $("model").files?.[0];
    const r = await api();
    manifest ||= await r.manifest();
    const template =
      ui.source === "standard"
        ? manifest.objects.items.find((i: Metadata) => i.id === ui.standard)
        : null;
    if (ui.source === "standard" && !template)
      throw new Error("Choose a decoration template.");
    let files = record?.files || [];
    if (picked.length || model) {
      const existing = files.filter(
        (f) =>
          !(
            (picked.length && f.name.toLowerCase().endsWith(".package")) ||
            (model && f.name.startsWith("model."))
          ),
      );
      files = [
        ...existing,
        ...(await r.saveFiles([
          ...picked,
          ...(model ? [{ file: model, name: settings.model_file }] : []),
        ])),
      ];
    }
    if (template)
      files = files.filter((f) => !f.name.toLowerCase().endsWith(".package"));
    if (
      !template &&
      !files.some((f) => f.name.toLowerCase().endsWith(".package"))
    )
      throw new Error(
        "Choose a template package and its matching dependencies.",
      );
    if (requireModel && !files.some((f) => f.name === settings.model_file))
      throw new Error("Choose a GLB or model ZIP for your new object.");
    const next = {
      ...(record || {
        id: r.store.id(),
        kind: "object" as const,
        state: "draft" as const,
        schema_version: 1,
        revision: 0,
        updated: 0,
      }),
      manifest,
      template,
      files,
      parameters: settings,
      ui,
      ...(model ? { modelSource: null, optimization: null } : {}),
    };
    if (save) {
      next.revision += 1;
      record = await r.saveDraft(next);
      return record;
    }
    return next;
  }
  async function inspect({ requireModel = false } = {}) {
    inspection = null;
    const r = await api(),
      next = await draft({ requireModel });
    inspection = await r.openObject(next);
    if (!inspection.can_import_model) {
      inspection = null;
      throw new Error(
        "This template cannot accept an imported model. Choose a floor or tabletop decoration template.",
      );
    }
    if (!heightEdited) fitTemplateHeight();
    $("inspection").textContent =
      `Template ready: ${inspection.label}. ${templateKind(inspection.kind)}. ${inspection.requirements}`;
    if (!priceEdited && !record?.snapshotHash)
      $("price").value =
        inspection.objects.find(
          (o: Metadata) => o.tile === 65535 || o.master === 0,
        )?.price ?? 150;
    return next;
  }
  async function action(fn: () => Promise<unknown>, target = "status") {
    if (busy) return;
    clearTimeout(timer);
    busy = true;
    controls();
    try {
      await saving;
      await fn();
    } catch (eCause) {
      const e = eCause instanceof Error ? eCause : new Error(String(eCause));
      message(
        e?.message ||
          (e == null
            ? "The browser could not complete this operation. Check available storage and try again."
            : String(e)),
        target,
      );
    } finally {
      busy = false;
      controls();
    }
  }
  async function show(job: SavedJob) {
    record = job;
    const p = job.progress;
    $("progress").hidden = job.state !== "building";
    $("progress").value = Number(typeof p === "object" ? p.completed || 0 : 0);
    message(
      job.error?.message ||
        (job.state === "complete"
          ? "Package is ready. Structural checks passed. Gameplay has not been tested."
          : job.state === "building"
            ? "Creating and validating the object in your browser…"
            : job.state === "draft"
              ? "Saved in this browser."
              : "Saved build can be resumed with the same GUIDs."),
    );
    if (job.state === "complete")
      await (
        await api()
      ).bindDownload($("download") as unknown as HTMLAnchorElement, job.id);
    else $("download").hidden = true;
    controls();
  }
  $("optimize").addEventListener("click", () =>
    action(async () => {
      if (!$("target-triangles").reportValidity()) return;
      await draft({ save: true, requireModel: true });
      const source =
        required(record, "record").modelSource ||
        required(record, "record").files.find((f) =>
          f.name.startsWith("model."),
        );
      if (!source) throw new Error("Choose a GLB or model ZIP first.");
      optimizationController = new AbortController();
      controls();
      try {
        const r = await api();
        const result = await r.optimizeObjectModel(
          required(record, "batch"),
          source,
          {
            triangles: Number($("target-triangles").value),
            error: Number($("detail-protection").value),
          },
          {
            signal: optimizationController.signal,
            onProgress: (text) => {
              $("optimization-status").textContent = text;
            },
          },
        );
        if (!result.report.compatible) {
          await showOptimization(result.report);
          return;
        }
        const filename =
          source.filename.replace(/\.(glb|zip)$/i, "") + "_optimized.glb";
        const [file] = await r.saveFiles([
          {
            name: "model.glb",
            file: new File([result.bytes], filename, {
              type: "model/gltf-binary",
            }),
          },
        ]);
        const next = {
          ...record,
          revision: required(record, "record").revision + 1,
          modelSource: source,
          files: [
            ...required(record, "record").files.filter(
              (f) => !f.name.startsWith("model."),
            ),
            file,
          ],
          parameters: {
            ...required(record, "record").parameters,
            model_file: "model.glb",
          },
          optimization: { ...result.report, file },
        };
        // Publish only after local persistence succeeds. The input picker must no
        // longer override the optimized descriptor during subsequent autosaves.
        record = await r.saveDraft(next);
        $("model").value = "";
        invalidatePreview();
        await showOptimization();
        message(
          "Model optimized and saved locally. Preview it before creating the package.",
        );
      } catch (eCause) {
        const e = eCause instanceof Error ? eCause : new Error(String(eCause));
        $("optimization-status").textContent = e.message;
        throw e;
      } finally {
        optimizationController = null;
        controls();
      }
    }),
  );
  $("optimize-cancel").addEventListener("click", () =>
    optimizationController?.abort(),
  );
  $("optimize-options").addEventListener("submit", (e) => {
    e.preventDefault();
    $("optimize").click();
  });
  $("use-original").addEventListener("click", () =>
    action(async () => {
      const source = required(record, "record").modelSource;
      if (!source)
        throw new Error("No original model is saved for this batch.");
      const next = {
        ...record,
        revision: required(record, "record").revision + 1,
        optimization: null,
        files: [
          ...required(record, "record").files.filter(
            (f) => !f.name.startsWith("model."),
          ),
          source,
        ],
        parameters: {
          ...required(record, "record").parameters,
          model_file: source.name,
        },
      };
      record = await (await api()).saveDraft(next);
      $("model").value = "";
      invalidatePreview();
      clearOptimization();
      message(
        "Using the original model. You can change the optimization settings and try again.",
      );
    }),
  );
  $("inspect").addEventListener("click", () =>
    action(async () => {
      await inspect();
      await draft({ save: true });
    }, "inspection"),
  );
  const displayOptions = () => ({
    environment: $("environment").value,
    mannequin: $("mannequin").value,
    footprint: $("footprint").checked,
    dimensions: $("dimension-labels").checked,
  });
  function showLayout(result: Layout) {
    if (layout?.signature !== result.signature)
      $("placement-ack").checked = false;
    layout = result;
    const d = result.dimensions;
    $("dimensions").textContent =
      `${d.width.toFixed(2)} wide × ${d.depth.toFixed(2)} deep × ${d.height.toFixed(2)} high in game units. Template: ${result.placement.tile_count} occupied floor tile${result.placement.tile_count === 1 ? "" : "s"}.${result.placement.surface === "tabletop" ? " Table elevation is preview-only. Some in-game surface slots also scale decorations." : ""}`;
    $("placement-warning").hidden = !result.requires_acknowledgement;
    $("placement-message").textContent = result.warning;
    if (viewer?.models.size) viewer.updateLayout(result);
  }
  async function refreshLayout() {
    if (
      !hasModel() ||
      !$("height").validity.valid ||
      !$("rotation").validity.valid
    )
      return;
    const seq = ++layoutSequence,
      rev = revision;
    const next = await draft(),
      result = await (await api()).objectLayout(next);
    if (seq !== layoutSequence) return;
    if (rev !== revision) {
      clearTimeout(layoutTimer);
      layoutTimer = setTimeout(
        () =>
          refreshLayout().catch((e) => message(e.message, "preview-status")),
        80,
      );
      return;
    }
    showLayout(result);
  }
  $("preview").addEventListener("click", () => {
    if (!record?.snapshotHash && !modelControlsValid()) return;
    void action(async () => {
      message("Preparing the 3D preview in your browser…", "preview-status");
      let job = record;
      if (!record?.snapshotHash) {
        await inspect({ requireModel: true });
        await draft({ save: true, requireModel: true });
        job = {
          ...required(record, "batch"),
          parameters: {
            ...required(record, "record").parameters,
            creator: "Preview",
            object_name: "Preview",
            title: "Model preview",
            description: "",
            price: 150,
          },
        };
      }
      const r = await api(),
        result = await r.objectPreview(required(job, "batch")),
        refs = await r.objectReferences(required(job, "job").manifest);
      const { ObjectPreview } = await import("./object-preview.js");
      $("viewer").hidden = false;
      viewer ||= new ObjectPreview($("viewer"));
      await viewer.show(result, refs, displayOptions());
      showLayout(result.layout);
      $("preview-empty").hidden = true;
      message(
        "Preview ready. Height and rotation now update immediately.",
        "preview-status",
      );
    }, "preview-status");
  });
  $("form").addEventListener("submit", (e) => {
    e.preventDefault();
    void action(async () => {
      clearTimeout(timer);
      if (!record?.snapshotHash) {
        await inspect({ requireModel: true });
        await refreshLayout();
        if (layout?.requires_acknowledgement && !$("placement-ack").checked)
          throw new Error(
            "Review the placement warning above and acknowledge it before creating the package.",
          );
        await draft({ save: true, requireModel: true });
      }
      const r = await api();
      await show(await r.start(required(record, "batch")));
    });
  });
  $("cancel").addEventListener("click", () =>
    action(async () => {
      await show(await (await api()).cancel(required(record, "record").id));
    }),
  );
  $("new").addEventListener("click", () =>
    action(async () => {
      if (record?.state === "building")
        throw new Error(
          "Finish or cancel this build before starting a new batch.",
        );
      revision++;
      clearTimeout(timer);
      record = null;
      compression.reset();
      inspection = null;
      priceEdited = false;
      heightEdited = false;
      exactHeight = null;
      heightFromFit = false;
      $("form").reset();
      $("optimize-options").reset();
      manifest = await (await api()).manifest();
      items();
      fitTemplateHeight();
      invalidatePreview();
      clearOptimization();
      $("download").hidden = true;
      $("inspection").textContent = "";
      message("New batch. Existing saved batches are kept.");
    }),
  );
  function inputChanged(id: string) {
    if (record?.snapshotHash) {
      viewer?.setOptions(displayOptions());
      return;
    }
    if (id === "price") priceEdited = true;
    if (id === "height") {
      heightEdited = true;
      exactHeight = null;
      heightFromFit = false;
    }
    if (id === "model") {
      if (record) {
        record.modelSource = null;
        record.optimization = null;
      }
      clearOptimization();
    }
    if (["model", "source", "standard", "packages"].includes(id))
      invalidatePreview();
    if (["height", "rotation"].includes(id)) {
      $("placement-ack").checked = false;
      layoutSequence++;
      clearTimeout(layoutTimer);
      if (hasModel() && (viewer?.models.size || inspection || heightFromFit))
        layoutTimer = setTimeout(
          () =>
            refreshLayout().catch((e) => message(e.message, "preview-status")),
          80,
        );
    }
    if (
      ["environment", "mannequin", "footprint", "dimension-labels"].includes(id)
    ) {
      viewer?.setOptions(displayOptions());
      if (record?.snapshotHash) return;
    }
    revision++;
    const current = revision;
    clearTimeout(timer);
    if (["source", "standard", "packages"].includes(id)) {
      inspection = null;
      $("inspection").textContent = "";
      heightEdited = false;
      exactHeight = null;
      heightFromFit = false;
      fitTemplateHeight();
    }
    controls();
    scheduleSave(current);
  }
  const autosaves = createAutosaveQueue<void>();
  function scheduleSave(current = revision) {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      if (busy || current !== revision) return;
      saving = autosaves.enqueue(async () => {
        if (current !== revision) return;
        try {
          await draft({ save: true });
          if (current === revision && !busy) message("Saved in this browser.");
        } catch (e) {
          if (current === revision)
            message(
              `Could not save this batch: ${e instanceof Error ? e.message : String(e)}`,
            );
          // Autosave errors are shown above. Explicit actions retry their own
          // draft write, as they did before autosaves were coalesced.
        }
      });
      await saving.catch(() => {});
    }, 700);
  }
  for (const id of [...valueIds, ...checkIds, "packages", "model"])
    $(id).addEventListener("input", () => inputChanged(id));
  activateObject = async () => {
    if (initialized) return;
    const r = await api();
    manifest = await r.manifest();
    if (!manifest.objects?.items?.length)
      throw new Error(
        "Object templates are not installed in this engine release.",
      );
    initialized = true;
    items();
    fitTemplateHeight();
    r.events.addEventListener("job", (e) => {
      if (e.detail.id === record?.id)
        show(e.detail).catch((cause) => message(cause.message));
    });
    r.events.addEventListener("deleted", (e) => {
      if (e.detail !== record?.id) return;
      record = null;
      inspection = null;
      clearOptimization();
      invalidatePreview();
      const link = $("download");
      if (link.href.startsWith("blob:")) URL.revokeObjectURL(link.href);
      link.removeAttribute("href");
      link.hidden = true;
      controls();
      message("Saved batch deleted.");
    });
    r.savedPanel($("saved"), "object", async (job: SavedJob) => {
      if (busy) throw new Error("Wait for the current object operation.");
      if (
        job.parameters?.mode !== "model" ||
        job.parameters?.sizing_version !== 2
      )
        throw new Error(
          "This batch uses the previous sizing system. Its saved files and engine are kept. Download completed packages from Saved batches, or start a new batch to use height controls.",
        );
      clearTimeout(timer);
      await saving;
      if (!job.snapshotHash && job.parameters.fit_to_template === true) {
        if (
          !Number.isFinite(job.parameters.target_height) ||
          job.parameters.target_height <= 0
        )
          throw new Error(
            "This saved batch has no valid fitted height. Its inputs are kept. Start a new batch to fit the model again.",
          );
        job = await fixedHeightEngine(job);
        job = await r.saveDraft({
          ...job,
          revision: job.revision + 1,
          parameters: {
            ...job.parameters,
            fit_to_template: false,
            height_from_fit: true,
          },
          ui: { ...job.ui, height_mode: "fitted" },
        });
      }
      manifest = job.manifest;
      priceEdited = true;
      heightEdited = job.ui.height_mode !== "template";
      heightFromFit =
        job.parameters.height_from_fit === true ||
        job.parameters.fit_to_template === true;
      exactHeight = job.parameters.target_height;
      $("form").reset();
      $("optimize-options").reset();
      compression.restore(
        job.parameters.texture_encoder,
        job.parameters.refpack_compression,
      );
      for (const id of valueIds)
        if (id !== "standard") $(id).value = job.ui[id] ?? $(id).value;
      $("height").value = (
        (job.parameters.target_height /
          job.manifest.objects.reference.reference_height) *
        100
      ).toFixed(2);
      for (const id of checkIds) $(id).checked = job.ui[id] ?? true;
      items();
      $("standard").value = job.ui.standard;
      $("packages").value = "";
      $("model").value = "";
      inspection = null;
      invalidatePreview();
      await show(job);
      inspection = await r.openObject(job);
      if (!job.snapshotHash && !heightEdited && fitTemplateHeight()) {
        await draft({ save: true });
        job = required(record, "batch");
      }
      templateInfo();
      if (hasModel()) {
        showLayout(await r.objectLayout(job));
        $("placement-ack").checked =
          job.parameters.placement_ack === required(layout, "layout").signature;
      }
      await showOptimization();
      $("inspection").textContent =
        `Template ready: ${inspection.label}. ${inspection.requirements}`;
    });
  };
  for (const b of document.querySelectorAll<HTMLButtonElement>(
    "[data-object-height]",
  ))
    b.addEventListener("click", () => {
      $("height").value = b.dataset.objectHeight || "100";
      $("height").dispatchEvent(new Event("input", { bubbles: true }));
    });
  async function fixedHeightEngine(next: SavedJob) {
    const r = await api();
    const latest =
      next.manifest.objects?.fixed_fitted_height === 1
        ? next.manifest
        : await r.manifest({ refresh: true });
    if (latest.objects?.fixed_fitted_height !== 1)
      throw new Error(
        "Reload the website to load the engine that preserves fitted height during rotation.",
      );
    let template = next.template;
    if (template) {
      const updated = latest.objects.items.find(
        (t: Metadata) => t.id === required(template, "template").id,
      );
      if (
        !updated ||
        latest.assets[updated.asset]?.sha256 !==
          next.manifest.assets[template.asset]?.sha256
      )
        throw new Error(
          "This template changed in the newer engine. Your saved batch is kept. Start a new batch to use its current version.",
        );
      template = updated;
    }
    return { ...next, manifest: latest, template };
  }
  $("fit").addEventListener("click", () =>
    action(async () => {
      if (record?.snapshotHash)
        throw new Error(
          "Start a new batch to resize a completed or interrupted build.",
        );
      message(
        "Fitting the model to the template's height and occupied tiles…",
        "preview-status",
      );
      const r = await api(),
        next = await fixedHeightEngine(await draft({ requireModel: true }));
      next.parameters = {
        ...next.parameters,
        fit_to_template: true,
        height_from_fit: false,
        placement_ack: null,
      };
      const result = await r.objectLayout(next);
      next.parameters = {
        ...next.parameters,
        target_height: result.height,
        fit_to_template: false,
        height_from_fit: true,
      };
      next.ui = {
        ...next.ui,
        height_mode: "fitted",
        height: (
          (result.height / next.manifest.objects.reference.reference_height) *
          100
        ).toFixed(2),
        "placement-ack": false,
      };
      next.revision++;
      record = await r.saveDraft(next);
      manifest = next.manifest;
      heightFromFit = true;
      heightEdited = true;
      exactHeight = result.height;
      $("height").value = next.ui.height;
      revision++;
      layoutSequence++;
      clearTimeout(layoutTimer);
      $("placement-ack").checked = false;
      showLayout({ ...result, fit_to_template: false, height_from_fit: true });
      message(
        "Object fitted to the template's height and occupied floor area. Proportions are preserved.",
        "preview-status",
      );
    }, "preview-status"),
  );
  controls();
})();
