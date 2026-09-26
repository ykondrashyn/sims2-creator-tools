import { asset, manifest } from "./package-runtime/assets.js";
import type { Metadata } from "./package-runtime/types.js";
import {
  compositeTextures,
  decodeTexture,
  visibleLayers,
} from "./preview-textures.js";
import * as THREE from "./vendor/three-preview.js";

const BODY_NAMES: Record<string, string> = {
  am: "Male (AM)",
  af: "Female (AF)",
};

export class BodyPreview {
  declare root: HTMLElement;
  declare entries: Metadata[];
  declare hiddenIds: Set<unknown>;
  declare images: WeakMap<object, any>;
  declare models: Map<string, Promise<THREE.Object3D | null>>;
  declare gender: string;
  declare revision: number;
  declare frame: number;
  declare disposed: boolean;
  declare stage: HTMLElement;
  declare status: HTMLElement;
  declare bodySelect: HTMLSelectElement;
  declare skin: HTMLInputElement;
  declare layerList: HTMLElement;
  declare canvas: HTMLCanvasElement;
  declare renderer: THREE.WebGLRenderer;
  declare scene: THREE.Scene<THREE.Object3DEventMap>;
  declare camera: THREE.PerspectiveCamera;
  declare texture: THREE.CanvasTexture<any>;
  declare material: THREE.MeshStandardMaterial;
  declare controls: THREE.OrbitControls;
  declare contextLost: boolean;
  declare resizeObserver: ResizeObserver;
  declare onVisibility: () => void;
  declare size: THREE.Vector3;
  declare body: THREE.Object3D | null;
  declare measured: boolean;
  private layerLabels = new Map<unknown, HTMLElement>();
  private previewSkin?: string;
  private previewSummary?: {
    gender: string;
    count: number;
    errors: { id: unknown; message: string }[];
  };

  constructor(root: HTMLElement) {
    this.root = root;
    this.entries = [];
    this.hiddenIds = new Set();
    this.images = new WeakMap();
    this.models = new Map();
    this.gender = "am";
    this.revision = 0;
    this.frame = 0;
    this.disposed = false;
    root.innerHTML = `
      <div class="body-preview-toolbar">
        <label>Preview body<select data-body><option value="am">Male (AM)</option><option value="af">Female (AF)</option></select></label>
        <label>Mannequin color<input data-skin type="color" value="#c99b7c"></label>
        <div class="body-preview-views" role="group" aria-label="Camera views">
          <button type="button" class="secondary" data-view="front">Front</button>
          <button type="button" class="secondary" data-view="back">Back</button>
          <button type="button" class="secondary" data-view="left">Left</button>
          <button type="button" class="secondary" data-view="right">Right</button>
          <button type="button" class="secondary" data-view="reset">Reset view</button>
        </div>
      </div>
      <div class="body-preview-stage"></div>
      <p class="body-preview-hint">Drag to rotate. Scroll or pinch to zoom. Right-drag to pan.</p>
      <p class="body-preview-status" role="status" aria-live="polite"></p>
      <fieldset class="body-preview-layers"><legend>Visible tattoos</legend><div data-layers></div></fieldset>
      <p class="build-note">Visibility and mannequin color affect this preview only. Higher layers appear on top. The model uses the TS2 body layout, with neutral lighting.</p>`;
    this.stage = root.querySelector<HTMLElement>(".body-preview-stage")!;
    this.status = root.querySelector<HTMLElement>(".body-preview-status")!;
    this.bodySelect = root.querySelector<HTMLSelectElement>("[data-body]")!;
    this.skin = root.querySelector<HTMLInputElement>("[data-skin]")!;
    this.layerList = root.querySelector<HTMLElement>("[data-layers]")!;
    this.canvas = document.createElement("canvas");
    this.canvas.width = this.canvas.height = 1024;
    compositeTextures(this.canvas, [], this.skin.value);
    try {
      this.renderer = new THREE.WebGLRenderer({
        antialias: true,
        alpha: false,
      });
    } catch {
      root.replaceChildren();
      throw new Error(
        "3D preview needs WebGL 2. Enable browser hardware acceleration or try another browser. Texture conversion and package creation are still available.",
      );
    }
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.NoToneMapping;
    this.renderer.setClearColor("#141714");
    this.renderer.domElement.setAttribute(
      "aria-label",
      "Interactive TS2 body texture preview",
    );
    this.renderer.domElement.setAttribute("role", "img");
    this.stage.append(this.renderer.domElement);
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(36, 1, 0.01, 50);
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0xb3a69c, 2));
    const key = new THREE.DirectionalLight(0xffffff, 1.4);
    key.position.set(2, 3, 4);
    this.scene.add(key);
    const fill = new THREE.DirectionalLight(0xffffff, 0.8);
    fill.position.set(-3, 2, -4);
    this.scene.add(fill);
    this.texture = new THREE.CanvasTexture(this.canvas);
    // glTF stores image coordinates with a top-left origin.
    this.texture.flipY = false;
    this.texture.colorSpace = THREE.SRGBColorSpace;
    this.texture.anisotropy = Math.min(
      8,
      this.renderer.capabilities.getMaxAnisotropy(),
    );
    this.material = new THREE.MeshStandardMaterial({
      map: this.texture,
      roughness: 1,
      metalness: 0,
    });
    this.controls = new THREE.OrbitControls(
      this.camera,
      this.renderer.domElement,
    );
    this.controls.minDistance = 0.35;
    this.controls.maxDistance = 10;
    this.controls.enableDamping = false;
    this.controls.addEventListener("change", () => this.invalidate());
    this.bodySelect.addEventListener("change", () => {
      this.gender = this.bodySelect.value;
      this.updateLayerControls();
      void this.refresh();
    });
    this.skin.addEventListener("input", () => this.refresh());
    root
      .querySelectorAll<HTMLButtonElement>("[data-view]")
      .forEach((button) => {
        button.addEventListener("click", () =>
          this.setView(button.dataset.view),
        );
      });
    this.layerList.addEventListener("change", (event) => {
      const id = (event.target as HTMLInputElement).dataset.layerId;
      if (!id) return;
      if ((event.target as HTMLInputElement).checked) this.hiddenIds.delete(id);
      else this.hiddenIds.add(id);
      void this.refresh();
    });
    this.contextLost = false;
    this.renderer.domElement.addEventListener("webglcontextlost", (event) => {
      event.preventDefault();
      this.contextLost = true;
      this.status.textContent =
        "3D graphics were interrupted. Waiting for the browser to restore the preview.";
      this.root.dataset.state = "error";
    });
    this.renderer.domElement.addEventListener("webglcontextrestored", () => {
      this.contextLost = false;
      void this.refresh();
    });
    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(this.stage);
    this.onVisibility = () => {
      if (!document.hidden) this.resize();
    };
    document.addEventListener("visibilitychange", this.onVisibility);
    this.size = new THREE.Vector3(1.87, 1.65, 0.32);
    this.setView("reset");
    this.resize();
  }

  setEntries(
    entries: Metadata[],
    {
      gender,
      lockGender = false,
    }: { gender?: string; lockGender?: boolean } = {},
  ) {
    if (this.disposed) return;
    const labelsOnly =
      this.entries.length > 0 &&
      this.previewSkin === this.skin.value &&
      (!gender || gender === this.gender) &&
      entries.length === this.entries.length &&
      entries.every((entry, i) => {
        const previous = this.entries[i];
        return (
          entry.id === previous.id &&
          entry.layer === previous.layer &&
          entry.am === previous.am &&
          entry.af === previous.af
        );
      });
    this.entries = entries.map((entry) => ({ ...entry }));
    const ids = new Set(entries.map((entry) => entry.id));
    for (const id of this.hiddenIds)
      if (!ids.has(id)) this.hiddenIds.delete(id);
    if (gender) this.gender = gender;
    this.bodySelect.value = this.gender;
    this.bodySelect.disabled = lockGender;
    if (labelsOnly) {
      for (const entry of this.entries) {
        const label = this.layerLabels.get(entry.id);
        if (label) label.textContent = this.layerLabel(entry);
      }
      this.updateStatus();
      return;
    }
    this.updateLayerControls();
    return this.refresh();
  }

  layerLabel(entry: Metadata) {
    return `${entry.label} · Layer ${entry.layer + 1}${entry[this.gender] ? "" : ` · No ${this.gender.toUpperCase()} texture`}`;
  }

  updateLayerControls() {
    this.layerList.replaceChildren();
    this.layerLabels.clear();
    for (const entry of [...this.entries].sort((a, b) => b.layer - a.layer)) {
      const label = document.createElement("label");
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.dataset.layerId = entry.id;
      checkbox.checked = !this.hiddenIds.has(entry.id);
      checkbox.disabled = !entry[this.gender];
      const text = document.createElement("span");
      text.textContent = this.layerLabel(entry);
      this.layerLabels.set(entry.id, text);
      label.append(checkbox, text);
      this.layerList.append(label);
    }
  }

  async model(gender: string) {
    if (!this.models.has(gender)) {
      const promise = (async () => {
        const blob = await asset(await manifest(), `preview-body:${gender}`);
        const gltf = await new THREE.GLTFLoader().parseAsync(
          await blob.arrayBuffer(),
          "",
        );
        const body = gltf.scene;
        body.traverse((child) => {
          if (!(child instanceof THREE.Mesh)) return;
          for (const material of Array.isArray(child.material)
            ? child.material
            : [child.material])
            material.dispose();
          child.material = this.material;
        });
        const bounds = new THREE.Box3().setFromObject(body);
        const center = bounds.getCenter(new THREE.Vector3());
        body.position.sub(center);
        body.userData.size = bounds.getSize(new THREE.Vector3());
        if (this.disposed) {
          body.traverse((child) => {
            if (child instanceof THREE.Mesh) child.geometry.dispose();
          });
          return null;
        }
        return body;
      })();
      this.models.set(gender, promise);
      promise.catch(() => this.models.delete(gender));
    }
    return this.models.get(gender);
  }

  image(file: Blob) {
    if (!this.images.has(file)) this.images.set(file, decodeTexture(file));
    return this.images.get(file);
  }

  async refresh() {
    if (this.disposed || this.contextLost) return;
    const revision = ++this.revision;
    this.previewSummary = undefined;
    this.previewSkin = this.skin.value;
    const gender = this.gender;
    const layers = visibleLayers(this.entries, gender, this.hiddenIds);
    this.status.textContent = `Preparing ${BODY_NAMES[gender]} preview…`;
    this.root.dataset.state = "loading";
    // Clear the old texture immediately so replaced input is never shown as current.
    compositeTextures(this.canvas, [], this.skin.value);
    this.texture.needsUpdate = true;
    this.invalidate();
    try {
      const [body, results] = await Promise.all([
        this.model(gender),
        Promise.all(
          layers.map(async (entry) => {
            try {
              return { image: await this.image(entry[gender]) };
            } catch (errorCause) {
              const error =
                errorCause instanceof Error
                  ? errorCause
                  : new Error(String(errorCause));
              return { error: { id: entry.id, message: error.message } };
            }
          }),
        ),
      ]);
      if (this.disposed || revision !== this.revision || !body) return;
      if (this.body !== body) {
        if (this.body) this.scene.remove(this.body);
        this.body = body;
        this.scene.add(body);
        this.size.copy(body.userData.size);
      }
      const images = results
        .filter((result) => result.image)
        .map((result) => result.image);
      const errors = results.flatMap((result) =>
        result.error ? [result.error] : [],
      );
      compositeTextures(this.canvas, images, this.skin.value);
      this.texture.needsUpdate = true;
      this.previewSummary = { gender, count: images.length, errors };
      this.updateStatus();
      this.root.dataset.state = errors.length ? "error" : "ready";
      this.invalidate();
    } catch (errorCause) {
      const error =
        errorCause instanceof Error
          ? errorCause
          : new Error(String(errorCause));
      if (this.disposed || revision !== this.revision) return;
      if (this.body) this.scene.remove(this.body);
      this.body = null;
      this.status.textContent = error.message;
      this.root.dataset.state = "error";
      this.invalidate();
    }
  }

  updateStatus() {
    if (!this.previewSummary) return;
    const { gender, count, errors } = this.previewSummary;
    this.status.textContent = `${BODY_NAMES[gender]} · ${count} visible ${count === 1 ? "tattoo" : "tattoos"}.`;
    if (errors.length) {
      const details = errors.map(
        ({ id, message }) =>
          `${this.entries.find((entry) => entry.id === id)?.label}: ${message}`,
      );
      this.status.textContent += ` Skipped invalid textures. ${details.join(" ")}`;
    } else if (!count) {
      this.status.textContent +=
        " Choose a matching TS2 PNG above or enable a tattoo below.";
    }
  }

  setView(view = "reset") {
    const angles: Record<string, number> = {
      front: 0,
      back: Math.PI,
      left: Math.PI / 2,
      right: -Math.PI / 2,
      reset: 0,
    };
    const angle = angles[view] ?? 0;
    const fit =
      (Math.max(this.size.y, this.size.x / this.camera.aspect) /
        (2 * Math.tan((this.camera.fov * Math.PI) / 360))) *
      1.16;
    this.controls.target.set(0, 0, 0);
    this.camera.position.set(Math.sin(angle) * fit, 0, Math.cos(angle) * fit);
    this.controls.update();
    this.invalidate();
  }

  resize() {
    if (this.disposed) return;
    const width = this.stage.clientWidth;
    const height = this.stage.clientHeight;
    if (!width || !height) return;
    const first = !this.measured;
    this.measured = true;
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(width, height, false);
    if (first) this.setView("reset");
    this.invalidate();
  }

  invalidate() {
    if (
      this.disposed ||
      this.contextLost ||
      this.frame ||
      document.hidden ||
      !this.stage.clientWidth
    )
      return;
    this.frame = requestAnimationFrame(() => {
      this.frame = 0;
      if (
        !this.disposed &&
        !this.contextLost &&
        !document.hidden &&
        this.stage.clientWidth
      )
        this.renderer.render(this.scene, this.camera);
    });
  }

  dispose() {
    this.disposed = true;
    this.revision++;
    cancelAnimationFrame(this.frame);
    this.resizeObserver.disconnect();
    document.removeEventListener("visibilitychange", this.onVisibility);
    this.controls.dispose();
    if (this.body) this.scene.remove(this.body);
    for (const promise of this.models.values())
      promise
        .then((body) =>
          body?.traverse((child) => {
            if (child instanceof THREE.Mesh) child.geometry.dispose();
          }),
        )
        .catch(() => {});
    this.models.clear();
    this.entries = [];
    this.images = new WeakMap();
    this.texture.dispose();
    this.material.dispose();
    this.renderer.dispose();
    this.root.replaceChildren();
  }
}
