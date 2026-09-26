import {
  disposeMaterials,
  type ReferenceAssets,
} from "./shared/scene-types.js";
import * as THREE from "./vendor/three-preview.js";

export function disposeScene(group: THREE.Object3D) {
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
export function scaleLighting(scene: THREE.Scene<THREE.Object3DEventMap>) {
  scene.add(new THREE.HemisphereLight(0xffffff, 0x777a80, 2));
  const key = new THREE.DirectionalLight(0xffffff, 2);
  key.position.set(3, 5, 4);
  scene.add(key);
}
export function scaleRoom(scene: THREE.Scene<THREE.Object3DEventMap>) {
  const room = new THREE.Group();
  scene.add(room);
  const box = (
    w: number | undefined,
    h: number | undefined,
    d: number | undefined,
    x: number,
    y: number,
    z: number | undefined,
    color: number,
  ) => {
    const o = new THREE.Mesh(
      new THREE.BoxGeometry(w, h, d),
      new THREE.MeshStandardMaterial({ color, roughness: 1 }),
    );
    o.position.set(x, y, z);
    room.add(o);
  };
  box(8, 0.04, 8, 0, -0.03, 0, 0x737c78);
  box(8, 3, 0.06, 0, 1.5, -3.5, 0xabb4b2);
  box(0.06, 3, 5, -4, 1.5, 1, 0xa0aaa8);
  box(0.06, 3, 1, -4, 1.5, -3, 0xa0aaa8);
  box(0.06, 0.8, 1, -4, 2.6, -2, 0xa0aaa8);
  const grid = new THREE.GridHelper(16, 16, 0x99aaa6, 0x8a9994);
  grid.position.set(0.5, -0.004, 0.5);
  scene.add(grid);
  return { room, grid };
}
export async function scaleMannequins(refs: ReferenceAssets) {
  const mannequins = new Map();
  for (const gender of ["am", "af"] as const) {
    const group = new THREE.Group();
    const { scene } = await new THREE.GLTFLoader().parseAsync(refs[gender], "");
    const spec = refs.definition.bodies[gender];
    scene.position.y = spec.floor_offset;
    scene.traverse((o) => {
      if (o instanceof THREE.Mesh) {
        disposeMaterials(o.material);
        o.material = new THREE.MeshStandardMaterial({
          color: 0xc8c7c0,
          roughness: 1,
        });
      }
    });
    group.add(scene);
    const head = new THREE.Mesh(
      new THREE.SphereGeometry(1, 20, 16),
      new THREE.MeshStandardMaterial({ color: 0xc8c7c0, roughness: 1 }),
    );
    head.scale.fromArray(spec.head.radii);
    head.position.fromArray(spec.head.center);
    group.add(head);
    group.position.set(2.1, 0, 0);
    mannequins.set(gender, group);
  }
  return mannequins;
}
export function frameScaleCamera(
  camera: THREE.PerspectiveCamera,
  controls: THREE.OrbitControls,
  box: THREE.Box3,
) {
  const size = box.getSize(new THREE.Vector3()),
    center = box.getCenter(new THREE.Vector3());
  const distance =
    Math.max(size.y, size.x / Math.min(camera.aspect, 1.6), size.z, 0.5) * 1.8;
  controls.target.copy(center);
  camera.position
    .copy(center)
    .add(new THREE.Vector3(distance * 0.7, distance * 0.4, distance));
  controls.update();
}
