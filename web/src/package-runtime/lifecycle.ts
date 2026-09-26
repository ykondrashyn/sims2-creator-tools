import { required } from "../shared/dom.js";
import { archive } from "./archives.js";
import { asset, manifest } from "./assets.js";
import { download } from "./downloads.js";
import { RuntimeError } from "./errors.js";
import { loadFiles, saveFiles } from "./files.js";
import { hashBlob } from "./hashing.js";
import { exclusive, idle } from "./queue.js";
import { createSavedPanel } from "./saved-panel.js";
import { boot, call, failWorker, put, rpc, state } from "./session.js";
import * as store from "./store.js";
import type {
  FileInput,
  JobKind,
  Metadata,
  RuntimeManifest,
  SavedJob,
  StoredFile,
} from "./types.js";
import { JobEvents } from "./types.js";
import { validateSavedJob } from "./validation.js";
export { bindDownload, download, prepareDownload } from "./downloads.js";
export { manifest, store };
export const events = new JobEvents();
const changes =
  typeof BroadcastChannel === "function"
    ? new BroadcastChannel("sims2-package-jobs")
    : null;
changes?.addEventListener("message", ({ data }) =>
  events.dispatchEvent(
    new CustomEvent("refresh", { detail: data?.id ?? data?.deleted }),
  ),
);
const buildHashes = new Map<string, AbortController>();
function notify(job: SavedJob, persisted = false) {
  if (persisted) {
    changes?.postMessage({ id: job.id });
    events.dispatchEvent(new CustomEvent("persisted", { detail: job }));
  }
  events.dispatchEvent(new CustomEvent("job", { detail: job }));
}

function runtimeRefs(
  m: RuntimeManifest,
  kind: string,
  template: Metadata = {},
) {
  const keys = ["worker", "glue", "wasm"];
  if (kind === "sim") {
    const item = m.sims?.items?.find((i: Metadata) => i.id === template?.id);
    if (!item || item.asset !== template.asset)
      throw new Error("Saved Sim rig is unavailable. Your model is retained.");
    keys.push(item.asset);
    if (m.sims.everyday_test?.bodies.includes(item.id))
      keys.push(m.sims.everyday_test.asset);
    if (m.sims.guided_version === 2)
      keys.push(
        "object-reference",
        "object-reference-am",
        "object-reference-af",
      );
    return keys.map((k) => `asset:${m.assets[k].sha256}`);
  }
  if (kind === "painting") {
    if (
      !m.paintings?.items?.some((i: Metadata) => i.id === template?.id) ||
      !template?.asset ||
      !template?.recipe_asset
    )
      throw new Error(
        "This saved painting template is unavailable. Your image has been retained.",
      );
    keys.push(
      "painting-catalog",
      "object-game",
      template.asset,
      template.recipe_asset,
      "object-reference",
      "object-reference-am",
      "object-reference-af",
    );
    return keys.map((k) => `asset:${m.assets[k].sha256}`);
  }
  if (kind === "conversion") {
    if (!m.conversion?.profiles?.[template?.id] || !template.asset)
      throw new Error(
        "The saved conversion profile is unavailable. Your input has been kept.",
      );
    keys.push(template.asset);
    return keys.map((k) => `asset:${m.assets[k].sha256}`);
  }
  keys.push("palette");
  if (kind === "hair") {
    if (!m.assets.archive)
      throw new Error(
        "This draft uses an unsupported archive format. Its saved data has been kept. Start a new batch.",
      );
    keys.push("archive");
    if (template?.asset) keys.push(template.asset);
    else keys.push("game-meshes");
  } else if (kind === "object") {
    keys.push("object-catalog", "object-game");
    if (m.objects?.sizing_version === 2)
      keys.push(
        "object-reference",
        "object-reference-am",
        "object-reference-af",
        "object-reference-table",
      );
    if (m.assets["object-optimizer"]) keys.push("object-optimizer");
    if (template?.asset) keys.push(template.asset);
  } else keys.push("tattoo-overlay", "tattoo-controller", "tattoo-face");
  return keys.map((k) => `asset:${m.assets[k].sha256}`);
}
export async function saveDraft(record: Metadata): Promise<SavedJob> {
  if (
    !["tattoo", "hair", "object", "conversion", "painting", "sim"].includes(
      record.kind,
    )
  )
    throw new Error("Unsupported saved batch type");
  if (
    record.kind === "sim" &&
    (record.files?.length !== 1 ||
      record.files.some((f: StoredFile) => f.size > 64 * 1024 ** 2))
  )
    throw new Error("Choose one GLB or glTF ZIP no larger than 64 MiB.");
  if (
    record.kind === "painting" &&
    (record.files?.length > 1 ||
      record.files?.some((f: StoredFile) => f.size > 32 * 1024 ** 2))
  )
    throw new Error("Choose one painting image no larger than 32 MiB.");
  if (
    record.kind === "conversion" &&
    (record.files?.length !== 1 || record.files[0].size > 32 * 1024 ** 2)
  )
    throw new Error("Choose one PNG no larger than 32 MiB.");
  if (record.kind === "object") {
    const files = new Map(
      [
        ...(record.files || []),
        ...(record.modelSource ? [record.modelSource] : []),
      ].map((f) => [f.sha256, f]),
    );
    if ([...files.values()].reduce((n, f) => n + f.size, 0) > 128 * 1024 ** 2)
      throw new Error(
        "Object inputs and the retained original model exceed 128 MiB. Use a smaller model or fewer dependencies.",
      );
  }
  if (
    record.kind === "tattoo" &&
    (record.files?.length > 40 ||
      record.files?.some((f: { size: number }) => f.size > 8 * 1024 ** 2))
  )
    throw new Error("Choose up to 40 PNGs, 8 MiB each and 128 MiB total.");
  record = {
    schema_version: 1,
    id: store.id(),
    revision: 0,
    state: "draft",
    created: Date.now(),
    checkpoints: [],
    ...record,
  };
  if (!record.manifest) record.manifest = await manifest();
  record.runtimeBlobs = runtimeRefs(
    record.manifest,
    record.kind,
    record.template,
  );
  const pinnedAssets = new Map<string, Blob>();
  for (const [name, a] of Object.entries(
    (record.manifest as RuntimeManifest).assets,
  )) {
    if (record.runtimeBlobs.includes(`asset:${a.sha256}`))
      pinnedAssets.set(`asset:${a.sha256}`, await asset(record.manifest, name));
  }
  record.label =
    record.kind === "hair"
      ? `${record.parameters?.creator || "Creator"}_${record.parameters?.hair_name || "Hair"}`
      : record.kind === "object"
        ? `${record.parameters?.creator || "Creator"}_${record.parameters?.object_name || "Object"}`
        : record.kind === "sim"
          ? record.parameters?.sim_name || "Untitled Sim"
          : record.kind === "painting"
            ? record.parameters?.title || "Untitled painting"
            : record.kind === "conversion"
              ? record.parameters.filename
              : record.ui?.catalogName || "Tattoo batch";
  await store.saveJob(record as SavedJob, null, pinnedAssets);
  notify(record as SavedJob, true);
  return record as SavedJob;
}

// Optimization has its own disposable worker and shares the build lease so
// another tab cannot start a memory-intensive build at the same time.
export async function optimizeObjectModel(
  record: SavedJob,
  source: StoredFile,
  options: { triangles: number; error: number },
  {
    signal,
    onProgress = () => {},
  }: { signal?: AbortSignal; onProgress?: (value: string) => void } = {},
) {
  idle();
  if (signal?.aborted)
    throw new Error("Optimization cancelled. The original model is kept.");
  const token = await store.acquire(record.id);
  let w: Worker | undefined,
    url: string | undefined,
    timeout: number | undefined,
    abort: (() => void) | undefined,
    rejectWorker: (reason?: any) => void;
  const heartbeat = setInterval(
    () => store.heartbeat(token).catch((e) => rejectWorker?.(e)),
    30000,
  );
  try {
    const blob = await store.getBlob(source.blob);
    if (
      !blob ||
      blob.size !== source.size ||
      (await hashBlob(blob)) !== source.sha256
    )
      throw new Error(
        "The saved source model is missing or damaged. Select the original file again.",
      );
    const code = await asset(record.manifest, "object-optimizer");
    if (signal?.aborted)
      throw new Error("Optimization cancelled. The original model is kept.");
    url = URL.createObjectURL(new Blob([code], { type: "text/javascript" }));
    w = new Worker(url, { type: "module" });
    const id = ++state.serial,
      bytes = await blob.arrayBuffer();
    return await new Promise<Metadata>((resolve, reject) => {
      rejectWorker = reject;
      abort = () =>
        reject(
          new Error("Optimization cancelled. The original model is kept."),
        );
      signal?.addEventListener("abort", abort, { once: true });
      if (signal?.aborted) return abort();
      timeout = setTimeout(
        () =>
          reject(
            new Error(
              "Optimization exceeded ten minutes. Try a smaller source model. Saved inputs are kept.",
            ),
          ),
        600000,
      );
      w!.onerror = () =>
        reject(
          new Error(
            "The optimization worker stopped. Try a smaller source model. Saved inputs are kept.",
          ),
        );
      w!.onmessage = ({ data }) => {
        if (
          data.version !== 1 ||
          data.id !== id ||
          data.job !== record.id ||
          data.revision !== record.revision
        )
          return;
        if (data.progress) onProgress(data.progress);
        else if (data.error) reject(new Error(data.error.message));
        else resolve(data.result);
      };
      w!.postMessage(
        {
          version: 1,
          id,
          job: record.id,
          revision: record.revision,
          bytes,
          format: source.name.endsWith(".zip") ? "zip" : "glb",
          options,
        },
        [bytes],
      );
    });
  } finally {
    w?.terminate();
    if (url) URL.revokeObjectURL(url);
    if (abort) signal?.removeEventListener("abort", abort);
    clearInterval(heartbeat);
    clearTimeout(timeout);
    await store.release(token);
  }
}
async function inspectPackagesInternal(
  files: (File | FileInput)[],
): Promise<Metadata & { files: StoredFile[] }> {
  idle();
  const m = await manifest();
  await boot(m);
  state.contextKey = null;
  await call("reset_inputs", {});
  await put("game-meshes", await asset(m, "game-meshes"));
  await call("init", {});
  const descriptors = await saveFiles(files);
  for (const f of descriptors)
    await put(f.name, required(await store.getBlob(f.blob), "saved package"));
  const result = await call("inspect_packages", {
    files: descriptors.map((f: StoredFile) => f.name),
  });
  return { ...result, files: descriptors };
}
export async function open(record: Metadata): Promise<Metadata> {
  const m = record.manifest || (await manifest());
  if (
    record.kind === "object" &&
    record.parameters?.fit_to_template &&
    m.objects?.fit_to_template !== 1
  )
    throw new Error(
      "This saved engine does not support fitting the occupied floor area. Use Fit within template on an editable draft to update its engine, or start a new batch.",
    );
  if (
    record.kind === "object" &&
    record.parameters?.height_from_fit &&
    m.objects?.fixed_fitted_height !== 1
  )
    throw new Error(
      "This saved engine does not support fixed fitted heights. Reload the website and use Fit within template on an editable draft, or start a new batch.",
    );
  await boot(m, record.kind);
  const key = JSON.stringify([
    m.release,
    record.kind,
    record.template?.id,
    record.files?.map((f: { name: any; sha256: any }) => [f.name, f.sha256]),
  ]);
  if (state.contextKey === key && state.contextInfo) return state.contextInfo;
  await call("reset_inputs", {});
  await loadFiles(record as SavedJob);
  if (record.kind === "sim") {
    const item = m.sims?.items?.find(
      (i: Metadata) => i.id === record.template?.id,
    );
    if (
      !item ||
      item.asset !== record.template.asset ||
      item.id !== record.parameters.body
    )
      throw new Error(
        "Saved Sim rig does not match this batch. Your model is retained.",
      );
    await put("sim-reference", await asset(m, item.asset), record);
    if (m.sims.everyday_test?.bodies.includes(item.id))
      await put(
        "sim-everyday-template",
        await asset(m, m.sims.everyday_test.asset),
        record,
      );
    const file = record.files?.[0];
    if (!file) throw new Error("Saved Sim model is missing.");
    state.contextInfo = await call(
      "sim_inspect_model",
      { model: file.name },
      record,
    );
    state.contextKey = key;
    return state.contextInfo;
  }
  if (record.kind === "painting") {
    const item = m.paintings?.items?.find(
      (i: Metadata) => i.id === record.template?.id,
    );
    if (
      !item ||
      item.asset !== record.template.asset ||
      item.recipe_asset !== record.template.recipe_asset
    )
      throw new Error(
        "Saved painting assets do not match their release. Your image is retained.",
      );
    for (const [name, assetKey] of [
      ["object-catalog", "painting-catalog"],
      ["object-game", "object-game"],
      ["painting-template", item.asset],
      ["painting-recipe", item.recipe_asset],
    ])
      await put(name, await asset(m, assetKey), record);
    state.contextInfo = await call("painting_open", {}, record);
    state.contextKey = key;
    return state.contextInfo;
  }
  if (record.kind === "conversion") {
    if (
      record.files?.length !== 1 ||
      !["am", "af"].includes(record.parameters?.body) ||
      record.template?.id !== record.parameters.body
    )
      throw new Error(
        "The saved conversion snapshot is damaged. Start a new conversion from the original PNG.",
      );
    await put("conversion-map", await asset(m, record.template.asset), record);
    state.contextInfo = await call(
      "conversion_validate",
      { input: "conversion-input" },
      record,
    );
    state.contextKey = key;
    return state.contextInfo;
  }
  if (record.kind === "hair") {
    const item = record.template;
    if (item.asset)
      await put("selected-template", await asset(m, item.asset), record);
    else {
      await put("game-meshes", await asset(m, "game-meshes"), record);
      await call("init", {});
      const inspection = await call(
        "inspect_packages",
        { files: record.files.map((f: { name: any }) => f.name) },
        record,
      );
      if (!inspection.items.some((i: { id: any }) => i.id === item.id))
        throw new Error("Saved mesh and recolor dependencies do not match.");
    }
    state.contextInfo = await call(
      "open_hair",
      { asset: item.asset ? "selected-template" : item.template_file },
      record,
    );
    state.contextKey = key;
    return state.contextInfo;
  }
  if (record.kind === "object") {
    for (const name of ["object-catalog", "object-game"])
      await put(name, await asset(m, name), record);
    if (record.template?.asset)
      await put(
        "selected-object",
        await asset(m, record.template.asset),
        record,
      );
    state.contextInfo = await call(
      "object_inspect",
      {
        files: record.template?.asset
          ? ["selected-object"]
          : record.files
              .filter((f: StoredFile) =>
                f.name.toLowerCase().endsWith(".package"),
              )
              .map((f: StoredFile) => f.name),
        trusted: !!record.template?.asset,
      },
      record,
    );
    state.contextKey = key;
    return state.contextInfo;
  }
  for (const k of ["tattoo-overlay", "tattoo-controller", "tattoo-face"])
    await put(k, await asset(m, k), record);
  state.contextKey = key;
  state.contextInfo = {};
  return state.contextInfo;
}
async function openHairInternal(
  template: Metadata,
  files: StoredFile[] = [],
  m?: RuntimeManifest,
) {
  idle();
  return open({
    kind: "hair",
    template,
    files,
    manifest: m || (await manifest()),
  });
}
async function parseCurveInternal(file: File) {
  idle();
  await boot(await manifest());
  await put("curve-input", file);
  const result = await call("parse_curve", {
    asset: "curve-input",
    filename: file.name,
  });
  await rpc("drop", { name: "curve-input" });
  return result;
}
async function previewInternal(record: SavedJob, slot: string) {
  idle();
  await open(record);
  const result = await call(
    "preview",
    { job: { ...record.parameters, id: record.id }, slot },
    record,
  );
  if (!record.snapshotHash) {
    record = {
      ...record,
      revision: record.revision + 1,
      parameters: result.job,
      state: "draft",
    };
    await saveDraft(record);
  }
  return { record, preview: result };
}
async function startInternal(record: SavedJob) {
  if (
    record.kind === "sim" &&
    (record.manifest.sims.everyday_test?.version !== 1 ||
      record.parameters?.experimental_everyday !== true ||
      record.parameters.body !== "am")
  )
    throw new Error(
      "Only the acknowledged Adult Male Everyday body test is available. Start a new batch with the current engine. The complete replacement Sim is not available yet.",
    );
  idle();
  if (record.state === "complete")
    throw new Error("Completed batches are read-only. Start a new batch.");
  const token = await store.acquire(record.id);
  state.active = { id: record.id, token };
  buildHashes.set(token, new AbortController());
  state.metrics = { peak_heap_bytes: 0, worker_ms: 0 };
  let keepAlive;
  try {
    record = { ...record, state: "building", error: undefined };
    await store.saveJob(record, token);
    notify(record, true);
    keepAlive = setInterval(
      () => store.heartbeat(token).catch((e) => cancel(record.id, e.message)),
      10000,
    );
  } catch (errorCause) {
    const error =
      errorCause instanceof Error ? errorCause : new Error(String(errorCause));
    state.active = null;
    buildHashes.delete(token);
    await store.release(token);
    throw error;
  }
  run(record, token)
    .catch(async (error) => {
      if (state.active?.token !== token) return;
      const latest = await store.getJob(record.id);
      if (latest) {
        latest.state = "failed";
        latest.error = RuntimeError.from(error).toJSON();
        await store.saveJob(latest, token).catch(() => {});
        notify(latest, true);
      }
    })
    .finally(async () => {
      clearInterval(keepAlive);
      buildHashes.delete(token);
      if (state.active?.token === token) {
        await store.release(token);
        state.active = null;
        failWorker("Package build finished.");
      }
    });
  return record;
}
async function run(record: SavedJob, token: string) {
  const signal = required(
    buildHashes.get(token),
    "build hashing context",
  ).signal;
  async function checkedHash(blob: Blob) {
    if (state.active?.token !== token || signal.aborted)
      throw new DOMException("Build hashing cancelled.", "AbortError");
    const digest = await hashBlob(blob, { signal });
    if (state.active?.token !== token || signal.aborted)
      throw new DOMException("Build hashing cancelled.", "AbortError");
    return digest;
  }
  let elapsed = 0,
    last = performance.now();
  const timeout = setInterval(() => {
    const now = performance.now(),
      delta = now - last;
    last = now;
    if (delta < 5000) elapsed += delta;
    if (elapsed > 600000)
      void cancel(
        record.id,
        "Build exceeded ten minutes of active processing. Your batch is saved. Resume it, or start a smaller batch.",
      );
  }, 1000);
  try {
    await open(record);
    if (record.kind === "hair")
      record.parameters = await call(
        "prepare_hair_job",
        { ...record.parameters, id: record.id },
        record,
      );
    // Reuse the frozen Sim parameters exactly, including landmark decimals.
    // The build validates them again without rewriting the saved snapshot.
    else if (record.kind === "sim" && !record.snapshotHash)
      record.parameters = await call(
        "sim_prepare",
        {
          job: {
            ...record.parameters,
            id: record.id,
            pose_confirmed: true,
            partition_confirmed: true,
          },
        },
        record,
      );
    else if (record.kind === "painting")
      record.parameters = await call(
        "painting_prepare",
        { job: { ...record.parameters, id: record.id } },
        record,
      );
    else if (record.kind === "object")
      record.parameters = await call(
        "object_prepare",
        { job: { ...record.parameters, id: record.id } },
        record,
      );
    else if (record.kind === "tattoo")
      record.parameters = await call(
        "prepare_tattoo_job",
        {
          id: record.id,
          spec: record.spec,
          ...([1, 2].includes(record.manifest.texture_encoders?.version)
            ? {
                texture_encoder:
                  record.parameters?.texture_encoder || "directxtex",
              }
            : {}),
          ...(record.manifest.package_compression?.version === 1
            ? {
                refpack_compression:
                  record.parameters?.refpack_compression !== false,
              }
            : {}),
        },
        record,
      );
    const snapshotHash = store.sha256(
      new TextEncoder().encode(
        JSON.stringify({
          manifest: record.manifest.release,
          parameters: record.parameters,
          files: record.files,
        }),
      ),
    );
    if (record.snapshotHash && record.snapshotHash !== snapshotHash)
      throw new Error(
        "Saved build snapshot changed. Start a new batch to edit it.",
      );
    record.snapshotHash = snapshotHash;
    record.snapshot =
      record.snapshot ||
      structuredClone({
        parameters: record.parameters,
        spec: record.spec,
        files: record.files,
        release: record.manifest.release,
      });
    await store.saveJob(record, token);
    if (record.kind === "conversion") {
      await call(
        "conversion_begin",
        {
          input: "conversion-input",
          mapping: "conversion-map",
          body: record.parameters.body,
        },
        record,
      );
      let step;
      do {
        step = await call("conversion_step", { pixels: 4096 }, record);
        if (state.active?.token !== token)
          throw new Error("Conversion cancelled. Your input is saved.");
        notify({
          ...record,
          progress: Math.round(step.progress * 95),
          message: "Converting texture in your browser…",
        });
      } while (!step.done);
      const report = await call("conversion_finish", {}, record);
      const { bytes } = await rpc("take", { name: "output" }, record);
      if (!report.validated || bytes.byteLength > 8 * 1024 ** 2)
        throw new Error("Converted PNG failed validation or exceeds 8 MiB.");
      const key = `job:${record.id}:png:${token}`;
      await store.putBlob(key, new Blob([bytes], { type: "image/png" }), token);
      record.output = {
        parts: [key],
        size: bytes.byteLength,
        type: "image/png",
        filename: record.parameters.filename,
      };
      record.validation = { status: "passed", conversion: report };
    } else if (record.kind === "sim") {
      notify({
        ...record,
        progress: 25,
        message: "Building the experimental Everyday body…",
      });
      const report = await call(
        "sim_experimental_build",
        { job: record.parameters },
        record,
      );
      const { bytes } = await rpc("take", { name: "output" }, record);
      if (!report.validated || bytes.byteLength > 64 * 1024 ** 2)
        throw new Error(
          "Experimental body package failed validation or exceeds 64 MiB.",
        );
      const key = `job:${record.id}:package:${token}`;
      await store.putBlob(key, new Blob([bytes]), token);
      record.output = {
        parts: [key],
        size: bytes.byteLength,
        type: "application/octet-stream",
        filename: report.filename,
      };
      record.validation = { status: "passed", sim: report };
    } else if (record.kind === "tattoo") {
      notify({
        ...record,
        progress: 20,
        message: "Creating and checking tattoo resources…",
      });
      const report = await call(
        "build_tattoo",
        { job: record.parameters },
        record,
      );
      const { bytes } = await rpc("take", { name: "output" }, record);
      if (bytes.byteLength > 64 * 1024 ** 2)
        throw new Error("Tattoo package exceeds 64 MiB. Use fewer tattoos.");
      const key = `job:${record.id}:package:${token}`;
      await store.putBlob(key, new Blob([bytes]), token);
      record.output = {
        parts: [key],
        size: bytes.byteLength,
        type: "application/octet-stream",
        filename: `${record.parameters.slug}.package`,
      };
      record.validation = { rust: report };
    } else if (record.kind === "painting") {
      notify({
        ...record,
        progress: 35,
        message: "Creating and checking your painting…",
      });
      const report = await call(
        "painting_build",
        { job: record.parameters },
        record,
      );
      const { bytes } = await rpc("take", { name: "output" }, record);
      if (report.status !== "passed" || bytes.byteLength > 64 * 1024 ** 2)
        throw new Error("Painting output failed validation or exceeds 64 MiB.");
      const key = `job:${record.id}:package:${token}`;
      await store.putBlob(key, new Blob([bytes]), token);
      record.output = {
        parts: [key],
        size: bytes.byteLength,
        type: "application/octet-stream",
        filename: `${record.parameters.creator}_${record.parameters.object_name}.package`,
      };
      record.validation = { status: "passed", painting: report };
    } else if (record.kind === "object") {
      notify({
        ...record,
        progress: {
          completed: 0,
          total: 1,
          color: "Creating and checking object",
        },
      });
      const report = await call(
        "object_build",
        { job: record.parameters },
        record,
      );
      const { bytes } = await rpc("take", { name: "output" }, record);
      if (bytes.byteLength > 128 * 1024 ** 2)
        throw new Error(
          "Object output exceeds 128 MiB. Reduce model textures or dependencies.",
        );
      const key = `job:${record.id}:package:${token}`;
      await store.putBlob(key, new Blob([bytes]), token);
      const filename = `${record.parameters.creator}_${record.parameters.object_name}`;
      record.output = {
        parts: [key],
        size: bytes.byteLength,
        type: "application/octet-stream",
        filename: `${filename}.package`,
      };
      record.validation = { status: "passed", object: report };
    } else {
      const colors = record.parameters.colors;
      const palette = [
        ...record.manifest.hair.palette,
        ...record.parameters.custom_colors,
      ];
      const byId = (id: any) => palette.find((c) => (c.id || c.name) === id);
      const keys = new Set(),
        entries = [],
        reports = [];
      const checkpoints: Metadata[] = [];
      for (let index = 0; index < colors.length; index++) {
        const color = colors[index],
          c = byId(color);
        const filename = `${record.parameters.creator}_${record.parameters.hair_name}_${c.name.replaceAll(" ", "")}.package`;
        record.progress = {
          completed: index,
          total: colors.length,
          color: c.name,
        };
        notify(record);
        let cp = record.checkpoints?.find((p) => p.color === color);
        let blob = cp && (await store.getBlob(cp.blob));
        if (blob && cp && (await checkedHash(blob)) !== cp.report.sha256)
          throw new Error(
            "Saved package checkpoint is damaged. Start a new batch from its source files.",
          );
        if (!blob) {
          const report = await call(
            "build_hair",
            { job: record.parameters, color },
            record,
          );
          const { bytes } = await rpc("take", { name: "output" }, record);
          cp = {
            color,
            filename,
            blob: `job:${record.id}:color:${record.snapshotHash}:${index}`,
            report,
          };
          blob = new Blob([bytes]);
          await store.putBlob(cp.blob, blob, token);
        }
        if (!cp)
          throw new Error(
            "The validated package checkpoint is missing. Saved inputs are kept.",
          );
        for (const key of cp.report.resource_keys) {
          if (keys.has(key))
            throw new Error("Generated package resource identities collide.");
          keys.add(key);
        }
        checkpoints.push(cp);
        record.checkpoints = [
          ...checkpoints,
          ...(record.checkpoints || []).filter(
            (p) =>
              !checkpoints.some((checkpoint) => checkpoint.color === p.color),
          ),
        ];
        await store.saveJob(record, token);
        notify(record, true);
        entries.push({ name: filename, key: cp.blob });
        reports.push({ color: c.name, ...cp.report });
      }
      const meshKeys = new Set();
      for (const mesh of required(record.template, "record.template").meshes ||
        []) {
        const file = record.files.find((f) => f.name === mesh.filename),
          blob = file && (await store.getBlob(file.blob));
        if (!blob || (await checkedHash(blob)) !== mesh.sha256)
          throw new Error("A saved mesh dependency is missing or changed.");
        const inv = await call("inventory", { asset: file.name }, record);
        for (const k of inv.resource_keys) {
          if (keys.has(k) || meshKeys.has(k))
            throw new Error("Mesh resource identities collide.");
          meshKeys.add(k);
        }
        entries.push({ name: `Meshes/${mesh.filename}`, key: file.blob });
      }
      const { text } = await call("readme", { job: record.parameters }, record);
      entries.push({
        name: "README.txt",
        blob: new Blob([text], { type: "text/plain" }),
      });
      record.progress = {
        completed: colors.length,
        total: colors.length,
        color: "Checking ZIP",
      };
      notify(record);
      record.output = {
        ...(await archive(
          record,
          entries,
          token,
          () => state.active?.token === token,
        )),
        filename: `${record.parameters.creator}_${record.parameters.hair_name}_Recolors.zip`,
      };
      record.validation = { status: "passed", packages: reports };
    }
    record.runtimeMetrics = { ...state.metrics };
    required(record.output, "record.output").hashes = [];
    for (const key of required(record.output, "record.output").parts) {
      required(record.output, "record.output").hashes.push(
        await checkedHash(
          required(await store.getBlob(key), "generated package"),
        ),
      );
    }
    record.state = "complete";
    record.completed = Date.now();
    record.progress = ["tattoo", "conversion", "painting", "sim"].includes(
      record.kind,
    )
      ? 100
      : record.kind === "object"
        ? { completed: 1, total: 1 }
        : {
            completed: record.parameters.colors.length,
            total: record.parameters.colors.length,
          };
    await store.saveJob(record, token);
    notify(record, true);
  } finally {
    clearInterval(timeout);
  }
}
export async function cancel(
  id: string,
  message = "Build cancelled. Saved inputs and completed checkpoints are kept.",
) {
  if (state.active?.id !== id)
    throw new Error(
      "This build is running in another browser tab. Cancel it there.",
    );
  const { token } = state.active;
  buildHashes
    .get(token)
    ?.abort(new DOMException("Build hashing cancelled.", "AbortError"));
  state.active = null;
  failWorker(message);
  const job = await store.getJob(id);
  if (job) {
    job.state = "cancelled";
    job.error = { message };
    await store.saveJob(job, token).catch(() => {});
    notify(job, true);
  }
  await store.release(token);
  if (!job) throw new Error("The cancelled batch was deleted in another tab.");
  return job;
}
async function restoreInternal(id: string) {
  idle();
  const job = await store.getJob(id);
  validateSavedJob(job);
  if (job.state === "building") {
    const current = await store.lease();
    if (current?.job === id && current.until > Date.now())
      throw new Error("This batch is still running in another browser tab.");
    job.state = "interrupted";
    await store.saveJob(job);
    notify(job, true);
  }
  await open(job);
  return job;
}

export function savedPanel(
  container: HTMLElement,
  kind: JobKind,
  onOpen: (job: SavedJob) => unknown,
) {
  return createSavedPanel(
    {
      restore,
      download,
      events,
      onDeleted: (id) => {
        changes?.postMessage({ deleted: id });
        events.dispatchEvent(new CustomEvent("deleted", { detail: id }));
      },
    },
    container,
    kind,
    onOpen,
  );
}
window.addEventListener("beforeunload", (event) => {
  if (state.active) {
    event.preventDefault();
    event.returnValue = "";
  }
});

export const inspectPackages = (files: (File | FileInput)[]) =>
  exclusive(() => inspectPackagesInternal(files));

export const openHair = (
  template: Metadata,
  files: StoredFile[] = [],
  m?: RuntimeManifest,
) => exclusive(() => openHairInternal(template, files, m));

export const parseCurve = (file: File) =>
  exclusive(() => parseCurveInternal(file));

export const preview = (record: SavedJob, slot: string) =>
  exclusive(() => previewInternal(record, slot));

export const start = (record: SavedJob) =>
  exclusive(() => startInternal(record));

export const restore = (id: string) => exclusive(() => restoreInternal(id));

window.addEventListener("pagehide", () => {
  if (state.active) {
    const token = state.active.token;
    buildHashes
      .get(token)
      ?.abort(new DOMException("Browser closed.", "AbortError"));
    state.active = null;
    failWorker("Browser closed. Resume your saved batch.");
    // Unload can discard asynchronous IndexedDB work. Record only the stopped
    // attempt token synchronously so the next page can finish fencing it.
    try {
      sessionStorage.setItem("sims2-interrupted-attempt", token);
    } catch {}
    store.interrupt(token).catch(() => {});
  }
});

try {
  const token = sessionStorage.getItem("sims2-interrupted-attempt");
  if (token) {
    await store.interrupt(token);
    sessionStorage.removeItem("sims2-interrupted-attempt");
  }
} catch {}

store.startCleanupScheduler();
