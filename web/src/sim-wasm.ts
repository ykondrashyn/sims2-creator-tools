export let activateSim: () => Promise<void>;
import { createAutosaveQueue } from "./shared/autosave.js";
import type {
  Metadata,
  RuntimeManifest,
  SavedJob,
} from "./package-runtime/types.js";
import { prefixedControls, required } from "./shared/dom.js";
import type { EditMarker, SimPreview } from "./sim-preview.js";
import { TextureCompression } from "./texture-compression.js";
/* Guided fitting v2. Input geometry, guides and saved work remain in this browser. */
(() => {
  const $ = prefixedControls("sim-"),
    stages = ["align", "markers", "head", "check"];
  interface Guides {
    guided_version: number;
    alignment: number[];
    pose: string;
    markers: Record<string, [number, number, number]>;
    neck_height: number;
    roles: Record<string, string>;
    review: Record<string, boolean>;
  }
  const clone = <T>(v: T): T => structuredClone(v);
  let runtime: typeof import("./package-runtime/client.js"),
    manifest: RuntimeManifest,
    record: SavedJob | null,
    info: Metadata | null,
    viewer: SimPreview | null,
    source: Metadata | null,
    fit: Metadata | null,
    busy = false,
    initialized = false,
    timer: number | undefined,
    sequence = 0,
    saving: Promise<unknown> = Promise.resolve();
  const compression = TextureCompression.mount(
    "sim",
    $("build").parentElement,
    () => schedule(),
  );
  let state = defaults(),
    undo: Guides[] = [],
    redo: Guides[] = [],
    dragSnapshot: Guides | null | undefined,
    selected = "neck",
    selectedPart: string | number | null = null,
    legacy = false,
    lastFit: string | null = null,
    pendingTask: { cancelled: any } | null = null,
    autoDownload: string | null = null;
  $("tool").addEventListener("creatorvisibilitychange", (event: Event) => {
    viewer?.setVisible(
      (event as CustomEvent<{ visible: boolean }>).detail.visible,
    );
  });
  function defaults(): Guides {
    return {
      guided_version: 2,
      alignment: [0, 0, 0],
      pose: "t",
      markers: {},
      neck_height: 1.55,
      roles: {},
      review: { align: false, markers: false, head: false, check: false },
    };
  }
  const message = (text: string | null, id = "status") => {
    $(id).textContent = text;
  };
  const frozen = () =>
    legacy || !!record?.snapshotHash || record?.state === "complete";
  const stage = () => $("fitting").dataset.stage || "align";
  function params() {
    return {
      ...clone(state),
      ...compression.payload(),
      body: $("body").value,
      creator: $("creator").value,
      sim_name: $("name").value,
      description: $("description").value,
      experimental_everyday: $("experimental").checked,
      model_file: record?.files?.[0]?.name,
    };
  }
  function display() {
    return {
      stage: stage(),
      view: viewer?.mode || "orbit",
      marker_group: $("marker-group").value,
      mirror: $("mirror").checked,
      morph: $("morph").value,
      motion: $("motion").value,
      comparison: $("comparison").value,
      environment: $("environment").value,
      mannequin: $("mannequin").value,
      skeleton: $("reference").checked,
      issues: $("issues").checked,
    };
  }
  function signature() {
    const p = clone(state);
    const { review, ...fitting } = p;
    return JSON.stringify([fitting, $("body").value]);
  }
  function controls() {
    const blocked = busy || frozen();
    for (const el of $("form").querySelectorAll<HTMLInputElement>(
      "input,select,textarea,button",
    ))
      el.disabled = blocked;
    for (const id of ["new", "copy"]) $(id).disabled = busy;
    $("cancel").disabled = false;
    $("cancel").hidden = !busy;
    $("build").disabled =
      blocked ||
      !fit ||
      lastFit !== signature() ||
      !state.review.check ||
      !$("experimental").checked ||
      $("body").value !== "am" ||
      manifest?.sims?.everyday_test?.version !== 1;
    $("retry-build").hidden =
      !record?.snapshotHash ||
      !["failed", "cancelled", "interrupted"].includes(record.state);
    $("retry-build").disabled = busy;
    $("download").hidden = record?.state !== "complete";
    $("build-progress").hidden = record?.state !== "building";
    $("export-note").textContent =
      manifest?.sims?.everyday_test?.version !== 1
        ? "This saved engine predates test export. Start a new Sim to use it. Your existing draft is retained."
        : $("body").value !== "am"
          ? "Adult Female package export is not implemented yet. Fitting previews remain available."
          : "Use an isolated test profile. This exports the Everyday body with the stock head and hair. Install in SavedSims.";
    for (const b of $("fitting").querySelectorAll<HTMLButtonElement>(
      "[data-sim-stage]",
    ))
      b.disabled =
        blocked ||
        !info ||
        (b.dataset.simStage === "markers" && !state.review.align) ||
        (b.dataset.simStage === "head" && !state.review.markers) ||
        (b.dataset.simStage === "check" && !state.review.head && !fit);
    for (const el of $("fitting").querySelectorAll<HTMLInputElement>(
      "[data-sim-panel] input,[data-sim-panel] select,[data-sim-panel] button",
    ))
      el.disabled = blocked || !info;
    $("preview").disabled =
      blocked ||
      !info ||
      !state.review.align ||
      !state.review.markers ||
      !state.review.head;
    $("confirm-check").disabled = blocked || !fit || lastFit !== signature();
    $("comparison").options[1].disabled = !fit;
    $("undo").disabled = blocked || !undo.length;
    $("redo").disabled = blocked || !redo.length;
    $("reset-stage").disabled = blocked || !info;
    if (viewer) viewer.disabled = blocked || !info;
    $("filename").textContent =
      `${$("creator").value.replaceAll(" ", "_") || "Creator"}_${$("name").value.replaceAll(" ", "_") || "SimName"}.package`;
    $("stale").hidden = !fit || lastFit === signature();
    compression.update({
      manifest,
      record,
      formats: info ? ["DXT3"] : [],
      locked: blocked,
    });
  }
  const autosaves = createAutosaveQueue<void>();
  async function save() {
    if (!record || frozen()) return;
    const candidate = {
      ...record,
      revision: record.revision + 1,
      parameters: params(),
      ui: display(),
      template: manifest.sims.items.find(
        (i: { id: any }) => i.id === $("body").value,
      ),
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
          message("Saved in this browser.", "save-status");
        }
      } catch (e) {
        if (current())
          message(
            `Could not save: ${e instanceof Error ? e.message : String(e)}`,
            "save-status",
          );
        throw e;
      }
    });
    return saving;
  }
  function schedule() {
    controls();
    if (record && !frozen()) message("Saving…", "save-status");
    clearTimeout(timer);
    timer = setTimeout(() => save().catch(() => {}), 250);
  }
  function remember(previous = state) {
    undo.push(clone(previous));
    if (undo.length > 60) undo.shift();
    redo = [];
  }
  function invalidate(from: string) {
    for (let i = stages.indexOf(from); i < stages.length; i++)
      state.review[stages[i]] = false;
    $("naming").open = false;
    sequence++;
    schedule();
  }
  function redraw() {
    if (!viewer) return;
    viewer.setMarkers(
      state.markers,
      state.neck_height,
      state.roles,
      selected,
      $("marker-group").value,
    );
    for (const [i, id] of ["marker-x", "marker-y", "marker-z"].entries())
      $(id).value = state.markers[selected]?.[i]?.toFixed(3) || "";
    $("neck").value = String(state.neck_height);
    controls();
  }
  function markerOptions() {
    const g = $("marker-group").value;
    const keys = Object.keys(state.markers).filter((k) =>
      g === "torso"
        ? ["neck", "pelvis"].includes(k)
        : g === "arms"
          ? /shoulder|elbow|wrist/.test(k)
          : /hip|knee|ankle|toe/.test(k),
    );
    $("marker").replaceChildren(...keys.map((k) => new Option(label(k), k)));
    if (!keys.includes(selected)) selected = keys[0] || "neck";
    $("marker").value = selected;
    markerHelp();
    redraw();
  }
  function label(k: string) {
    return k
      .replace(/^l_/, "Left ")
      .replace(/^r_/, "Right ")
      .replaceAll("_", " ")
      .replace(/^./, (c: string) => c.toUpperCase());
  }
  function markerHelp() {
    const name = selected.replace(/^[lr]_/, "");
    const help: Record<string, string> = {
      neck: "Place the neck center where the head meets the body.",
      pelvis: "Place the pelvis center inside the hips.",
      shoulder: "Place the shoulder marker where the arm meets the torso.",
      elbow: "Place the elbow marker where the arm bends.",
      wrist: "Place the wrist marker where the hand meets the forearm.",
      hip: "Place the hip marker where the leg joins the pelvis.",
      knee: "Place the knee marker where the leg bends.",
      ankle: "Place the ankle marker where the foot meets the leg.",
      toe: "Place the toe marker at the base of the toes.",
    };
    $("marker-help").textContent = help[name] || "";
  }
  const editMarker: EditMarker = (id, value, commit, before) => {
    if (frozen() || busy) return;
    if (!dragSnapshot) {
      dragSnapshot = clone(state);
      if (before !== undefined) {
        if (typeof before === "number") dragSnapshot.neck_height = before;
        else dragSnapshot.markers[id] = before;
      }
    }
    if (id === "neck_boundary") {
      state.neck_height = Number(value);
      invalidate("head");
    } else if (Array.isArray(value)) {
      state.markers[id] = value.map((v: any) =>
        Math.max(-3.5, Math.min(3.5, Number(v))),
      ) as [number, number, number];
      if ($("mirror").checked && /^[lr]_/.test(id)) {
        const peer = (id[0] === "l" ? "r" : "l") + id.slice(1);
        state.markers[peer] = [-value[0], value[1], value[2]];
      }
      invalidate("markers");
    }
    redraw();
    if (commit) {
      remember(dragSnapshot);
      dragSnapshot = null;
      schedule();
    }
  };
  function selectMarker(id: string, kind?: string) {
    if (kind === "part") {
      selectedPart = id;
      $("part-choice").hidden = false;
      required(
        $("part-choice").firstChild,
        '$("part-choice").firstChild',
      ).textContent = `${id} `;
      $("part-role").value = state.roles[id] || "split";
      return;
    }
    selected = id;
    $("marker").value = id;
    markerHelp();
  }
  function setStage(next: string | undefined) {
    if (!next || !stages.includes(next)) return;
    $("fitting").dataset.stage = next;
    for (const panel of $("fitting").querySelectorAll<HTMLElement>(
      "[data-sim-panel]",
    ))
      panel.hidden = panel.dataset.simPanel !== next;
    for (const b of $("fitting").querySelectorAll<HTMLButtonElement>(
      "[data-sim-stage]",
    ))
      b.setAttribute("aria-pressed", String(b.dataset.simStage === next));
    viewer?.setStage(next);
    if (next !== "check") {
      $("comparison").value = "before";
      viewer?.setOptions({ comparison: "before", motion: "none" });
    } else viewer?.setOptions(display());
    if (["markers", "head"].includes(next)) setView("front");
    if (next === "check") setView("orbit");
    if (next === "markers") markerOptions();
    redraw();
    schedule();
  }
  function setView(view = "orbit") {
    viewer?.view(view);
    for (const b of $("form").querySelectorAll<HTMLElement>("[data-sim-view]"))
      b.setAttribute("aria-pressed", String(b.dataset.simView === view));
    schedule();
  }
  function feedback(report: { errors?: any; warnings?: any }) {
    $("feedback").replaceChildren();
    for (const item of [...(report.errors || []), ...(report.warnings || [])]) {
      const p = document.createElement("p");
      p.textContent = item.message;
      $("feedback").append(p);
    }
  }
  async function task(
    text: string,
    fn: (current: () => boolean) => Promise<void>,
  ) {
    if (busy) return;
    busy = true;
    const attempt = { cancelled: false };
    pendingTask = attempt;
    const seq = ++sequence;
    controls();
    $("progress").hidden = false;
    message(text);
    try {
      return await fn(() => seq === sequence);
    } catch (eCause) {
      const e = eCause instanceof Error ? eCause : new Error(String(eCause));
      message(
        attempt.cancelled
          ? "Fitting cancelled. Your model and guides are saved."
          : e.message,
      );
      return null;
    } finally {
      busy = false;
      if (pendingTask === attempt) pendingTask = null;
      $("progress").hidden = true;
      controls();
    }
  }
  async function operation(
    op: Parameters<typeof runtime.simOperation>[1],
    extra = {},
  ) {
    clearTimeout(timer);
    await save();
    await saving;
    if (pendingTask?.cancelled)
      throw new Error("Fitting cancelled. Your model and guides are saved.");
    return runtime.simOperation(required(record, "batch"), op, extra);
  }
  async function align(resetMarkers = true) {
    if (!record || legacy) return;
    await task(
      "Preparing the model and joint guides…",
      async (current: () => any) => {
        const result = await operation("sim_align", {
          include_images: !source,
        });
        if (!current()) return;
        source = result;
        required(info, "info").parts = required(info, "info").parts.map(
          (part: { id: any }) => ({
            ...part,
            component_count:
              result.parts.find((p: { id: any }) => p.id === part.id)
                ?.component_count || 1,
          }),
        );
        fit = null;
        lastFit = null;
        $("comparison").value = "before";
        if (resetMarkers || !Object.keys(state.markers).length) {
          state.markers = clone(result.suggestions);
          state.neck_height = state.markers.neck[2];
        }
        try {
          await required(viewer, "viewer").show(result, "before");
        } catch (eCause) {
          const e =
            eCause instanceof Error ? eCause : new Error(String(eCause));
          message(`3D preview unavailable: ${e.message}`);
        }
        if (!current()) return;
        markerOptions();
        parts();
        required(viewer, "viewer").setStage(stage());
        redraw();
        message(
          "Review the suggested joint positions. They are starting points, not detected anatomy.",
        );
        $("dimensions").textContent =
          `Height: ${((result.height / manifest.objects.reference.reference_height) * 100).toFixed(1)}% of the application's reference Sim. Each floor square is one game tile.`;
        await save();
      },
    );
  }
  async function applyFit() {
    await task(
      "Fitting body proportions and transferring game weights…",
      async (current: () => any) => {
        const result = await operation("sim_guided_fit", {
          include_images: false,
        });
        if (!current()) return;
        await required(viewer, "viewer").show(result, "after");
        if (!current()) return;
        fit = result;
        lastFit = signature();
        required(record, "record").fitReport = {
          version: 2,
          signature: lastFit,
          height: result.height,
          warnings: result.warnings,
          metrics: result.runtimeMetrics,
        };
        $("comparison").value = "after";
        required(viewer, "viewer").setOptions(display());
        feedback(result);
        $("dimensions").textContent =
          `Fitted height: ${((result.height / manifest.objects.reference.reference_height) * 100).toFixed(1)}% of the application's reference Sim. Each floor square is one game tile.`;
        message(
          "Fit ready. Compare the original and check head, arm and knee movement.",
        );
        await save();
      },
    );
  }
  function parts() {
    $("parts").replaceChildren();
    for (const part of (info?.parts || []).flatMap(
      (p: { component_count: number; id: any }) =>
        p.component_count > 1
          ? Array.from({ length: p.component_count }, (_, i) => ({
              ...p,
              id: `${p.id}#${i}`,
            }))
          : [p],
    )) {
      const labelEl = document.createElement("label");
      labelEl.textContent = part.id;
      const select = document.createElement("select");
      for (const [v, t] of [
        ["split", "Split at neck"],
        ["head", "Head only"],
        ["body", "Body only"],
      ])
        select.add(new Option(t, v));
      select.value = state.roles[part.id] || "split";
      select.addEventListener("change", () => {
        remember();
        state.roles[part.id] = select.value;
        invalidate("head");
        redraw();
      });
      labelEl.append(select);
      $("parts").append(labelEl);
    }
  }
  async function choose() {
    const file = $("model").files?.[0];
    if (!file) return;
    if (file.size > 64 * 1024 ** 2 || !/[.](glb|zip)$/i.test(file.name)) {
      message("Choose one GLB or glTF ZIP no larger than 64 MiB.");
      return;
    }
    await task("Inspecting and saving the model…", async () => {
      clearTimeout(timer);
      await saving.catch(() => {});
      const files = await runtime.saveFiles([
        {
          name: file.name.toLowerCase().endsWith(".zip")
            ? "sim-model.zip"
            : "sim-model.glb",
          file,
        },
      ]);
      const candidate = await runtime.saveDraft({
        kind: "sim",
        files,
        manifest,
        template: manifest.sims.items.find(
          (i: { id: any }) => i.id === $("body").value,
        ),
        parameters: { ...params(), ...defaults() },
      });
      const inspection = await runtime.openSim(candidate);
      record = candidate;
      info = inspection;
      legacy = false;
      state = defaults();
      source = null;
      fit = null;
      undo = [];
      redo = [];
      lastFit = null;
      $("legacy").hidden = true;
      required(viewer, "viewer").clearModels?.();
      setStage("align");
      message(
        `${file.name} · ${inspection.parts.length} material groups`,
        "model-status",
      );
    });
    if (info) await align();
  }
  function syncUI() {
    ["tilt", "roll", "rotation"].forEach(
      (id, i) => ($(id).value = String(state.alignment[i])),
    );
    $("pose").value = state.pose;
    $("neck").value = String(state.neck_height);
    parts();
    markerOptions();
  }
  async function open(saved: SavedJob) {
    clearTimeout(timer);
    await saving.catch(() => {});
    record = saved;
    compression.restore(
      saved.parameters.texture_encoder,
      saved.parameters.refpack_compression,
    );
    manifest = saved.manifest;
    legacy = saved.parameters?.guided_version !== 2;
    info = null;
    source = null;
    fit = null;
    lastFit = null;
    undo = [];
    redo = [];
    required(viewer, "viewer").clearModels?.();
    $("legacy").hidden = !legacy;
    $("model").value = "";
    for (const [id, key] of [
      ["body", "body"],
      ["creator", "creator"],
      ["name", "sim_name"],
      ["description", "description"],
    ])
      $(id).value = saved.parameters[key] || "";
    $("experimental").checked = saved.parameters.experimental_everyday === true;
    $("naming").open =
      !!saved.snapshotHash || saved.parameters.review?.check === true;
    message(saved.files?.[0].filename, "model-status");
    if (legacy) {
      message(
        "Start a guided-fit copy to use the new controls. The previous draft is unchanged.",
      );
      controls();
      return;
    }
    const { body, creator, sim_name, description, ...guides } = clone(
      saved.parameters,
    );
    state = { ...defaults(), ...guides };
    for (const id of [
      "environment",
      "mannequin",
      "morph",
      "motion",
      "comparison",
      "marker-group",
    ])
      $(id).value =
        saved.ui?.[id.replaceAll("-", "_")] ||
        {
          environment: "room",
          mannequin: "am",
          morph: "normal",
          motion: "none",
          comparison: "before",
          "marker-group": "torso",
        }[id];
    $("mirror").checked = saved.ui?.mirror !== false;
    $("reference").checked = saved.ui?.skeleton !== false;
    $("issues").checked = saved.ui?.issues !== false;
    await required(viewer, "viewer").references(
      await runtime.simReferences(manifest),
    );
    info = await runtime.openSim(record);
    syncUI();
    setStage(saved.ui?.stage || "align");
    await align(false);
    setView(saved.ui?.view || "orbit");
    required(viewer, "viewer").setOptions(display());
    controls();
  }
  async function copy() {
    const previous = record;
    manifest = await runtime.manifest({ refresh: true });
    state = defaults();
    record = await runtime.saveDraft({
      kind: "sim",
      files: required(previous, "previous").files,
      manifest,
      template: manifest.sims.items.find(
        (i: { id: any }) => i.id === $("body").value,
      ),
      parameters: params(),
    });
    legacy = false;
    $("legacy").hidden = true;
    await open(record);
  }
  async function fresh() {
    clearTimeout(timer);
    await saving.catch(() => {});
    sequence++;
    record = null;
    info = null;
    source = null;
    fit = null;
    legacy = false;
    state = defaults();
    undo = [];
    redo = [];
    lastFit = null;
    manifest = await runtime.manifest();
    $("form").reset();
    compression.reset();
    $("legacy").hidden = true;
    $("naming").open = false;
    required(viewer, "viewer").clearModels?.();
    await required(viewer, "viewer").references(
      await runtime.simReferences(manifest),
    );
    setStage("align");
    for (const id of ["status", "model-status", "save-status"]) message("", id);
    feedback({});
    controls();
  }
  async function init() {
    if (initialized) return;
    initialized = true;
    try {
      runtime = await import("./package-runtime/client.js");
      manifest = await runtime.manifest();
      const { SimPreview } = await import("./sim-preview.js");
      viewer = new SimPreview($("viewer"), editMarker, selectMarker);
      viewer.setVisible(!$("tool").hidden);
      await viewer.references(await runtime.simReferences(manifest));
      runtime.savedPanel($("saved"), "sim", (saved) =>
        open(saved).catch((e) => message(e.message)),
      );
      runtime.events.addEventListener("job", ({ detail: j }) => {
        if (record?.id !== j.id || j.kind !== "sim" || j.state === "draft")
          return;
        record = j;
        busy = j.state === "building";
        $("build-progress").value = Number(
          typeof j.progress === "number" ? j.progress : 0,
        );
        message(
          j.error?.message ||
            (j.state === "complete"
              ? "Test package ready. Install in the isolated profile's SavedSims folder. The stock head is retained."
              : j.message || "Creating package…"),
        );
        controls();
        if (j.state === "complete" && autoDownload === j.id) {
          autoDownload = null;
          runtime
            .download(j.id)
            .catch((e: { message: any }) => message(e.message));
        }
      });
      runtime.events.addEventListener("deleted", ({ detail: id }) => {
        if (record?.id === id) fresh().catch((e) => message(e.message));
      });
      setStage("align");
      controls();
    } catch (eCause) {
      const e = eCause instanceof Error ? eCause : new Error(String(eCause));
      initialized = false;
      viewer?.dispose();
      viewer = null;
      message(e.message);
      throw e;
    }
  }
  $("model").addEventListener("change", () =>
    choose().catch((e) => message(e.message)),
  );
  $("body").addEventListener("change", () => {
    if (!record) return;
    remember();
    state.review = { align: false, markers: false, head: false, check: false };
    align(false).catch((e) => message(e.message));
  });
  for (const id of ["creator", "name", "description"])
    $(id).addEventListener("input", schedule);
  for (const b of $("form").querySelectorAll<HTMLButtonElement>(
    "[data-sim-stage]",
  ))
    b.addEventListener("click", () => setStage(b.dataset.simStage));
  for (const b of $("form").querySelectorAll<HTMLElement>("[data-sim-view]"))
    b.addEventListener("click", () => setView(b.dataset.simView));
  for (const b of $("form").querySelectorAll<HTMLElement>("[data-sim-rotate]"))
    b.addEventListener("click", () => {
      remember();
      const i = Number(b.dataset.simRotate);
      state.alignment[i] = (state.alignment[i] + 90) % 360;
      invalidate("align");
      syncUI();
      align().catch((e) => message(e.message));
    });
  $("align-update").addEventListener("click", () => {
    remember();
    state.alignment = ["tilt", "roll", "rotation"].map((id) =>
      Number($(id).value),
    );
    invalidate("align");
    align().catch((e) => message(e.message));
  });
  $("pose").addEventListener("change", () => {
    remember();
    state.pose = $("pose").value;
    invalidate("align");
    align().catch((e) => message(e.message));
  });
  $("confirm-align").addEventListener("click", () => {
    remember();
    state.review.align = true;
    setStage("markers");
  });
  $("confirm-markers").addEventListener("click", () =>
    task("Checking joint positions…", async (current: () => any) => {
      const result = await operation("sim_landmarks");
      if (!current()) return;
      feedback(result);
      if (result.valid) {
        remember();
        state.review.markers = true;
        state.neck_height = state.markers.neck[2];
        setStage("head");
      } else message("Correct the indicated joint positions.");
    }).catch((e) => message(e.message)),
  );
  $("confirm-head").addEventListener("click", () => {
    remember();
    state.review.head = true;
    setStage("check");
  });
  $("confirm-check").addEventListener("click", () => {
    remember();
    state.review.check = true;
    $("naming").open = true;
    message(
      "Fit review saved. You can now create an experimental Everyday body package.",
    );
    schedule();
  });
  $("marker-group").addEventListener("change", () => {
    markerOptions();
    schedule();
  });
  $("marker").addEventListener("change", () => {
    selected = $("marker").value;
    markerHelp();
    redraw();
  });
  for (const id of ["marker-x", "marker-y", "marker-z"])
    $(id).addEventListener("change", () => {
      const before = clone(state.markers[selected]);
      editMarker(
        selected,
        ["marker-x", "marker-y", "marker-z"].map((recordId) =>
          Number($(recordId).value),
        ) as [number, number, number],
        true,
        before,
      );
    });
  $("neck").addEventListener("input", () =>
    editMarker("neck_boundary", Number($("neck").value), false),
  );
  $("neck").addEventListener("change", () =>
    editMarker("neck_boundary", Number($("neck").value), true),
  );
  $("part-role").addEventListener("change", () => {
    if (!selectedPart) return;
    remember();
    state.roles[selectedPart] = $("part-role").value;
    invalidate("head");
    parts();
    redraw();
  });
  for (const id of [
    "environment",
    "mannequin",
    "morph",
    "motion",
    "comparison",
    "reference",
    "issues",
    "mirror",
  ])
    $(id).addEventListener("change", () => {
      viewer?.setOptions(display());
      schedule();
    });
  for (const id of ["undo", "redo"])
    $(id).addEventListener("click", async () => {
      const a = id === "undo" ? undo : redo,
        b = id === "undo" ? redo : undo;
      if (!a.length) return;
      b.push(clone(state));
      const old = state;
      state = a.pop()!;
      syncUI();
      if (
        JSON.stringify(old.alignment) !== JSON.stringify(state.alignment) ||
        old.pose !== state.pose
      )
        await align(false);
      else redraw();
      schedule();
    });
  $("reset-stage").addEventListener("click", () => {
    remember();
    const s = stage();
    invalidate(s);
    if (s === "align") {
      state.alignment = [0, 0, 0];
      state.pose = "t";
      syncUI();
      void align();
    } else if (s === "markers") {
      state.markers = clone(required(source, "source").suggestions);
      markerOptions();
    } else if (s === "head") {
      state.roles = {};
      state.neck_height = state.markers.neck[2];
      parts();
      redraw();
    } else {
      $("motion").value = "none";
      $("morph").value = "normal";
      required(viewer, "viewer").setOptions(display());
    }
    schedule();
  });
  $("preview").addEventListener("click", () =>
    applyFit().catch((e) => message(e.message)),
  );
  $("cancel").addEventListener("click", () => {
    if (record?.state === "building") {
      autoDownload = null;
      runtime
        .cancel(record.id)
        .catch((e: { message: any }) => message(e.message));
      return;
    }
    sequence++;
    if (pendingTask) pendingTask.cancelled = true;
    if (viewer) viewer.sequence++;
    runtime.cancelSimPreview();
    message("Fitting cancelled. Your model and guides are saved.");
  });
  $("reset-view").addEventListener("click", () => viewer?.resetView());
  $("new").addEventListener("click", () =>
    fresh().catch((e) => message(e.message)),
  );
  $("copy").addEventListener("click", () =>
    copy().catch((e) => message(e.message)),
  );
  async function buildTest(retry = false) {
    if (busy) return;
    busy = true;
    controls();
    try {
      if (!retry) {
        if (!fit || lastFit !== signature() || !state.review.check)
          throw new Error("Apply the current fit and finish its review first.");
        clearTimeout(timer);
        await save();
        await saving;
      }
      autoDownload = required(record, "record").id;
      record = await runtime.start(required(record, "batch"));
    } catch (errorCause) {
      const error =
        errorCause instanceof Error
          ? errorCause
          : new Error(String(errorCause));
      autoDownload = null;
      message(error.message);
    }
    busy = record?.state === "building";
    controls();
  }
  $("experimental").addEventListener("change", schedule);
  $("form").addEventListener("submit", (e) => {
    e.preventDefault();
    void buildTest();
  });
  $("retry-build").addEventListener("click", () => buildTest(true));
  $("download").addEventListener("click", (e) => {
    e.preventDefault();
    runtime
      .download(required(record, "record").id)
      .catch((cause: { message: any }) => message(cause.message));
  });
  activateSim = init;
  window.addEventListener("pagehide", (e) => {
    clearTimeout(timer);
    save().catch(() => {});
    runtime?.cancelSimPreview();
    if (!e.persisted) {
      viewer?.dispose();
      viewer = null;
    }
  });
})();
