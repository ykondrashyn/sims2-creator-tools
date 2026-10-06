import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
import ts from "typescript";
import * as THREE from "../../tools/body-preview/node_modules/three/build/three.module.js";

// Exercise maintained TypeScript and real Three geometry with deterministic DOM,
// frame and renderer boundaries. These are behavior tests, not GPU benchmarks.
class Element extends EventTarget {
  children = [];
  dataset = {};
  style = {};
  hidden = false;
  clientWidth = 800;
  clientHeight = 600;
  textContent = "";
  attributes = new Map();
  classList = { toggle() {} };
  append(...children) {
    this.children.push(...children);
  }
  replaceChildren(...children) {
    this.children = children;
  }
  setAttribute(name, value) {
    this.attributes.set(name, value);
  }
  getAttribute(name) {
    return this.attributes.get(name);
  }
  closest() {
    return this.hidden ? this : null;
  }
}
class Renderer {
  domElement = new Element();
  renders = 0;
  loop = null;
  setPixelRatio() {}
  setClearColor() {}
  setSize() {}
  setAnimationLoop(fn) {
    this.loop = fn;
  }
  render() {
    this.renders++;
  }
  dispose() {}
}
class Controls {
  target = new THREE.Vector3();
  addEventListener() {}
  update() {}
  dispose() {}
}
function harness(overrides = {}) {
  const document = new EventTarget();
  document.hidden = false;
  document.createElement = () => new Element();
  const frames = new Map();
  let nextFrame = 0;
  const context = vm.createContext({
    console,
    Error,
    structuredClone,
    Blob,
    URL,
    atob,
    devicePixelRatio: 1,
    document,
    CustomEvent,
    requestAnimationFrame(fn) {
      const id = ++nextFrame;
      frames.set(id, fn);
      return id;
    },
    cancelAnimationFrame(id) {
      frames.delete(id);
    },
    ResizeObserver: class {
      observe() {}
      disconnect() {}
    },
    ...overrides,
  });
  const three = {
    ...THREE,
    WebGLRenderer: Renderer,
    OrbitControls: Controls,
    ...overrides.three,
  };
  const modules = new Map();
  const base = fileURLToPath(new URL("../src/", import.meta.url));
  function load(file) {
    const full = path.resolve(base, file);
    if (modules.has(full)) return modules.get(full);
    const module = { exports: {} };
    const code = ts.transpileModule(readFileSync(full, "utf8"), {
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        target: ts.ScriptTarget.ES2022,
      },
    }).outputText;
    const require = (name) =>
      name.endsWith("package-runtime/assets.js")
        ? {
            asset() {
              throw Error("Rendering unit test must not load assets");
            },
            manifest() {},
          }
        : name.endsWith("vendor/three-preview.js")
          ? three
          : load(
              path
                .relative(base, path.resolve(path.dirname(full), name))
                .replace(/\.js$/, ".ts"),
            );
    vm.runInContext(
      `(function(require, module, exports) {${code}\n})`,
      context,
      { filename: full },
    )(require, module, module.exports);
    modules.set(full, module.exports);
    return module.exports;
  }
  return {
    load,
    document,
    frames,
    tick() {
      const callbacks = [...frames.values()];
      frames.clear();
      for (const fn of callbacks) fn(100);
    },
  };
}
function sourceMesh() {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute(
    "position",
    new THREE.BufferAttribute(
      new Float32Array([
        0, 0, 0.5, 0, 0, 1.4, 0, 0, 1.6, 0, 0, 1.8, 0, 0, 1.6, 0, 0, 1.4, 0, 0,
        1.9,
      ]),
      3,
    ),
  );
  const mesh = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial());
  mesh.name = "skin";
  mesh.userData = {
    part: true,
    map: null,
    components: new Uint32Array([0, 0, 0, 1, 2, 1, 3]),
  };
  return mesh;
}
function expectedColors(mesh, roles, neck) {
  const attr = mesh.geometry.attributes.position;
  const values = new Float32Array(attr.count * 3);
  for (let i = 0; i < attr.count; i++) {
    const role =
      roles[`${mesh.name}#${mesh.userData.components?.[i]}`] ||
      roles[mesh.name] ||
      "split";
    const head = role === "head" || (role === "split" && attr.getZ(i) >= neck);
    values.set(head ? [0.98, 0.69, 0.36] : [0.22, 0.73, 0.78], i * 3);
  }
  return values;
}
function simSetup() {
  const h = harness();
  const { SimPreview } = h.load("sim-preview.ts");
  const root = new Element();
  const viewer = new SimPreview(root);
  const mesh = sourceMesh();
  const before = new THREE.Group();
  before.add(mesh);
  viewer.models.set("before", before);
  return { ...h, viewer, root, mesh };
}

test("Sim boundary edits coalesce and reuse exact color and role buffers", () => {
  const h = simSetup(),
    { viewer, mesh } = h;
  viewer.setStage("head");
  const roles = {
    skin: "split",
    "skin#1": "head",
    "skin#2": "body",
    "skin#3": "accessory",
  };
  for (const neck of [1.0, 1.1, 1.5])
    viewer.setMarkers({ neck: [0, 0, 1.5] }, neck, roles);
  assert.equal(h.frames.size, 1);
  assert.equal(mesh.geometry.getAttribute("color"), undefined);
  h.tick();
  const color = mesh.geometry.getAttribute("color"),
    roleBuffer = mesh.userData.partitions.roles;
  assert.deepEqual([...color.array], [...expectedColors(mesh, roles, 1.5)]);
  const version = mesh.material.version,
    uploaded = color.version;
  viewer.setMarkers({ neck: [0.1, 0, 1.5] }, 1.5, { ...roles });
  h.tick();
  assert.equal(color.version, uploaded);
  for (const neck of [1.2, 1.3, 1.7]) viewer.setMarkers({}, neck, roles);
  h.tick();
  assert.equal(mesh.geometry.getAttribute("color"), color);
  assert.equal(mesh.userData.partitions.roles, roleBuffer);
  assert.equal(color.version, uploaded + 1);
  assert.deepEqual(color.updateRanges, [{ start: 6, count: 3 }]);
  assert.equal(mesh.material.version, version);
  assert.deepEqual([...color.array], [...expectedColors(mesh, roles, 1.7)]);
  const afterCrossing = color.version;
  viewer.setMarkers({}, 1.71, roles);
  h.tick();
  assert.equal(color.version, afterCrossing);
  assert.deepEqual([...color.array], [...expectedColors(mesh, roles, 1.71)]);
  roles["skin#2"] = "head";
  viewer.setMarkers({}, 1.7, roles);
  h.tick();
  assert.equal(mesh.geometry.getAttribute("color"), color);
  assert.deepEqual([...color.array], [...expectedColors(mesh, roles, 1.7)]);
  viewer.setStage("markers");
  h.tick();
  assert.equal(mesh.material.vertexColors, false);
  viewer.setStage("head");
  h.tick();
  assert.equal(mesh.geometry.getAttribute("color"), color);
  viewer.dispose();
});

test("Sim guide selection and moves retain geometry and hide other groups", () => {
  const h = simSetup(),
    { viewer } = h;
  viewer.setMarkers(
    { neck: [0, 0, 1.5], pelvis: [0, 0, 0.9], l_wrist: [0.4, 0, 1.4] },
    1.5,
  );
  h.tick();
  const sphere = viewer.markerMeshes.get("neck"),
    geometry = sphere.geometry;
  let disposed = 0;
  geometry.addEventListener("dispose", () => disposed++);
  viewer.setMarkers(
    { neck: [0.1, 0.2, 1.6], pelvis: [0, 0, 0.9], l_wrist: [0.4, 0, 1.4] },
    1.6,
    {},
    "pelvis",
  );
  h.tick();
  assert.equal(viewer.markerMeshes.get("neck"), sphere);
  assert.equal(sphere.geometry, geometry);
  assert.equal(disposed, 0);
  assert.deepEqual(sphere.position.toArray(), [-0.1, 1.6, 0.2]);
  assert.equal(sphere.scale.x, 0.021);
  viewer.setMarkers(viewer.markers, 1.6, {}, "l_wrist", "arms");
  h.tick();
  assert.equal(sphere.visible, false);
  assert.equal(viewer.markerMeshes.get("l_wrist").visible, true);
  viewer.clearModels();
  h.tick();
  assert.equal(disposed, 1);
  assert.equal(viewer.markerMeshes.size, 0);
  viewer.dispose();
});

test("Sim issue attributes survive display toggles and motion pauses while hidden", () => {
  const h = simSetup(),
    { viewer, root, document } = h;
  const after = new THREE.Group();
  after.userData.bones = [];
  const mesh = new THREE.SkinnedMesh(
    sourceMesh().geometry,
    new THREE.MeshStandardMaterial(),
  );
  mesh.userData.issues = new Uint32Array([0, 1, 0, 0, 1, 0, 0]);
  mesh.morphTargetInfluences = [0, 0];
  mesh.morphTargetDictionary = { fatbot: 0, pregbot: 1 };
  after.add(mesh);
  viewer.models.set("after", after);
  viewer.setOptions({ issues: true, motion: "arms", comparison: "after" });
  h.tick();
  const color = mesh.geometry.getAttribute("color"),
    version = mesh.material.version;
  viewer.setOptions({ morph: "fat" });
  assert.equal(mesh.geometry.getAttribute("color"), color);
  assert.equal(mesh.material.version, version);
  assert.deepEqual(mesh.morphTargetInfluences, [1, 0]);
  const animate = viewer.renderer.loop;
  assert.equal(typeof animate, "function");
  root.hidden = true;
  viewer.setVisible(false);
  assert.equal(viewer.renderer.loop, null);
  assert.equal(h.frames.size, 0);
  const count = viewer.renderer.renders;
  animate(1000);
  h.tick();
  assert.equal(viewer.renderer.renders, count);
  root.hidden = false;
  viewer.setVisible(true);
  assert.equal(typeof viewer.renderer.loop, "function");
  document.hidden = true;
  document.dispatchEvent(new Event("visibilitychange"));
  assert.equal(viewer.renderer.loop, null);
  document.hidden = false;
  document.dispatchEvent(new Event("visibilitychange"));
  assert.equal(typeof viewer.renderer.loop, "function");
  viewer.setOptions({ issues: false, motion: "none" });
  h.tick();
  assert.equal(viewer.renderer.loop, null);
  viewer.setOptions({ issues: true });
  h.tick();
  assert.equal(mesh.geometry.getAttribute("color"), color);
  const idle = viewer.renderer.renders;
  h.tick();
  h.tick();
  assert.equal(viewer.renderer.renders, idle);
  viewer.dispose();
  document.dispatchEvent(new Event("visibilitychange"));
  assert.equal(h.frames.size, 0);
});

function bodySetup() {
  const h = harness(),
    { BodyPreview } = h.load("body-preview.ts");
  const counts = { draws: 0, clears: 0, fills: 0, dirty: 0 };
  const context = {
    clearRect() {
      counts.clears++;
    },
    fillRect() {
      counts.fills++;
    },
    drawImage() {
      counts.draws++;
    },
  };
  const viewer = Object.assign(Object.create(BodyPreview.prototype), {
    entries: [],
    layerLabels: new Map(),
    layerList: new Element(),
    revision: 0,
    gender: "am",
    hiddenIds: new Set(),
    bodySelect: {},
    skin: { value: "#dddddd" },
    status: {},
    root: { dataset: {} },
    canvas: { width: 1024, height: 1024, getContext: () => context },
    texture: {
      set needsUpdate(v) {
        if (v) counts.dirty++;
      },
    },
    body: {},
    model: async function () {
      return this.body;
    },
    image: async () => ({}),
    invalidate() {},
  });
  const entries = Array.from({ length: 20 }, (_, i) => ({
    id: `tattoo-${i}`,
    label: "Label",
    layer: i,
    am: new Blob(["image"]),
    af: new Blob(["female"]),
  }));
  return { viewer, entries, counts };
}
test("tattoo label-only changes perform zero compositing and retain controls", async () => {
  const { viewer, entries, counts } = bodySetup();
  await viewer.setEntries(entries);
  const before = { ...counts },
    labels = [...viewer.layerList.children];
  for (let i = 0; i < 3; i++) {
    entries[0].label = `New name ${i}`;
    await viewer.setEntries(entries);
  }
  assert.deepEqual(counts, before);
  assert.deepEqual(viewer.layerList.children, labels);
  assert.equal(
    viewer.layerLabels.get("tattoo-0").textContent,
    "New name 2 · Layer 1",
  );
  entries[0].am = new Blob(["replacement"]);
  await viewer.setEntries(entries);
  assert.equal(counts.draws - before.draws, 20);
  assert.equal(counts.clears - before.clears, 2);
  const draws = counts.draws;
  await viewer.setEntries(entries, { gender: "af" });
  assert.equal(counts.draws - draws, 20);
  viewer.skin.value = "#abcdef";
  await viewer.setEntries(entries);
  assert.equal(counts.draws - draws, 40);
});

test("tattoo ordering changes still composite and rename updates error labels", async () => {
  const { viewer, entries, counts } = bodySetup();
  viewer.image = async (blob) => {
    if (blob === entries[0].am) throw new Error("invalid PNG");
    return {};
  };
  await viewer.setEntries(entries);
  const before = counts.draws;
  entries[0].label = "Broken tattoo";
  await viewer.setEntries(entries);
  assert.equal(counts.draws, before);
  assert.match(viewer.status.textContent, /Broken tattoo: invalid PNG/);
  entries[0].layer = 19;
  entries[19].layer = 0;
  await viewer.setEntries(entries);
  assert.equal(counts.draws, before + 19);
});

function paintingSetup() {
  const group = new THREE.Group();
  for (const name of ["frame", "artwork"]) {
    const mesh = new THREE.Mesh(
      new THREE.PlaneGeometry(),
      new THREE.MeshStandardMaterial(),
    );
    mesh.name = name;
    group.add(mesh);
  }
  const h = harness({
    three: {
      GLTFLoader: class {
        async parseAsync() {
          return { scene: group };
        }
      },
    },
  });
  const { PaintingPreview } = h.load("painting-preview.ts");
  const prepared = [];
  const viewer = Object.assign(Object.create(PaintingPreview.prototype), {
    sequence: 0,
    scene: new THREE.Scene(),
    references: async () => {},
    framed: 0,
    rendered: 0,
    resetView() {
      this.framed++;
    },
    render() {
      this.rendered++;
    },
    async material(data) {
      prepared.push(data);
      return new THREE.MeshStandardMaterial({ map: new THREE.Texture() });
    },
  });
  const appearance = {
    meshes: [{ glb: "" }],
    materials: {
      frame: { image: "frame.png", diffuse: [0.7, 0.8, 0.9], alpha_test: true },
      artwork: { image: "crop-0.png" },
    },
  };
  return { viewer, appearance, prepared, group };
}
test("painting crop updates retain frame material, textures, geometry and framing", async () => {
  const { viewer, appearance, prepared, group } = paintingSetup();
  await viewer.show(appearance, "frame-template", {});
  const frame = group.children[0],
    art = group.children[1];
  const material = frame.material,
    texture = material.map,
    geometry = frame.geometry;
  let disposedFrame = 0,
    disposedArt = 0;
  material.addEventListener("dispose", () => disposedFrame++);
  art.material.addEventListener("dispose", () => disposedArt++);
  for (let i = 1; i <= 2; i++) {
    appearance.materials.artwork.image = `crop-${i}.png`;
    await viewer.show(appearance, "frame-template", {});
  }
  assert.equal(prepared.length, 4);
  assert.equal(frame.material, material);
  assert.equal(frame.material.map, texture);
  assert.equal(frame.geometry, geometry);
  assert.equal(disposedFrame, 0);
  assert.equal(disposedArt, 1);
  assert.equal(viewer.framed, 1);
  appearance.materials.frame.diffuse[0] = 0.5;
  await viewer.show(appearance, "frame-template", {});
  assert.equal(disposedFrame, 1);
  assert.notEqual(frame.material, material);
});

test("painting stale async material results are disposed without replacing current artwork", async () => {
  const { viewer, appearance, group } = paintingSetup();
  await viewer.show(appearance, "frame-template", {});
  let release, started;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  const entered = new Promise((resolve) => {
    started = resolve;
  });
  const stale = new THREE.MeshStandardMaterial({ map: new THREE.Texture() });
  let disposed = 0,
    disposedMap = 0;
  stale.addEventListener("dispose", () => disposed++);
  stale.map.addEventListener("dispose", () => disposedMap++);
  const current = new THREE.MeshStandardMaterial();
  viewer.material = async (data) => {
    if (data.image === "old-crop.png") {
      started();
      await gate;
      return stale;
    }
    return current;
  };
  const old = viewer.show(
    {
      ...appearance,
      materials: {
        ...appearance.materials,
        artwork: { image: "old-crop.png" },
      },
    },
    "frame-template",
    {},
  );
  await entered;
  await viewer.show(
    {
      ...appearance,
      materials: {
        ...appearance.materials,
        artwork: { image: "latest-crop.png" },
      },
    },
    "frame-template",
    {},
  );
  release();
  await old;
  assert.equal(group.children[1].material, current);
  assert.equal(disposed, 1);
  assert.equal(disposedMap, 1);
});

test("object label measurements precede position writes and are cached during orbit", () => {
  const h = harness(),
    { ObjectPreview } = h.load("object-preview.ts");
  const events = [],
    labels = new Map();
  for (const id of ["width", "height", "depth", "reference"]) {
    let hidden = true;
    const el = {
      get hidden() {
        return hidden;
      },
      set hidden(value) {
        hidden = value;
        events.push(`visible:${id}`);
      },
      get offsetWidth() {
        events.push(`read:${id}`);
        return 100;
      },
      get offsetHeight() {
        events.push(`read:${id}`);
        return 20;
      },
      style: {
        set left(value) {
          events.push(`write:${id}`);
        },
        set top(value) {
          events.push(`write:${id}`);
        },
      },
    };
    labels.set(id, {
      el,
      point: { clone: () => ({ project: () => ({ x: 0, y: 0, z: 0 }) }) },
    });
  }
  const viewer = Object.assign(Object.create(ObjectPreview.prototype), {
    frame: 0,
    renderer: new Renderer(),
    labels,
    layout: {},
    options: { dimensions: true },
    select: { value: "converted" },
    stage: { clientWidth: 800, clientHeight: 600 },
  });
  viewer.render();
  viewer.render();
  viewer.render();
  assert.equal(h.frames.size, 1);
  h.tick();
  assert.equal(viewer.renderer.renders, 1);
  assert.equal(events.filter((e) => e.startsWith("read:")).length, 8);
  assert.ok(
    events.findLastIndex((e) => e.startsWith("read:")) <
      events.findIndex((e) => e.startsWith("write:")),
  );
  events.length = 0;
  viewer.render();
  h.tick();
  assert.equal(events.filter((e) => e.startsWith("read:")).length, 0);
  labels.get("width").size = undefined;
  events.length = 0;
  viewer.render();
  h.tick();
  assert.equal(events.filter((e) => e.startsWith("read:")).length, 2);
});

test("creator navigation visibility follows the final panel hidden state", () => {
  const h = harness(),
    { setPanelVisibility } = h.load("shared/navigation.ts");
  const panel = new Element();
  const observed = [];
  panel.addEventListener("creatorvisibilitychange", (event) =>
    observed.push([event.detail.visible, panel.hidden]),
  );
  setPanelVisibility(panel, false);
  setPanelVisibility(panel, true);
  assert.deepEqual(observed, [
    [false, true],
    [true, false],
  ]);
});
