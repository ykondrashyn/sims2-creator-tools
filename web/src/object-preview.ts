import {
  frameScaleCamera,
  scaleLighting,
  scaleMannequins,
  scaleRoom,
} from "./scale-scene.js";
import {
  disposeMaterials,
  type Layout,
  type ObjectPreviewResult,
  type ReferenceAssets,
} from "./shared/scene-types.js";
import * as THREE from "./vendor/three-preview.js";

// All transforms are supplied by the package engine. Display references never
// participate in layout or in generated geometry.
export class ObjectPreview {
  declare root: HTMLElement;
  declare room: THREE.Group;
  declare grid: THREE.GridHelper;
  declare select: HTMLSelectElement;
  declare stage: HTMLDivElement;
  declare note: HTMLParagraphElement;
  declare renderer: THREE.WebGLRenderer;
  declare labels: Map<any, any>;
  declare scene: THREE.Scene<THREE.Object3DEventMap>;
  declare camera: THREE.PerspectiveCamera;
  declare controls: THREE.OrbitControls;
  declare resize: ResizeObserver;
  declare models: Map<any, any>;
  declare mannequins: Map<any, any>;
  declare footprint: THREE.Group<THREE.Object3DEventMap>;
  declare dimensions: THREE.Group<THREE.Object3DEventMap>;
  declare options: {
    environment: string;
    mannequin: string;
    footprint: boolean;
    dimensions: boolean;
  };
  declare current: any;
  declare layout: any;
  declare framed: boolean;
  declare referenceVersion: any;
  declare reference: any;
  declare table: THREE.Group<THREE.Object3DEventMap>;
  declare initialMatrix: THREE.Matrix4;
  private frame = 0;
  private labelResize: ResizeObserver;

  constructor(root: HTMLElement) {
    this.root = root;
    root.replaceChildren();
    const bar = document.createElement("div");
    bar.className = "status-actions";
    this.select = document.createElement("select");
    this.select.setAttribute(
      "aria-label",
      "Preview your object or its template",
    );
    this.select.add(new Option("Your object", "converted"));
    this.select.add(new Option("Template", "original"));
    this.select.addEventListener("change", () => this.display());
    const reset = document.createElement("button");
    reset.type = "button";
    reset.className = "secondary";
    reset.textContent = "Reset view";
    reset.addEventListener("click", () => this.resetView());
    bar.append(this.select, reset);
    this.stage = document.createElement("div");
    this.stage.className = "object-preview-stage";
    this.note = document.createElement("p");
    this.note.className = "build-note";
    root.append(bar, this.stage, this.note);
    try {
      this.renderer = new THREE.WebGLRenderer({ antialias: true });
    } catch {
      root.replaceChildren();
      throw new Error(
        "The 3D preview needs WebGL 2. Package creation is still available.",
      );
    }
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.setClearColor("#20282c");
    this.stage.append(this.renderer.domElement);
    this.labels = new Map();
    for (const id of ["width", "height", "depth", "reference"]) {
      const el = document.createElement("span");
      el.className = "object-dimension-label";
      el.hidden = true;
      this.stage.append(el);
      this.labels.set(id, { el, point: new THREE.Vector3() });
    }
    this.labelResize = new ResizeObserver((entries) => {
      for (const entry of entries) {
        for (const label of this.labels.values()) {
          if (label.el === entry.target) label.size = undefined;
        }
      }
      this.render();
    });
    for (const { el } of this.labels.values()) this.labelResize.observe(el);
    this.scene = new THREE.Scene();
    scaleLighting(this.scene);
    this.camera = new THREE.PerspectiveCamera(40, 1, 0.01, 1000);
    this.controls = new THREE.OrbitControls(
      this.camera,
      this.renderer.domElement,
    );
    this.controls.addEventListener("change", () => this.render());
    this.resize = new ResizeObserver(() => {
      const w = this.stage.clientWidth,
        h = this.stage.clientHeight;
      if (!w || !h) return;
      this.renderer.setSize(w, h);
      this.camera.aspect = w / h;
      this.camera.updateProjectionMatrix();
      this.render();
    });
    this.resize.observe(this.stage);
    this.models = new Map();
    this.mannequins = new Map();
    Object.assign(this, scaleRoom(this.scene));
    this.footprint = new THREE.Group();
    this.scene.add(this.footprint);
    this.dimensions = new THREE.Group();
    this.scene.add(this.dimensions);
    this.options = {
      environment: "room",
      mannequin: "am",
      footprint: true,
      dimensions: true,
    };
  }
  disposeGroup(group: THREE.Group<THREE.Object3DEventMap>) {
    group?.traverse((o) => {
      if (!(o instanceof THREE.Mesh || o instanceof THREE.Line)) return;
      o.geometry.dispose();
      for (const m of o.material
        ? Array.isArray(o.material)
          ? o.material
          : [o.material]
        : []) {
        if ("map" in m) (m as THREE.MeshStandardMaterial).map?.dispose();
        m.dispose();
      }
    });
  }
  clear() {
    for (const group of this.models.values()) {
      this.scene.remove(group);
      this.disposeGroup(group);
    }
    this.models.clear();
    this.current = null;
    this.layout = null;
    this.framed = false;
    this.clearOverlay();
    this.render();
  }
  clearOverlay() {
    for (const group of [this.footprint, this.dimensions]) {
      this.disposeGroup(group);
      group.clear();
    }
    for (const { el } of this.labels.values()) el.hidden = true;
  }
  async references(refs: ReferenceAssets) {
    if (this.referenceVersion === refs.definition.reference_height) return;
    this.referenceVersion = refs.definition.reference_height;
    this.reference = refs.definition;
    for (const g of this.mannequins.values()) {
      this.scene.remove(g);
      this.disposeGroup(g);
    }
    this.mannequins.clear();
    this.mannequins = await scaleMannequins(refs);
    for (const group of this.mannequins.values()) this.scene.add(group);
    if (this.table) {
      this.scene.remove(this.table);
      this.disposeGroup(this.table);
    }
    const { scene: table } = await new THREE.GLTFLoader().parseAsync(
      refs.table!,
      "",
    );
    table.traverse((o) => {
      if (o instanceof THREE.Mesh) {
        o.visible = o.name !== "b_mesh" && !/shadow/i.test(o.name);
        disposeMaterials(o.material);
        o.material = new THREE.MeshStandardMaterial({
          color: 0xaaa08d,
          roughness: 1,
        });
      }
    });
    // The table spans two tiles, center its top under the decoration's tile.
    table.position.x = 0.5;
    this.table = table;
    this.scene.add(table);
    this.setOptions(this.options);
  }
  async show(
    result: ObjectPreviewResult,
    refs: ReferenceAssets,
    options: {
      environment: string;
      mannequin: string;
      footprint: boolean;
      dimensions: boolean;
    },
  ) {
    this.clear();
    await this.references(refs);
    this.options = { ...this.options, ...options };
    for (const name of ["original", "converted"] as const) {
      const group = new THREE.Group();
      for (const mesh of result[name]?.meshes || []) {
        const glb = Uint8Array.from(atob(mesh.glb), (c) => c.charCodeAt(0));
        const loaded = await new THREE.GLTFLoader().parseAsync(glb.buffer, "");
        const pending: any[] = [];
        loaded.scene.traverse((o) => {
          if (!(o instanceof THREE.Mesh)) return;
          // Baked game shadow planes need the game's projection, not a floating
          // opaque plane in a neutral studio. They are retained in source packages.
          if (o.name === "b_mesh" || /shadow/i.test(o.name)) {
            o.visible = false;
            return;
          }
          const data = result[name].materials?.[o.name] || {};
          pending.push(
            (async () => {
              const map = data.image
                ? await new THREE.TextureLoader().loadAsync(data.image)
                : null;
              if (map) {
                map.flipY = false;
                map.colorSpace = THREE.SRGBColorSpace;
              }
              disposeMaterials(o.material);
              o.material = new THREE.MeshStandardMaterial({
                map,
                color: new THREE.Color(...(data.diffuse || [1, 1, 1])),
                roughness: 0.85,
                metalness: 0,
                side: data.double_sided ? THREE.DoubleSide : THREE.FrontSide,
                transparent:
                  data.blend === "blend" || data.blend === "additive",
                alphaTest: data.alpha_test ? 0.5 : 0,
              });
              if (data.blend === "additive") {
                o.material.blending = THREE.AdditiveBlending;
                o.material.depthWrite = false;
              }
            })(),
          );
        });
        await Promise.all(pending);
        group.add(loaded.scene);
      }
      this.models.set(name, group);
    }
    this.initialMatrix = new THREE.Matrix4().fromArray(result.layout.matrix);
    this.select.value = "converted";
    this.updateLayout(result.layout);
    this.display();
    this.resetView();
    this.note.textContent =
      "Drag to orbit, scroll to zoom, right-drag to pan. Each grid square is one game tile. The mannequins are neutral body references with proportional heads.";
  }
  setOptions(options: {
    environment: string;
    mannequin: string;
    footprint: boolean;
    dimensions: boolean;
  }) {
    this.options = { ...this.options, ...options };
    this.room.visible = this.options.environment === "room";
    for (const [gender, group] of this.mannequins)
      group.visible = gender === this.options.mannequin;
    this.footprint.visible = this.options.footprint;
    this.dimensions.visible =
      this.options.dimensions && this.select.value === "converted";
    if (this.table)
      this.table.visible = this.layout?.placement.surface === "tabletop";
    this.render();
  }
  line(points: any[], color: number, group = this.dimensions) {
    const geometry = new THREE.BufferGeometry().setFromPoints(
      points.map((p) => new THREE.Vector3(...p)),
    );
    group.add(
      new THREE.Line(
        geometry,
        new THREE.LineBasicMaterial({
          color,
          depthTest: false,
          transparent: true,
          opacity: 0.9,
        }),
      ),
    );
  }
  updateLayout(layout: Layout) {
    this.layout = layout;
    const elevation =
      layout.placement.surface === "tabletop"
        ? this.reference.table.surface_height
        : 0;
    const converted = this.models.get("converted");
    if (converted) {
      const transform = new THREE.Matrix4()
        .makeTranslation(0, elevation, 0)
        .multiply(new THREE.Matrix4().fromArray(layout.matrix))
        .multiply(this.initialMatrix.clone().invert());
      converted.matrixAutoUpdate = false;
      converted.matrix.copy(transform);
      converted.matrixWorldNeedsUpdate = true;
    }
    const original = this.models.get("original");
    if (original) original.position.y = elevation;
    this.clearOverlay();
    for (const [x, z] of layout.placement.tiles) {
      const tile = new THREE.Mesh(
        new THREE.PlaneGeometry(1, 1),
        new THREE.MeshBasicMaterial({
          color: layout.requires_acknowledgement ? 0xc99446 : 0x4ca692,
          transparent: true,
          opacity: 0.3,
          depthWrite: false,
          side: THREE.DoubleSide,
        }),
      );
      tile.rotation.x = -Math.PI / 2;
      tile.position.set(-x, 0.002, z);
      this.footprint.add(tile);
      this.line(
        [
          [-x - 0.5, 0.006, z - 0.5],
          [-x + 0.5, 0.006, z - 0.5],
          [-x + 0.5, 0.006, z + 0.5],
          [-x - 0.5, 0.006, z + 0.5],
          [-x - 0.5, 0.006, z - 0.5],
        ],
        0xe4c085,
        this.footprint,
      );
    }
    const a = layout.game_min,
      b = layout.game_max;
    const lo = [-b[0], a[2] + elevation, a[1]],
      hi = [-a[0], b[2] + elevation, b[1]];
    const color = layout.requires_acknowledgement ? 0xffba63 : 0xb4ebdf;
    // Outline selection bounds so overhang remains obvious even on irregular meshes.
    const wire = new THREE.LineSegments(
      new THREE.EdgesGeometry(
        new THREE.BoxGeometry(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]),
      ),
      new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.55 }),
    );
    wire.position.fromArray(lo.map((v, i) => (v + hi[i]) / 2));
    this.dimensions.add(wire);
    const specs = {
      width: {
        point: [(lo[0] + hi[0]) / 2, lo[1], hi[2] + 0.12],
        text: `W ${layout.dimensions.width.toFixed(2)}`,
      },
      height: {
        point: [hi[0] + 0.12, (lo[1] + hi[1]) / 2, hi[2]],
        text: `H ${layout.dimensions.height.toFixed(2)}`,
      },
      depth: {
        point: [lo[0] - 0.12, lo[1], (lo[2] + hi[2]) / 2],
        text: `D ${layout.dimensions.depth.toFixed(2)}`,
      },
      reference: {
        point: [3.2, this.reference.reference_height, 0],
        text: "Reference Sim 100%",
      },
    };
    for (const [id, spec] of Object.entries(specs)) {
      const label = this.labels.get(id);
      label.point.fromArray(spec.point);
      if (label.el.textContent !== spec.text) {
        label.el.textContent = spec.text;
        label.size = undefined;
      }
    }
    const h = this.reference.reference_height;
    this.line(
      [
        [3.05, 0, 0],
        [3.2, 0, 0],
        [3.2, h, 0],
        [3.05, h, 0],
      ],
      0xe0e1d4,
    );
    this.setOptions(this.options);
  }
  display() {
    if (this.current) this.scene.remove(this.current);
    this.current = this.models.get(this.select.value);
    if (this.current) this.scene.add(this.current);
    // Comparing the template must not move the camera or change reference scale.
    this.setOptions(this.options);
  }
  resetView() {
    if (!this.layout) return;
    const l = this.layout,
      e =
        l.placement.surface === "tabletop"
          ? this.reference.table.surface_height
          : 0;
    const box = new THREE.Box3(
      new THREE.Vector3(-l.game_max[0], l.game_min[2] + e, l.game_min[1]),
      new THREE.Vector3(-l.game_min[0], l.game_max[2] + e, l.game_max[1]),
    );
    if (this.options.mannequin !== "none")
      box.union(
        new THREE.Box3().setFromObject(
          this.mannequins.get(this.options.mannequin),
        ),
      );
    frameScaleCamera(this.camera, this.controls, box);
    this.framed = true;
    this.render();
  }
  render() {
    if (this.frame) return;
    this.frame = requestAnimationFrame(() => {
      this.frame = 0;
      this.draw();
    });
  }
  draw() {
    const w = this.stage.clientWidth,
      h = this.stage.clientHeight;
    if (!w || !h) return;
    this.renderer.render(this.scene, this.camera);
    const projected = [...this.labels].map(([id, label]) => {
      const p = label.point.clone().project(this.camera);
      const hidden =
        !this.layout ||
        !this.options.dimensions ||
        this.select.value !== "converted" ||
        p.z > 1 ||
        p.z < -1 ||
        Math.abs(p.x) > 0.96 ||
        Math.abs(p.y) > 0.94;
      return { id, label, p, hidden };
    });
    // Reveal labels together before measuring any newly visible label. Keep
    // all metric reads together and ahead of every position write.
    for (const { label, hidden } of projected)
      if (label.el.hidden !== hidden) label.el.hidden = hidden;
    const visible = projected.filter(({ hidden }) => !hidden);
    for (const { label } of visible)
      label.size ||= { w: label.el.offsetWidth, h: label.el.offsetHeight };
    const placed: { x: number; y: number; w: number; h: number }[] = [];
    for (const { id, label, p } of visible) {
      const { w: ew, h: eh } = label.size;
      let x =
          ((p.x + 1) * w) / 2 +
          (id === "height" ? 24 : id === "depth" ? -20 : 0),
        y = ((1 - p.y) * h) / 2 + (id === "width" ? 18 : 0);
      x = Math.max(ew / 2 + 4, Math.min(w - ew / 2 - 4, x));
      y = Math.max(eh / 2 + 4, Math.min(h - eh / 2 - 4, y));
      for (
        let n = 0;
        n < 4 &&
        placed.some(
          (r) =>
            Math.abs(r.x - x) < (r.w + ew) / 2 + 4 &&
            Math.abs(r.y - y) < (r.h + eh) / 2 + 4,
        );
        n++
      )
        y += eh + 5;
      label.el.style.left = `${x}px`;
      label.el.style.top = `${y}px`;
      placed.push({ x, y, w: ew, h: eh });
    }
  }
}
