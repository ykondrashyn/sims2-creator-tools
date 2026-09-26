import {
  disposeMaterials,
  type Appearance,
  type AppearanceMaterial,
  type ReferenceAssets,
} from "./shared/scene-types.js";
import * as THREE from "./vendor/three-preview.js";

// Source meshes already carry the game's coordinates. No fitting or elevation.
export class PaintingPreview {
  declare root: HTMLElement;
  declare renderer: THREE.WebGLRenderer;
  declare scene: THREE.Scene<THREE.Object3DEventMap>;
  declare camera: THREE.PerspectiveCamera;
  declare controls: THREE.OrbitControls;
  declare people: Map<any, any>;
  declare options: { mannequin: string };
  declare sequence: number;
  declare resize: ResizeObserver;
  declare model: THREE.Group<THREE.Object3DEventMap>;
  declare key: any;

  constructor(root: HTMLElement) {
    this.root = root;
    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.setClearColor(0xd4d7d0);
    root.replaceChildren(this.renderer.domElement);
    this.scene = new THREE.Scene();
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x858276, 2));
    const light = new THREE.DirectionalLight(0xffffff, 2);
    light.position.set(-3, 5, 4);
    this.scene.add(light);
    const wall = new THREE.Mesh(
      new THREE.BoxGeometry(8, 3, 0.04),
      new THREE.MeshStandardMaterial({ color: 0xc6c8bc, roughness: 1 }),
    );
    wall.position.set(0, 1.5, -0.47);
    this.scene.add(wall);
    const floor = new THREE.GridHelper(8, 8, 0x929589, 0xb7baac);
    floor.position.y = -0.005;
    this.scene.add(floor);
    this.camera = new THREE.PerspectiveCamera(38, 1, 0.01, 100);
    this.controls = new THREE.OrbitControls(
      this.camera,
      this.renderer.domElement,
    );
    this.controls.addEventListener("change", () => this.render());
    this.controls.maxPolarAngle = Math.PI / 2;
    this.people = new Map();
    this.options = { mannequin: "am" };
    this.sequence = 0;
    this.resize = new ResizeObserver(() => {
      const w = root.clientWidth,
        h = root.clientHeight;
      if (!w || !h) return;
      this.renderer.setSize(w, h);
      this.camera.aspect = w / h;
      this.camera.updateProjectionMatrix();
      this.render();
    });
    this.resize.observe(root);
  }
  free(
    group:
      | THREE.Scene<THREE.Object3DEventMap>
      | THREE.Group<THREE.Object3DEventMap>,
  ) {
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
  async references(refs: ReferenceAssets) {
    if (this.people.size) return;
    for (const gender of ["am", "af"] as const) {
      const group = new THREE.Group(),
        { scene } = await new THREE.GLTFLoader().parseAsync(refs[gender], "");
      const def = refs.definition.bodies[gender];
      scene.position.y = def.floor_offset;
      scene.traverse((o) => {
        if (o instanceof THREE.Mesh) {
          disposeMaterials(o.material);
          o.material = new THREE.MeshStandardMaterial({
            color: 0xb4b2a7,
            roughness: 1,
          });
        }
      });
      group.add(scene);
      const head = new THREE.Mesh(
        new THREE.SphereGeometry(1, 20, 16),
        new THREE.MeshStandardMaterial({ color: 0xb4b2a7, roughness: 1 }),
      );
      head.scale.fromArray(def.head.radii);
      head.position.fromArray(def.head.center);
      group.add(head);
      group.position.set(1.35, 0, 0.35);
      this.people.set(gender, group);
      this.scene.add(group);
    }
    this.setOptions(this.options);
  }
  async material(data: AppearanceMaterial) {
    const map = data.image
      ? await new THREE.TextureLoader().loadAsync(data.image)
      : null;
    if (map) {
      map.flipY = false;
      map.colorSpace = THREE.SRGBColorSpace;
      map.wrapS = map.wrapT = THREE.RepeatWrapping;
    }
    return new THREE.MeshStandardMaterial({
      map,
      color: new THREE.Color(...(data.diffuse || [1, 1, 1])),
      roughness: 0.85,
      metalness: 0,
      side: data.double_sided ? THREE.DoubleSide : THREE.FrontSide,
      transparent: data.blend === "blend",
      alphaTest: data.alpha_test ? 0.5 : 0,
    });
  }
  materialKey(data: AppearanceMaterial) {
    return JSON.stringify([
      data.image || null,
      data.diffuse || [1, 1, 1],
      !!data.double_sided,
      data.blend === "blend",
      !!data.alpha_test,
    ]);
  }
  async show(scene: Appearance, key: string, refs: ReferenceAssets) {
    const seq = ++this.sequence;
    await this.references(refs);
    if (seq !== this.sequence) return;
    if (this.key === key && this.model) {
      const changes: any[] = [];
      this.model.traverse((o) => {
        if (o instanceof THREE.Mesh) {
          if (o.name === "b_mesh" || /shadow/i.test(o.name)) return;
          const data = scene.materials[o.name] || {};
          const appearanceKey = this.materialKey(data);
          if (o.userData.appearanceKey === appearanceKey) return;
          changes.push(
            (async () => {
              const m = await this.material(data);
              if (seq !== this.sequence) {
                if ("map" in m)
                  (m as THREE.MeshStandardMaterial).map?.dispose();
                m.dispose();
                return;
              }
              disposeMaterials(o.material, true);
              o.material = m;
              o.userData.appearanceKey = appearanceKey;
            })(),
          );
        }
      });
      await Promise.all(changes);
      if (seq !== this.sequence) return;
      this.render();
      return;
    }
    const model = new THREE.Group();
    for (const mesh of scene.meshes) {
      const b = Uint8Array.from(atob(mesh.glb), (c) => c.charCodeAt(0));
      const loaded = await new THREE.GLTFLoader().parseAsync(b.buffer, "");
      const changes: any[] = [];
      loaded.scene.traverse((o) => {
        if (!(o instanceof THREE.Mesh)) return;
        if (o.name === "b_mesh" || /shadow/i.test(o.name)) {
          o.visible = false;
          return;
        }
        changes.push(
          (async () => {
            disposeMaterials(o.material);
            const data = scene.materials[o.name] || {};
            o.material = await this.material(data);
            o.userData.appearanceKey = this.materialKey(data);
          })(),
        );
      });
      await Promise.all(changes);
      model.add(loaded.scene);
    }
    if (seq !== this.sequence) {
      this.free(model);
      return;
    }
    if (this.model) {
      this.scene.remove(this.model);
      this.free(this.model);
    }
    this.model = model;
    this.key = key;
    this.scene.add(model);
    this.resetView();
  }
  bounds() {
    const box = new THREE.Box3();
    this.model?.traverse((o) => {
      if (o instanceof THREE.Mesh && o.visible) {
        o.geometry.computeBoundingBox();
        box.union(o.geometry.boundingBox.clone().applyMatrix4(o.matrixWorld));
      }
    });
    return box;
  }
  resetView(front = false) {
    if (!this.model) return;
    this.scene.updateMatrixWorld(true);
    const box = this.bounds();
    if (!front && this.options.mannequin !== "none")
      box.union(
        new THREE.Box3().setFromObject(this.people.get(this.options.mannequin)),
      );
    const size = box.getSize(new THREE.Vector3()),
      center = box.getCenter(new THREE.Vector3());
    const distance =
      Math.max(size.y, size.x / Math.max(0.5, this.camera.aspect), 0.8) * 1.7;
    this.controls.target.copy(center);
    this.camera.position
      .copy(center)
      .add(
        new THREE.Vector3(
          front ? 0 : distance * 0.2,
          front ? 0 : distance * 0.07,
          distance,
        ),
      );
    this.controls.update();
    this.render();
  }
  setOptions(options: { mannequin: string }) {
    this.options = { ...this.options, ...options };
    for (const [g, p] of this.people) p.visible = g === this.options.mannequin;
    this.render();
  }
  render() {
    this.renderer.render(this.scene, this.camera);
  }
  dispose() {
    this.sequence++;
    this.resize.disconnect();
    this.controls.dispose();
    this.free(this.scene);
    this.renderer.dispose();
  }
}
