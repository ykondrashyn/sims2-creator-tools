import type { Metadata } from "./package-runtime/types.js";
import {
  disposeScene,
  frameScaleCamera,
  scaleLighting,
  scaleMannequins,
  scaleRoom,
} from "./scale-scene.js";
import { type ReferenceAssets, type Vec3 } from "./shared/scene-types.js";
import * as THREE from "./vendor/three-preview.js";
const world = (p: ArrayLike<number>) => new THREE.Vector3(-p[0], p[2], p[1]);
const game = (p: THREE.Vector3): Vec3 => [-p.x, p.z, p.y];
const axisMatrix = new THREE.Matrix4().set(
  -1,
  0,
  0,
  0,
  0,
  0,
  1,
  0,
  0,
  1,
  0,
  0,
  0,
  0,
  0,
  1,
);
const types = { f32: Float32Array, u32: Uint32Array };
export function typed(
  result: Metadata,
  spec: { type: keyof typeof types; offset: number; length: number },
) {
  return new types[spec.type](result.buffer, spec.offset, spec.length);
}
export type EditMarker = (
  id: string,
  value: number | Vec3,
  commit: boolean,
  before?: number | Vec3,
) => void;
export class SimPreview {
  declare root: HTMLElement;
  declare onEdit: EditMarker;
  declare onSelect: (id: string, kind?: string) => void;
  declare mode: string;
  declare stage: string;
  declare selected: string;
  declare options: {
    environment: string;
    mannequin: string;
    morph: string;
    motion: string;
    comparison: string;
    skeleton: boolean;
    issues?: boolean;
  };
  declare models: Map<any, any>;
  declare maps: Map<any, any>;
  declare markers: Record<string, Vec3>;
  declare markerMeshes: Map<any, any>;
  declare sequence: number;
  declare renderer: THREE.WebGLRenderer;
  declare scene: THREE.Scene<THREE.Object3DEventMap>;
  declare perspective: THREE.PerspectiveCamera;
  declare ortho: THREE.OrthographicCamera;
  declare camera: any;
  declare controls: THREE.OrbitControls;
  declare guides: THREE.Group<THREE.Object3DEventMap>;
  declare skeletonGuide: THREE.Group<THREE.Object3DEventMap>;
  declare neck: THREE.Mesh<
    THREE.PlaneGeometry,
    THREE.MeshBasicMaterial,
    THREE.Object3DEventMap
  >;
  declare ray: THREE.Raycaster;
  declare pointer: THREE.Vector2;
  declare drag: { id: string; before: number | Vec3 } | null;
  declare listeners: [string, EventListener][];
  declare room: THREE.Group;
  declare grid: THREE.GridHelper;
  disabled = false;
  viewStates = new Map<string, Metadata>();
  declare resize: ResizeObserver;
  declare definition: any;
  declare mannequins: Map<any, any>;
  declare hasFramedModel: boolean;
  declare reference: any;
  declare result: any;
  declare targets: any;
  declare neckHeight: any;
  declare roles: Record<string, string>;
  declare markerGroup: string;
  declare framed: boolean;
  private frame = 0;
  private disposed = false;
  private active = true;
  private guidesDirty = false;
  private partitionsDirty = false;
  private rolesKey = "";
  private rolesVersion = 0;
  private animation: ((time: number) => void) | null = null;
  private onVisibility = () => {
    this.updateAnimation();
    this.render();
  };

  constructor(
    root: HTMLElement,
    onEdit: EditMarker = () => {},
    onSelect: (id: string, kind?: string) => void = () => {},
  ) {
    this.root = root;
    this.active = !root.closest("[hidden]");
    this.onEdit = onEdit;
    this.onSelect = onSelect;
    this.mode = "orbit";
    this.stage = "align";
    this.selected = "neck";
    this.options = {
      environment: "room",
      mannequin: "am",
      morph: "normal",
      motion: "none",
      comparison: "before",
      skeleton: true,
    };
    this.models = new Map();
    this.maps = new Map();
    this.markers = {};
    this.markerMeshes = new Map();
    this.sequence = 0;
    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.setClearColor("#20282c");
    root.replaceChildren(this.renderer.domElement);
    this.renderer.domElement.tabIndex = 0;
    this.renderer.domElement.setAttribute(
      "aria-label",
      "Model fitting view. Select a joint, then use arrow keys to move it.",
    );
    this.scene = new THREE.Scene();
    scaleLighting(this.scene);
    Object.assign(this, scaleRoom(this.scene));
    this.perspective = new THREE.PerspectiveCamera(40, 1, 0.01, 1000);
    this.ortho = new THREE.OrthographicCamera(-2, 2, 2, -2, 0.01, 100);
    this.camera = this.perspective;
    this.perspective.position.set(3, 2, 5);
    this.controls = new THREE.OrbitControls(
      this.camera,
      this.renderer.domElement,
    );
    this.controls.target.set(0, 1, 0);
    this.controls.addEventListener("change", () => this.render());
    this.controls.update();
    this.guides = new THREE.Group();
    this.scene.add(this.guides);
    this.skeletonGuide = new THREE.Group();
    this.scene.add(this.skeletonGuide);
    this.neck = new THREE.Mesh(
      new THREE.PlaneGeometry(2.5, 0.018),
      new THREE.MeshBasicMaterial({
        color: 0xf0c56b,
        side: THREE.DoubleSide,
        depthTest: false,
      }),
    );
    this.neck.renderOrder = 5;
    this.neck.visible = false;
    this.scene.add(this.neck);
    this.ray = new THREE.Raycaster();
    this.pointer = new THREE.Vector2();
    this.drag = null;
    this.listeners = [];
    const listen = (
      name: string,
      fn: {
        (e: any): void;
        (e: any): void;
        (e: any): void;
        (e: any): void;
        (e: any): void;
        (this: HTMLCanvasElement, ev: any): any;
      },
    ) => {
      this.renderer.domElement.addEventListener(name, fn);
      this.listeners.push([name, fn]);
    };
    listen("pointerdown", (e: any) => this.pointerDown(e));
    listen("pointermove", (e: any) => this.pointerMove(e));
    listen("pointerup", (e: any) => this.pointerUp(e));
    listen("pointercancel", (e: any) => this.pointerUp(e));
    listen("keydown", (e: any) => this.key(e));
    document.addEventListener("visibilitychange", this.onVisibility);
    this.resize = new ResizeObserver(() => {
      const w = root.clientWidth,
        h = root.clientHeight;
      this.updateAnimation();
      if (!w || !h) return;
      this.renderer.setSize(w, h);
      this.perspective.aspect = w / h;
      this.perspective.updateProjectionMatrix();
      this.ortho.left = (-1.3 * w) / h;
      this.ortho.right = (1.3 * w) / h;
      this.ortho.top = 1.3;
      this.ortho.bottom = -1.3;
      this.ortho.updateProjectionMatrix();
      this.render();
    });
    this.resize.observe(root);
  }
  async references(refs: ReferenceAssets) {
    this.definition = refs.definition;
    for (const g of this.mannequins?.values() || []) {
      this.scene.remove(g);
      disposeScene(g);
    }
    this.mannequins = await scaleMannequins(refs);
    for (const g of this.mannequins.values()) this.scene.add(g);
    this.setOptions();
    if (!this.framed) this.resetView();
  }
  clearModels() {
    this.sequence++;
    this.animation = null;
    this.renderer.setAnimationLoop(null);
    for (const group of this.models.values()) {
      this.scene.remove(group);
      this.freeModel(group);
    }
    this.models.clear();
    for (const map of this.maps.values()) map.dispose();
    this.maps.clear();
    disposeScene(this.guides);
    this.guides.clear();
    this.markerMeshes.clear();
    this.guidesDirty = false;
    disposeScene(this.skeletonGuide);
    this.skeletonGuide.clear();
    this.neck.visible = false;
    this.hasFramedModel = false;
    this.render();
  }
  freeModel(group: THREE.Group<THREE.Object3DEventMap>) {
    group?.traverse((o) => {
      if (!(o instanceof THREE.Mesh || o instanceof THREE.Line)) return;
      o.geometry.dispose();
      if (o.material) {
        const ms = Array.isArray(o.material) ? o.material : [o.material];
        for (const m of ms) m.dispose();
      }
    });
    group?.userData.skeleton?.dispose();
  }
  async show(result: Metadata, kind = "before") {
    const seq = ++this.sequence;
    const group = new THREE.Group();
    group.applyMatrix4(axisMatrix);
    for (const item of result.textures || []) {
      const url = URL.createObjectURL(
        new Blob([item.bytes], { type: "image/png" }),
      );
      try {
        const map = await new THREE.TextureLoader().loadAsync(url);
        map.flipY = false;
        map.colorSpace = THREE.SRGBColorSpace;
        map.wrapS = map.wrapT = THREE.RepeatWrapping;
        this.maps.get(item.part)?.dispose();
        this.maps.set(item.part, map);
      } finally {
        URL.revokeObjectURL(url);
      }
    }
    const r = result.reference;
    let skeleton: THREE.Skeleton | undefined,
      bones: THREE.Bone[] = [];
    if (kind === "after") {
      const inverse = r.bones.map((b: { translation: any; rotation: any }) =>
        new THREE.Matrix4().compose(
          new THREE.Vector3(...b.translation),
          new THREE.Quaternion(...b.rotation),
          new THREE.Vector3(1, 1, 1),
        ),
      );
      const rest = inverse.map(
        (m: {
          clone: () => {
            (): any;
            new (): any;
            invert: { (): any; new (): any };
          };
        }) => m.clone().invert(),
      );
      bones = rest.map(
        (
          m: {
            decompose: (
              arg0: THREE.Vector3,
              arg1: THREE.Quaternion,
              arg2: THREE.Vector3,
            ) => void;
          },
          i: string | number,
        ) => {
          const bone = new THREE.Bone();
          bone.name =
            r.joints.find((j: any[]) => j[1] === i)?.[0] || `joint${i}`;
          const parent = r.parents[i];
          const local =
            parent === null ? m : rest[parent].clone().invert().multiply(m);
          local.decompose(bone.position, bone.quaternion, bone.scale);
          bone.userData.rest = bone.quaternion.clone();
          const worldRotation = new THREE.Quaternion();
          m.decompose(new THREE.Vector3(), worldRotation, new THREE.Vector3());
          bone.userData.axis = (
            bone.name.endsWith("_calf")
              ? new THREE.Vector3(1, 0, 0)
              : new THREE.Vector3(0, 0, 1)
          ).applyQuaternion(worldRotation.invert());
          return bone;
        },
      );
      bones.forEach(
        (b: THREE.Object3D<THREE.Object3DEventMap>, i: string | number) =>
          r.parents[i] === null ? group.add(b) : bones[r.parents[i]].add(b),
      );
      skeleton = new THREE.Skeleton(bones, inverse);
      group.userData.skeleton = skeleton;
      group.userData.bones = bones;
    }
    for (const part of result.parts) {
      const g = new THREE.BufferGeometry();
      for (const [key, size] of [
        ["positions", 3],
        ["normals", 3],
        ["uvs", 2],
      ] as const)
        g.setAttribute(
          { positions: "position", normals: "normal", uvs: "uv" }[key],
          new THREE.BufferAttribute(typed(result, part[key]), size),
        );
      g.setIndex(new THREE.BufferAttribute(typed(result, part.indices), 1));
      if (skeleton) {
        g.setAttribute(
          "skinIndex",
          new THREE.BufferAttribute(
            new Uint16Array(typed(result, part.joints)),
            4,
          ),
        );
        g.setAttribute(
          "skinWeight",
          new THREE.BufferAttribute(typed(result, part.weights), 4),
        );
        g.morphTargetsRelative = true;
        g.morphAttributes.position = part.morphs.map(
          (m: { positions: any; name: string }) => {
            const a = new THREE.BufferAttribute(typed(result, m.positions), 3);
            a.name = m.name;
            return a;
          },
        );
      }
      const material = new THREE.MeshStandardMaterial({
        map: this.maps.get(part.id),
        roughness: 0.85,
        side: part.double_sided ? THREE.DoubleSide : THREE.FrontSide,
        alphaTest: part.cutout ? 0.5 : 0,
      });
      const mesh = skeleton
        ? new THREE.SkinnedMesh(g, material)
        : new THREE.Mesh(g, material);
      mesh.name = part.id;
      mesh.userData.part = true;
      mesh.userData.components = part.components
        ? typed(result, part.components)
        : null;
      mesh.userData.map = material.map;
      mesh.userData.issues = part.issues ? typed(result, part.issues) : null;
      mesh.frustumCulled = false;
      if (skeleton && mesh instanceof THREE.SkinnedMesh)
        mesh.bind(skeleton, new THREE.Matrix4());
      group.add(mesh);
    }
    if (seq !== this.sequence) {
      this.freeModel(group);
      return;
    }
    const previous = this.models.get(kind);
    if (previous) {
      this.scene.remove(previous);
      this.freeModel(previous);
    }
    this.models.set(kind, group);
    this.scene.add(group);
    this.reference = r;
    this.result = result;
    this.targets = result.targets;
    disposeScene(this.skeletonGuide);
    this.skeletonGuide.clear();
    for (let i = 0; i < r.parents.length; i++) {
      const parent = r.parents[i];
      if (parent === null) continue;
      const line = new THREE.Line(
        new THREE.BufferGeometry().setFromPoints([
          world(r.joint_positions[i]),
          world(r.joint_positions[parent]),
        ]),
        new THREE.LineBasicMaterial({
          color: 0x96d9e4,
          transparent: true,
          opacity: 0.6,
          depthTest: false,
        }),
      );
      this.skeletonGuide.add(line);
    }
    this.partitionsDirty = true;
    this.setOptions({ comparison: kind });
    if (!this.hasFramedModel) {
      this.hasFramedModel = true;
      this.resetView();
    }
    this.render();
  }
  setMarkers(
    markers: Record<string, Vec3>,
    neck: number,
    roles: Record<string, string> = {},
    selected = this.selected,
    group = "torso",
  ) {
    // Keep the latest state synchronous for pointer-up and undo capture. Buffer
    // uploads and guide work are deferred to the next visible frame.
    this.markers = structuredClone(markers);
    const rolesKey = JSON.stringify(
      Object.entries(roles).sort(([a], [b]) => a.localeCompare(b)),
    );
    if (rolesKey !== this.rolesKey) {
      this.rolesKey = rolesKey;
      this.rolesVersion++;
      this.roles = { ...roles };
      this.partitionsDirty = true;
    }
    if (neck !== this.neckHeight) this.partitionsDirty = true;
    this.neckHeight = neck;
    this.selected = selected;
    this.markerGroup = group;
    this.guidesDirty = true;
    this.render();
  }
  updateGuides() {
    for (const [id, sphere] of this.markerMeshes) {
      if (id in this.markers) continue;
      this.guides.remove(sphere);
      disposeScene(sphere);
      this.markerMeshes.delete(id);
    }
    for (const [id, p] of Object.entries(this.markers)) {
      const visible =
        this.markerGroup === "torso"
          ? ["neck", "pelvis"].includes(id)
          : this.markerGroup === "arms"
            ? /shoulder|elbow|wrist/.test(id)
            : /hip|knee|ankle|toe/.test(id);
      let sphere = this.markerMeshes.get(id);
      if (!sphere && visible) {
        sphere = new THREE.Mesh(
          new THREE.SphereGeometry(1, 14, 10),
          new THREE.MeshBasicMaterial({ depthTest: false }),
        );
        sphere.userData.marker = id;
        sphere.renderOrder = 10;
        this.guides.add(sphere);
        this.markerMeshes.set(id, sphere);
      }
      if (!sphere) continue;
      sphere.visible = visible;
      sphere.position.copy(world(p));
      sphere.scale.setScalar(id === this.selected ? 0.029 : 0.021);
      sphere.material.color.setHex(id === this.selected ? 0xffd374 : 0x67daca);
    }
    this.neck.position.set(0, this.neckHeight || 1.55, 0);
    this.neck.rotation.y = this.mode === "side" ? Math.PI / 2 : 0;
  }
  colorPartitions() {
    const source = this.models.get("before");
    const show = this.stage === "head";
    source?.traverse(
      (mesh: THREE.Mesh<THREE.BufferGeometry, THREE.MeshStandardMaterial>) => {
        if (!mesh.userData.part) return;
        const map = show ? null : mesh.userData.map;
        if (mesh.material.map !== map || mesh.material.vertexColors !== show) {
          mesh.material.map = map;
          mesh.material.vertexColors = show;
          mesh.material.needsUpdate = true;
        }
        if (!show) return;
        const position = mesh.geometry.attributes.position;
        let cache = mesh.userData.partitions;
        if (!cache) {
          const attribute = new THREE.BufferAttribute(
            new Float32Array(position.count * 3),
            3,
          );
          attribute.setUsage(THREE.DynamicDrawUsage);
          mesh.geometry.setAttribute("color", attribute);
          cache = mesh.userData.partitions = {
            attribute,
            roles: new Uint8Array(position.count),
            rolesVersion: -1,
            neck: undefined,
          };
        }
        const changedRoles = cache.rolesVersion !== this.rolesVersion;
        if (changedRoles) {
          const fallback = this.roles?.[mesh.name] || "split";
          const componentRoles = new Map<number | undefined, number>();
          for (let i = 0; i < position.count; i++) {
            const component = mesh.userData.components?.[i];
            let code = componentRoles.get(component);
            if (code === undefined) {
              const role =
                this.roles?.[`${mesh.name}#${component}`] || fallback;
              code = role === "head" ? 1 : role === "split" ? 0 : 2;
              componentRoles.set(component, code);
            }
            cache.roles[i] = code;
          }
          cache.rolesVersion = this.rolesVersion;
        }
        if (!changedRoles && cache.neck === this.neckHeight) return;
        const colors = cache.attribute.array;
        let firstChanged = position.count;
        let lastChanged = -1;
        for (let i = 0; i < position.count; i++) {
          const role = cache.roles[i];
          if (
            !changedRoles &&
            (role !== 0 ||
              position.getZ(i) >= cache.neck ===
                position.getZ(i) >= this.neckHeight)
          )
            continue;
          const head =
            role === 1 || (role === 0 && position.getZ(i) >= this.neckHeight);
          colors[i * 3] = head ? 0.98 : 0.22;
          colors[i * 3 + 1] = head ? 0.69 : 0.73;
          colors[i * 3 + 2] = head ? 0.36 : 0.78;
          firstChanged = Math.min(firstChanged, i);
          lastChanged = i;
        }
        cache.neck = this.neckHeight;
        if (lastChanged >= firstChanged) {
          cache.attribute.clearUpdateRanges();
          cache.attribute.addUpdateRange(
            firstChanged * 3,
            (lastChanged - firstChanged + 1) * 3,
          );
          cache.attribute.needsUpdate = true;
        }
      },
    );
  }
  setStage(stage: string) {
    this.stage = stage;
    this.partitionsDirty = true;
    this.setOptions();
  }
  setOptions(options = {}) {
    Object.assign(this.options, options);
    const o = this.options;
    this.room.visible = o.environment === "room" && this.mode === "orbit";
    for (const [g, m] of this.mannequins || []) m.visible = g === o.mannequin;
    for (const [k, g] of this.models) g.visible = k === o.comparison;
    this.guides.visible = this.stage === "markers";
    this.neck.visible = this.stage === "head";
    this.skeletonGuide.visible =
      o.skeleton && ["markers", "check"].includes(this.stage);
    const after = this.models.get("after");
    after?.traverse(
      (
        mesh: THREE.SkinnedMesh<
          THREE.BufferGeometry,
          THREE.MeshStandardMaterial
        >,
      ) => {
        if (!mesh.isSkinnedMesh) return;
        mesh.morphTargetInfluences!.fill(0);
        const key =
          o.morph === "fat"
            ? "fatbot"
            : o.morph === "pregnant"
              ? "pregbot"
              : null;
        if (key && mesh.morphTargetDictionary![key] !== undefined)
          mesh.morphTargetInfluences![mesh.morphTargetDictionary![key]] = 1;
        const issues = mesh.userData.issues;
        const show = !!o.issues && !!issues;
        if (mesh.material.vertexColors !== show) {
          mesh.material.vertexColors = show;
          mesh.material.needsUpdate = true;
        }
        if (show && !mesh.geometry.getAttribute("color")) {
          const colors = new Float32Array(issues.length * 3);
          for (let i = 0; i < issues.length; i++)
            colors.set(issues[i] ? [1, 0.25, 0.12] : [1, 1, 1], i * 3);
          mesh.geometry.setAttribute(
            "color",
            new THREE.BufferAttribute(colors, 3),
          );
        }
      },
    );
    const bones = after?.userData.bones || [];
    const rest = () =>
      bones.forEach(
        (b: {
          quaternion: { copy: (arg0: any) => any };
          userData: { rest: any };
        }) => b.quaternion.copy(b.userData.rest),
      );
    rest();
    this.animation =
      o.motion !== "none" && after && o.comparison === "after"
        ? (time) => {
            if (!this.canRender()) {
              this.updateAnimation();
              return;
            }
            rest();
            const bend = (Math.sin(time / 650) + 1) / 2;
            const names =
              o.motion === "head"
                ? ["head"]
                : o.motion === "arms"
                  ? ["l_forearm", "r_forearm"]
                  : ["l_calf", "r_calf"];
            for (const b of bones.filter((candidate: { name: string }) =>
              names.includes(candidate.name),
            )) {
              const axis = b.userData.axis;
              const sign =
                o.motion === "arms" && b.name.startsWith("r_")
                  ? -1
                  : o.motion === "knees"
                    ? -1
                    : 1;
              b.quaternion.multiply(
                new THREE.Quaternion().setFromAxisAngle(
                  axis,
                  o.motion === "head"
                    ? Math.sin(time / 650) * 0.4
                    : bend * 0.8 * sign,
                ),
              );
            }
            this.draw();
          }
        : null;
    this.updateAnimation();
    this.render();
  }
  view(mode: string) {
    if (mode === this.mode) return;
    this.viewStates ||= new Map();
    this.viewStates.set(this.mode, {
      position: this.camera.position.clone(),
      target: this.controls.target.clone(),
      zoom: this.camera.zoom,
    });
    this.mode = mode;
    this.controls.dispose();
    this.camera = mode === "orbit" ? this.perspective : this.ortho;
    const saved = this.viewStates.get(mode);
    if (saved) {
      this.camera.position.copy(saved.position);
      this.camera.zoom = saved.zoom;
    } else if (mode !== "orbit") {
      this.camera.position.set(
        mode === "side" ? 5 : 0,
        1,
        mode === "front" ? 5 : 0,
      );
      this.camera.zoom = 1;
    }
    this.camera.updateProjectionMatrix();
    this.controls = new THREE.OrbitControls(
      this.camera,
      this.renderer.domElement,
    );
    this.controls.target.copy(saved?.target || new THREE.Vector3(0, 1, 0));
    this.controls.enableRotate = mode === "orbit";
    this.controls.addEventListener("change", () => this.render());
    this.controls.update();
    this.setOptions();
    this.neck.rotation.y = mode === "side" ? Math.PI / 2 : 0;
  }
  resetView() {
    if (this.mode !== "orbit") {
      this.ortho.zoom = 1;
      this.ortho.updateProjectionMatrix();
      this.camera.position.set(
        this.mode === "side" ? 5 : 0,
        1,
        this.mode === "front" ? 5 : 0,
      );
      this.controls.target.set(0, 1, 0);
      this.controls.update();
      this.render();
      return;
    }
    const box = new THREE.Box3(
      new THREE.Vector3(-0.7, 0, -0.3),
      new THREE.Vector3(0.7, 1.9, 0.3),
    );
    for (const m of this.models.values())
      if (m.visible) box.union(new THREE.Box3().setFromObject(m));
    for (const g of this.mannequins?.values() || [])
      if (g.visible) box.union(new THREE.Box3().setFromObject(g));
    frameScaleCamera(this.camera, this.controls, box);
    this.framed = true;
    this.render();
  }
  rayAt(e: { clientX: number; clientY: number }) {
    const r = this.renderer.domElement.getBoundingClientRect();
    this.pointer.set(
      ((e.clientX - r.left) / r.width) * 2 - 1,
      (-(e.clientY - r.top) / r.height) * 2 + 1,
    );
    this.ray.setFromCamera(this.pointer, this.camera);
  }
  pointerDown(e: {
    button: number;
    clientX: number;
    clientY: number;
    pointerId: number;
    preventDefault: () => void;
  }) {
    if (this.disabled || e.button !== 0) return;
    this.rayAt(e);
    if (this.stage === "markers" && this.mode !== "orbit") {
      // A constant screen-space target remains usable on narrow touch displays.
      const rect = this.renderer.domElement.getBoundingClientRect();
      const candidates = [...this.markerMeshes.values()]
        .filter((object) => object.visible)
        .map((object) => {
          const p = object.position.clone().project(this.camera);
          const distance = Math.hypot(
            ((p.x + 1) * rect.width) / 2 + rect.left - e.clientX,
            ((1 - p.y) * rect.height) / 2 + rect.top - e.clientY,
          );
          return { object, distance, visible: p.z >= -1 && p.z <= 1 };
        })
        .filter((hit) => hit.visible && hit.distance <= 24)
        .sort((a, b) => a.distance - b.distance);
      const hit = candidates[0];
      if (hit) {
        this.selected = hit.object.userData.marker;
        this.onSelect(this.selected);
        this.drag = {
          id: this.selected,
          before: structuredClone(this.markers[this.selected]),
        };
      }
    } else if (this.stage === "head" && this.mode !== "orbit") {
      if (this.ray.intersectObject(this.neck)[0])
        this.drag = { id: "neck_boundary", before: this.neckHeight };
      else {
        const model = this.models.get("before");
        const hit =
          model &&
          this.ray
            .intersectObject(model, true)
            .find((h) => h.object.userData.part);
        if (hit) {
          const component = hit.object.userData.components?.[hit.face.a];
          this.onSelect(
            component === undefined
              ? hit.object.name
              : `${hit.object.name}#${component}`,
            "part",
          );
        }
      }
    }
    if (this.drag) {
      this.controls.enabled = false;
      this.renderer.domElement.setPointerCapture(e.pointerId);
      e.preventDefault();
    }
  }
  pointerMove(e: any) {
    if (!this.drag) return;
    this.rayAt(e);
    const p =
      this.drag.id === "neck_boundary"
        ? [0, 0, this.neckHeight]
        : this.markers[this.drag.id];
    const normal =
      this.mode === "side"
        ? new THREE.Vector3(1, 0, 0)
        : new THREE.Vector3(0, 0, 1);
    const plane = new THREE.Plane().setFromNormalAndCoplanarPoint(
      normal,
      world(p),
    );
    const point = new THREE.Vector3();
    if (this.ray.ray.intersectPlane(plane, point)) {
      const v = game(point);
      if (this.drag.id === "neck_boundary")
        this.onEdit(this.drag.id, Math.max(0.4, Math.min(1.85, v[2])), false);
      else this.onEdit(this.drag.id, v, false);
    }
  }
  pointerUp(e: { pointerId: number }) {
    if (!this.drag) return;
    this.onEdit(
      this.drag.id,
      this.drag.id === "neck_boundary"
        ? this.neckHeight
        : this.markers[this.drag.id],
      true,
      this.drag.before,
    );
    this.drag = null;
    this.controls.enabled = true;
    if (this.renderer.domElement.hasPointerCapture(e.pointerId))
      this.renderer.domElement.releasePointerCapture(e.pointerId);
  }
  key(e: { key: string; preventDefault: () => void; shiftKey: any }) {
    if (
      this.disabled ||
      !["markers", "head"].includes(this.stage) ||
      !["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(e.key) ||
      this.mode === "orbit"
    )
      return;
    e.preventDefault();
    const step = e.shiftKey ? 0.02 : 0.005;
    if (this.stage === "head") {
      this.onEdit(
        "neck_boundary",
        this.neckHeight + (e.key === "ArrowDown" ? -step : step),
        true,
        this.neckHeight,
      );
      return;
    }
    const p = this.markers[this.selected];
    if (!p) return;
    const q = [...p];
    if (e.key === "ArrowUp" || e.key === "ArrowDown")
      q[2] += e.key === "ArrowUp" ? step : -step;
    else {
      const axis = this.mode === "side" ? 1 : 0;
      q[axis] += e.key === "ArrowRight" ? -step : step;
    }
    this.onEdit(this.selected, q as Vec3, true, [...p]);
  }
  canRender() {
    return (
      !this.disposed &&
      this.active &&
      !document.hidden &&
      !this.root.closest("[hidden]") &&
      this.root.clientWidth > 0 &&
      this.root.clientHeight > 0
    );
  }
  setVisible(visible: boolean) {
    this.active = visible;
    this.updateAnimation();
    this.render();
  }
  updateAnimation() {
    this.renderer.setAnimationLoop(this.canRender() ? this.animation : null);
    if (!this.canRender() && this.frame) {
      cancelAnimationFrame(this.frame);
      this.frame = 0;
    }
  }
  draw() {
    if (!this.canRender()) return;
    if (this.frame) cancelAnimationFrame(this.frame);
    this.frame = 0;
    if (this.guidesDirty) {
      this.guidesDirty = false;
      this.updateGuides();
    }
    if (this.partitionsDirty) {
      this.partitionsDirty = false;
      this.colorPartitions();
    }
    this.renderer.render(this.scene, this.camera);
  }
  render() {
    if (this.frame || !this.canRender()) return;
    this.frame = requestAnimationFrame(() => {
      this.frame = 0;
      this.draw();
    });
  }
  dispose() {
    this.disposed = true;
    if (this.frame) cancelAnimationFrame(this.frame);
    this.frame = 0;
    document.removeEventListener("visibilitychange", this.onVisibility);
    this.sequence++;
    this.renderer.setAnimationLoop(null);
    this.resize.disconnect();
    this.controls.dispose();
    for (const [n, f] of this.listeners)
      this.renderer.domElement.removeEventListener(n, f);
    for (const g of this.models.values()) this.freeModel(g);
    for (const m of this.maps.values()) m.dispose();
    disposeScene(this.room);
    disposeScene(this.grid);
    disposeScene(this.guides);
    disposeScene(this.skeletonGuide);
    disposeScene(this.neck);
    for (const g of this.mannequins?.values() || []) disposeScene(g);
    this.renderer.dispose();
  }
}
