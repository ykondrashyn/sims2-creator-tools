import { processImage, type ImageInfo } from "./upscale-image.js";
import { gpuAdapter, type LocalBackend } from "./local-upscale/backend.js";
import { outputExtension, outputFilename } from "./upscale-download.js";

interface LocalModel {
  readonly id: string;
  readonly name: string;
  readonly backend: LocalBackend;
  readonly description: string;
}
interface RunSnapshot {
  readonly revision: number;
  readonly model: Readonly<LocalModel>;
  readonly source: File;
  readonly prepared: Blob;
  readonly preserve: boolean;
}
interface UpscaleResult {
  readonly snapshot: RunSnapshot;
  readonly localVersion?: string;
  readonly localMetrics?: Record<string, number>;
  readonly raw: Blob;
  readonly png: Blob | null;
  readonly info: ImageInfo;
}
const MODELS: LocalModel[] = [
  {
    id: "local-real-esrgan",
    name: "Real-ESRGAN CPU",
    backend: "wasm",
    description:
      "Runs the full FP32 model on your CPU with 4× PNG output. Downloads the model on first use. Large images can take several minutes.",
  },
  {
    id: "local-real-esrgan-webgpu",
    name: "Real-ESRGAN WebGPU",
    backend: "webgpu",
    description:
      "Runs the same full FP32 model on your GPU with 4× PNG output. Requires a supported GPU and browser. No automatic CPU fallback.",
  },
];

export function attachUpscale() {
  const node = <T extends HTMLElement>(name: string) =>
    document.getElementById("upscale-" + name) as T;
  const form = node<HTMLFormElement>("form");
  const file = node<HTMLInputElement>("file");
  const model = node<HTMLSelectElement>("model");
  const preserve = node<HTMLInputElement>("alpha");
  const button = node<HTMLButtonElement>("start");
  const cancel = node<HTMLButtonElement>("cancel");
  const retry = node<HTMLButtonElement>("retry");
  const fresh = node<HTMLButtonElement>("new");
  const download = node<HTMLAnchorElement>("download");
  const rawDownload = node<HTMLAnchorElement>("raw-download");
  const status = node<HTMLElement>("status");
  const pictureBefore = node<HTMLImageElement>("before");
  const pictureAfter = node<HTMLImageElement>("after");
  let source: File | null = null;
  let prepared: Blob | null = null;
  let originalInfo: ImageInfo | null = null;
  let displayed: UpscaleResult | null = null;
  let revision = 0;
  let active: AbortController | null = null;
  let localRetry: ((signal: AbortSignal) => Promise<void>) | null = null;
  let gpuReason = "Checking WebGPU availability…";
  let gpuCheck: Promise<void> | undefined;
  const urls = new Map<string, string>();
  const selected = () => MODELS.find((item) => item.id === model.value)!;
  function current(signal: AbortSignal, expectedRevision = revision) {
    signal.throwIfAborted();
    if (expectedRevision !== revision)
      throw new DOMException("Cancelled", "AbortError");
  }
  function text(message: string, failed = false) {
    status.textContent = message;
    status.classList.toggle("error", failed);
    status.hidden = !message;
  }
  function url(key: string, blob: Blob) {
    const previous = urls.get(key);
    const next = URL.createObjectURL(blob);
    urls.set(key, next);
    if (previous) URL.revokeObjectURL(previous);
    return next;
  }
  function validateSize() {
    let message = "";
    if (originalInfo) {
      const { width, height } = originalInfo;
      if (width * height > 4_000_000 || width * 4 > 8192 || height * 4 > 8192)
        message =
          "Choose an image of at most 4 megapixels and 2048 pixels per side. The model produces a 4× result without reducing the source size.";
    }
    node("size-warning").textContent = message;
    node("size-warning").hidden = !message;
    return !message;
  }
  function busy(value: boolean) {
    for (const control of form.querySelectorAll<
      HTMLInputElement | HTMLSelectElement
    >("input, select"))
      control.disabled = value;
    button.disabled =
      value ||
      !prepared ||
      !validateSize() ||
      (selected()?.backend === "webgpu" && !!gpuReason);
    button.textContent = displayed ? "Upscale again" : "Upscale image";
    fresh.disabled = value;
    cancel.hidden = !value;
    cancel.disabled = false;
    node("activity").hidden = !value;
    retry.hidden = value || !localRetry;
    retry.textContent = "Retry";
  }
  function changeModel() {
    node("model-note").textContent = selected().description;
    const availability = node("availability");
    const update = () => {
      availability.hidden = selected().backend !== "webgpu" || !gpuReason;
      availability.textContent = gpuReason;
      busy(!!active);
    };
    if (selected().backend === "webgpu" && !gpuCheck)
      gpuCheck = gpuAdapter()
        .then(() => {
          gpuReason = "";
        })
        .catch((error: Error) => {
          gpuReason = error.message;
        })
        .then(update);
    update();
  }
  function showResult(result: UpscaleResult) {
    const { raw, png, info, snapshot } = result;
    // Pixel validation has finished before publishing replacement links.
    const extension = outputExtension(info.format);
    const preferred = png || raw;
    displayed = result;
    download.href = url("download", preferred);
    download.download = outputFilename(
      snapshot.source.name,
      snapshot.model.id,
      png ? "png" : extension,
    );
    download.textContent = "Download PNG";
    download.hidden = false;
    rawDownload.hidden = !png;
    if (png) {
      rawDownload.href = url("raw", raw);
      rawDownload.download = outputFilename(
        snapshot.source.name,
        snapshot.model.id,
        extension,
      );
    } else {
      const previous = urls.get("raw");
      if (previous) URL.revokeObjectURL(previous);
      urls.delete("raw");
      rawDownload.removeAttribute("href");
    }
    node("preview-error").hidden = true;
    pictureAfter.src = url("after", preferred);
    node("after-model").textContent = `Upscaled with ${snapshot.model.name}`;
    node("after-meta").textContent =
      `${info.width} × ${info.height} · PNG${info.alpha_restored ? " · original transparency restored" : ""}`;
    node("result-version").textContent =
      `Local model SHA-256: ${result.localVersion || "unavailable"}`;
    node("result-settings").textContent =
      `4× · Backend: ${snapshot.model.backend === "webgpu" ? "WebGPU" : "CPU WASM"}, FP32` +
      (result.localMetrics
        ? ` · Initialization: ${(result.localMetrics.initialization_ms / 1000).toFixed(2)} s · First tile: ${(result.localMetrics.first_tile_ms / 1000).toFixed(2)} s · Remaining tiles: ${((result.localMetrics.warm_tiles_ms || 0) / 1000).toFixed(2)} s`
        : "");
    node("result-details").hidden = false;
    node("result").hidden = false;
    node("after-placeholder").hidden = true;
    pictureAfter.hidden = false;
    updateZoom();
  }
  async function compose(
    snapshot: RunSnapshot,
    raw: Blob,
    keepAlpha: boolean,
    signal: AbortSignal,
  ): Promise<UpscaleResult> {
    text(
      keepAlpha
        ? "Restoring original transparency in your browser…"
        : "Checking the result in your browser…",
    );
    const result = await processImage(
      snapshot.source,
      { output: raw, preserve: keepAlpha, png: false },
      signal,
    );
    current(signal, snapshot.revision);
    outputExtension(result.info.format);
    return { snapshot, raw, png: result.png, info: result.info };
  }
  function ready(result: UpscaleResult) {
    showResult(result);
    text(
      result.info.alpha_error ||
        "Ready to download. Choose another upscaler to run the original image again.",
      !!result.info.alpha_error,
    );
  }
  async function run(fn: (signal: AbortSignal) => Promise<void>) {
    if (active) return;
    const controller = new AbortController();
    const currentRevision = revision;
    active = controller;
    node<HTMLProgressElement>("activity").removeAttribute("value");
    busy(true);
    try {
      await fn(controller.signal);
    } catch (error) {
      if (revision !== currentRevision || active !== controller) return;
      text(
        error instanceof DOMException && error.name === "AbortError"
          ? "Local processing stopped. Your previous result remains available."
          : error instanceof Error
            ? error.message
            : String(error),
        !(error instanceof DOMException && error.name === "AbortError"),
      );
    } finally {
      if (active === controller && revision === currentRevision) {
        active = null;
        busy(false);
      }
    }
  }
  async function prepare() {
    if (!source) return;
    const image = source,
      currentRevision = revision;
    const execute = async (signal: AbortSignal) => {
      text("Preparing the image in your browser…");
      if (image.size > 32 * 1024 * 1024)
        throw new Error("Choose an image no larger than 32 MiB.");
      const result = await processImage(image, {}, signal);
      current(signal, currentRevision);
      originalInfo = result.info;
      prepared = result.png;
      node("before-meta").textContent =
        `${originalInfo.width} × ${originalInfo.height} · ${image.name}`;
      node("alpha-field").hidden = !originalInfo.alpha;
      preserve.checked = originalInfo.alpha;
      pictureBefore.src = url("before", image);
      node("compare").hidden = false;
      text("Image ready for local upscaling.");
      validateSize();
      updateZoom();
      localRetry = null;
    };
    localRetry = execute;
    await run(execute);
  }
  function reset(clearFile = true) {
    revision++;
    active?.abort();
    active = null;
    source = null;
    prepared = null;
    originalInfo = null;
    displayed = null;
    localRetry = null;
    urls.forEach((value) => URL.revokeObjectURL(value));
    urls.clear();
    if (clearFile) file.value = "";
    node<HTMLInputElement>("zoom").value = "100";
    for (const key of [
      "compare",
      "result",
      "alpha-field",
      "size-warning",
      "preview-error",
      "result-details",
    ])
      node(key).hidden = true;
    download.hidden = true;
    rawDownload.hidden = true;
    download.removeAttribute("href");
    rawDownload.removeAttribute("href");
    pictureAfter.removeAttribute("src");
    pictureBefore.removeAttribute("src");
    node("after-placeholder").hidden = false;
    pictureAfter.hidden = true;
    for (const key of [
      "after-meta",
      "before-meta",
      "result-version",
      "result-settings",
    ])
      node(key).textContent = "";
    node("after-model").textContent = "Upscaled";
    text("");
    changeModel();
  }
  function updateZoom() {
    const zoom = Number(node<HTMLInputElement>("zoom").value);
    node("zoom-value").textContent = zoom + "%";
    for (const image of [pictureBefore, pictureAfter])
      image.style.width = zoom + "%";
  }
  for (const key of ["before-stage", "after-stage"]) {
    const stage = node(key),
      other = node(key === "before-stage" ? "after-stage" : "before-stage");
    stage.addEventListener("scroll", () => {
      const x =
        stage.scrollLeft / Math.max(1, stage.scrollWidth - stage.clientWidth);
      const y =
        stage.scrollTop / Math.max(1, stage.scrollHeight - stage.clientHeight);
      const left = x * (other.scrollWidth - other.clientWidth),
        top = y * (other.scrollHeight - other.clientHeight);
      if (Math.abs(other.scrollLeft - left) > 1) other.scrollLeft = left;
      if (Math.abs(other.scrollTop - top) > 1) other.scrollTop = top;
    });
  }
  for (const image of [pictureBefore, pictureAfter])
    image.addEventListener("error", () => {
      node("preview-error").hidden = false;
      node("preview-error").textContent =
        "The preview could not be displayed. Available download links still work.";
    });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (
      active ||
      !prepared ||
      !source ||
      !validateSize() ||
      (selected().backend === "webgpu" && !!gpuReason)
    )
      return;
    const snapshot: RunSnapshot = Object.freeze({
      revision,
      model: Object.freeze({ ...selected() }),
      source,
      prepared,
      preserve: preserve.checked,
    });
    let raw: Blob | null = null;
    let version: string | undefined;
    let metrics: Record<string, number> | undefined;
    const execute = async (signal: AbortSignal) => {
      if (!raw) {
        const { upscaleLocally } = await import("./local-upscale/client.js");
        const result = await upscaleLocally(
          snapshot.prepared,
          signal,
          (value) => {
            current(signal, snapshot.revision);
            text(value.message);
            const activity = node<HTMLProgressElement>("activity");
            if (value.total) {
              activity.max = value.total;
              activity.value = value.completed || 0;
            } else activity.removeAttribute("value");
          },
          snapshot.model.backend,
        );
        current(signal, snapshot.revision);
        raw = result.png;
        version = result.version;
        metrics = result.metrics;
      }
      const result = await compose(snapshot, raw, snapshot.preserve, signal);
      ready({ ...result, localVersion: version, localMetrics: metrics });
      raw = null;
      localRetry = null;
    };
    localRetry = execute;
    void run(execute);
  });
  file.addEventListener("change", () => {
    const next = file.files?.[0];
    if (!next || active) return;
    reset(false);
    source = next;
    void prepare();
  });
  model.addEventListener("change", changeModel);
  fresh.addEventListener("click", () => reset());
  cancel.addEventListener("click", () => active?.abort());
  retry.addEventListener("click", () => {
    if (localRetry && !active) void run(localRetry);
  });
  preserve.addEventListener("change", () => {
    const result = displayed;
    if (!result || active) return;
    const keepAlpha = preserve.checked;
    const execute = async (signal: AbortSignal) => {
      ready({
        ...(await compose(result.snapshot, result.raw, keepAlpha, signal)),
        localVersion: result.localVersion,
        localMetrics: result.localMetrics,
      });
      localRetry = null;
    };
    localRetry = execute;
    void run(execute);
  });
  node("zoom").addEventListener("input", updateZoom);
  node("reset-view").addEventListener("click", () => {
    node<HTMLInputElement>("zoom").value = "100";
    updateZoom();
    node("before-stage").scrollTo(0, 0);
    node("after-stage").scrollTo(0, 0);
  });
  window.addEventListener("pagehide", () => reset());
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) reset();
  });
  model.replaceChildren(
    ...MODELS.map((item) => new Option(item.name, item.id)),
  );
  model.value = MODELS[0].id;
  changeModel();
}
