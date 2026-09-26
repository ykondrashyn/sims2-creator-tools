import {artifact, fixturePath} from "./artifacts.mjs";
import fs from "node:fs/promises";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
const root = process.cwd(),
  out = process.env.WASM_PARITY_OUTPUT || root + "/artifacts/wasm-migration",
  fixtures = (process.env.PROJECT_FIXTURE_ROOT || root) + "/artifacts/wasm-migration/fixtures",
  assetRoot = artifact("PACKAGE_RUNTIME_ASSET_ROOT") + "/";
const m = JSON.parse(await fs.readFile(assetRoot + "manifest.json"));
const asset = (name) => fs.readFile(assetRoot + m.assets[name].sha256),
  hash = (b) => createHash("sha256").update(b).digest("hex");
const glue = await import(
  "data:text/javascript;base64," + (await asset("glue")).toString("base64")
);
await glue.default({ module_or_path: await asset("wasm") });
const e = new glue.BrowserEngine();
const call = (op, params = {}) =>
  JSON.parse(e.call(JSON.stringify({ version: 1, op, params })));
const assets = Object.fromEntries(
  [
    "palette",
    "game-meshes",
    "tattoo-overlay",
    "tattoo-controller",
    "tattoo-face",
  ].map((n) => [n, assetRoot + m.assets[n].sha256]),
);
for (const [name, path] of Object.entries(assets))
  e.put_asset(name, await fs.readFile(path));
call("init");
const cases = JSON.parse(await fs.readFile(fixtures + "/color-cases.json"));
for (const t of cases.imports) {
  e.put_asset("curve", await fs.readFile(fixturePath(t.path)));
  assert.deepEqual(
    call("parse_curve", { asset: "curve", filename: t.path.split("/").at(-1) })
      .tables,
    t.tables,
  );
}
for (const path of cases.invalid_files) {
  e.put_asset("curve", await fs.readFile(path));
  assert.throws(() => call("parse_curve", { asset: "curve" }));
}
assert.deepEqual(
  call("normalize_curve", cases.editor).tables,
  cases.editor.tables,
);
e.put_asset("pixels-input", await fs.readFile(fixtures + "/pixels.png"));
const processing = [];
for (const t of cases.processing) {
  const report = call("process_pixels", {
    asset: "pixels-input",
    settings: t.settings,
    tables: Array.from({ length: 3 }, () =>
      Array.from({ length: 256 }, (_, i) => i),
    ),
  });
  processing.push({ ...t, actual: report.sha256 });
  assert.equal(report.sha256, t.sha256, JSON.stringify(t.settings));
}
assert.throws(() => e.call(" ".repeat(256 * 1024 + 1)));
assert.throws(() =>
  call("process_pixels", {
    asset: "pixels-input",
    settings: { base: "Arbitrary texture", black: 1.5 },
    tables: cases.editor.tables,
  }),
);
console.log(
  "47 GIMP mappings, editor composition, malformed curves, 10 full RGBA base preparations match Python",
);
const requests = [{ version: 1, op: "init", params: {} }],
  reports = [];
await fs.mkdir(out + "/wasm-parity", { recursive: true });
async function build(op, params, filename) {
  const started = performance.now();
  const report = call(op, params);
  const bytes = e.take_asset("output");
  assert(bytes.length > 96);
  e.put_asset("candidate", bytes);
  assert.deepEqual(
    call(op.replace("build_", "validate_"), { ...params, asset: "candidate" }),
    report,
  );
  e.drop_asset("candidate");
  await fs.writeFile(out + "/wasm-parity/" + filename, bytes);
  requests.push({ version: 1, op, params, output: filename });
  reports.push({
    filename,
    bytes: bytes.length,
    sha256: hash(bytes),
    milliseconds: Math.round(performance.now() - started),
    report,
  });
}
const bun = m.hair.items.find((i) => i.label === "Bun");
assets.bun = assetRoot + m.assets[bun.asset].sha256;
e.put_asset("bun", await fs.readFile(assets.bun));
let info = call("open_hair", { asset: "bun" });
requests.push({ version: 1, op: "open_hair", params: { asset: "bun" } });
const baseJob = (name, info, colors) => ({
  id: "2222222222224222a222222222222222",
  creator: "Parity",
  hair_name: name,
  texture_encoder: process.env.TEXTURE_ENCODER || "directxtex", refpack_compression: process.env.REFPACK_COMPRESSION !== "false",
  colors,
  custom_colors: [],
  texture_settings: Object.fromEntries(
    info.textures.map((s) => [
      s.id,
      {
        base: name === "Bun" ? "Volatile" : "Arbitrary texture",
        black: 0,
        white: 255,
        gamma: 1,
      },
    ]),
  ),
});
let job = call(
  "prepare_hair_job",
  baseJob(
    "Bun",
    info,
    m.hair.palette.map((c) => c.name),
  ),
);
for (const color of job.colors)
  await build(
    "build_hair",
    { job, color },
    "Bun_" + color.replaceAll(" ", "") + ".package",
  );
console.log("Bun: all 43 selected colors structurally checked");
const own = {
  id: "custom:abcdefabcdefabcdefabcdefabcdefab",
  name: "My Shade",
  bin: 0,
  curve: cases.editor.curve,
};
let customJob = { ...baseJob("Bun", info, [own.id]), custom_colors: [own] };
await build(
  "build_hair",
  { job: call("prepare_hair_job", customJob), color: own.id },
  "Bun_CustomOnly.package",
);
for (const changes of [
  { name: "DepthCharge" },
  { name: "my.name" },
  { name: "A".repeat(49) },
  { bin: 5 },
  { id: "Dynamite" },
])
  assert.throws(() =>
    call("prepare_hair_job", {
      ...customJob,
      custom_colors: [{ ...own, ...changes }],
    }),
  );
const many = Array.from({ length: 16 }, (_, i) => ({
  ...own,
  id: "custom:" + i.toString(16).padStart(32, "0"),
  name: "Added " + i,
}));
assert.equal(
  call("prepare_hair_job", {
    ...customJob,
    colors: [...m.hair.palette.map((c) => c.name), ...many.map((c) => c.id)],
    custom_colors: many,
  }).colors.length,
  59,
);
assert.throws(() =>
  call("prepare_hair_job", {
    ...customJob,
    custom_colors: [...many, { ...own, name: "Seventeenth" }],
  }),
);
for (const t of m.hair.items.filter((i) => i.id !== bun.id)) {
  assets[t.id] = assetRoot + m.assets[t.asset].sha256;
  e.put_asset(t.id, await fs.readFile(assets[t.id]));
  info = call("open_hair", { asset: t.id });
  requests.push({ version: 1, op: "open_hair", params: { asset: t.id } });
  let j = baseJob("Standard", info, ["Dynamite"]);
  j.texture_settings = Object.fromEntries(
    info.textures.map((s) => [s.id, { base: t.input_base }]),
  );
  await build(
    "build_hair",
    { job: call("prepare_hair_job", j), color: "Dynamite" },
    t.id + ".package",
  );
}
console.log(
  "All nine standard hairstyles, custom-only output and 59-color selection limit checked",
);

const rose = ["mesh_rosehair_0124.package", "recolor_3555b7d0_rose72.package"];
for (const n of rose) {
  assets[n] = (process.env.PROJECT_FIXTURE_ROOT || root) + "/artifacts/hair-validation/embedded-bundle/" + n;
  e.put_asset(n, await fs.readFile(assets[n]));
}
const inspected = call("inspect_packages", { files: rose });
assert.equal(inspected.items.length, 1);
assert.equal(inspected.items[0].inspection.textures.length, 5);
assert.deepEqual(
  call("inspect_packages", { files: rose.slice().reverse() }).items,
  inspected.items,
);
assert.throws(() => call("inspect_packages", { files: [rose[0]] }));
assert.throws(() => call("inspect_packages", { files: [rose[1]] }));
e.put_asset("bad.zip", new Uint8Array([1, 2, 3]));
assert.throws(() => call("inspect_packages", { files: ["bad.zip"] }));

const split = JSON.parse(await fs.readFile(fixtures + "/split-mesh.json"));
const splitFiles = [...split.parts, split.recolor, split.second].map(fixturePath);
for (const path of splitFiles)
  e.put_asset(path.split("/").at(-1), await fs.readFile(path));
const splitNames = splitFiles.map((p) => p.split("/").at(-1));
const multi = call("inspect_packages", { files: splitNames });
assert.equal(multi.items.length, 2);
assert(multi.items.every((i) => i.meshes.length === split.parts.length));
assert.deepEqual(
  call("inspect_packages", { files: splitNames.slice().reverse() }).items.sort(
    (a, b) => a.id.localeCompare(b.id),
  ),
  multi.items.slice().sort((a, b) => a.id.localeCompare(b.id)),
);
assert.throws(() => call("inspect_packages", { files: splitNames.slice(1) }));
assert.throws(() => call("inspect_packages", { files: [...rose, rose[0]] }));
assert.throws(() =>
  call("inspect_packages", { files: Array(65).fill("invalid.package") }),
);
e.put_asset("broken.package", new Uint8Array([1, 2, 3]));
assert.throws(() => call("inspect_packages", { files: ["broken.package"] }));
console.log(
  "Multiple recolors and mesh dependencies, stable imports and missing/conflicting input checks passed",
);
info = call("open_hair", { asset: rose[1] });
requests.push({ version: 1, op: "open_hair", params: { asset: rose[1] } });
job = baseJob("Rose", info, [
  "Dynamite",
  "Depth Charge",
  "Incendiary",
  "Explosive", "Shrapnel", "Safety Fuse", "Pyrotechnic", "Land Mine",
  "Fission", "Grenade", "Comburent", "Flash Powder", "Brisance", "Primer", "Molotov",
]);
for (let bin = 0; bin <= 4; bin++) {
  const id = "custom:" + String(bin).padStart(32, "0");
  job.custom_colors.push({
    id,
    name: "Custom " + bin,
    bin,
    curve: cases.editor.curve,
  });
  job.colors.push(id);
}
job = call("prepare_hair_job", job);
assert.equal(
  new Set(job.custom_colors.map((c) => job.identities[c.id].family)).size,
  5,
);
for (const color of job.colors)
  await build(
    "build_hair",
    { job, color },
    "Rose_" + color.replaceAll(":", "_").replaceAll(" ", "") + ".package",
  );
assert(reports.filter(r => r.filename.startsWith("Rose_")).every(r =>
  r.report.textures.length === 5 &&
  r.report.resource_keys.filter(k => k.startsWith("49596978-")).length === 18));
console.log(
  "Rose DBPF 1.2: five textures and eighteen materials, fifteen natural colors and all custom categories checked",
);
for (const gender of ["am", "af"]) {
  assets[gender] = fixtures + "/" + gender + ".png";
  e.put_asset(gender, await fs.readFile(assets[gender]));
}
for (const genders of [["am"], ["af"], ["am", "af"]]) {
  const spec = {
    schema_version: 1,
    bundle: {
      slug: "parity-" + genders.join("-"),
      catalog_name: "Parity tattoos",
      catalog_description: "Two overlapping textures",
    },
    tattoos: genders.map((g, i) => ({
      key: "tattoo-" + g,
      menu_label: "Tattoo " + g,
      menu_order: genders.length - 1 - i,
      layer_order: i,
      assets: { [g]: g },
    })),
  };
  const job = call("prepare_tattoo_job", {
    texture_encoder: process.env.TEXTURE_ENCODER || "directxtex", refpack_compression: process.env.REFPACK_COMPRESSION !== "false",
    id: "3333333333334333a333333333333333",
    spec,
  });
  await build("build_tattoo", { job }, spec.bundle.slug + ".package");
}

for (let i = 0; i < 20; i++)
  for (const gender of ["am", "af"]) {
    const key = `limit-${gender}-${i}`;
    assets[key] = assets[gender];
    e.put_asset(key, await fs.readFile(assets[key]));
  }
const twenty = {
  schema_version: 1,
  bundle: {
    slug: "twenty-tattoos",
    catalog_name: "Twenty tattoos",
    catalog_description: "Twenty overlapping AM and AF layers",
  },
  tattoos: Array.from({ length: 20 }, (_, i) => ({
    key: "tattoo-" + i,
    menu_label: "Tattoo " + i,
    menu_order: 19 - i,
    layer_order: i,
    assets: { am: `limit-am-${i}`, af: `limit-af-${i}` },
  })),
};
await build(
  "build_tattoo",
  {
    job: call("prepare_tattoo_job", {
    texture_encoder: process.env.TEXTURE_ENCODER || "directxtex", refpack_compression: process.env.REFPACK_COMPRESSION !== "false",
      id: "4444444444444444a444444444444444",
      spec: twenty,
    }),
  },
  "twenty-tattoos.package",
);
assert.throws(() =>
  call("prepare_tattoo_job", {
    texture_encoder: process.env.TEXTURE_ENCODER || "directxtex", refpack_compression: process.env.REFPACK_COMPRESSION !== "false",
    id: "4444444444444444a444444444444444",
    spec: {
      ...twenty,
      tattoos: [
        ...twenty.tattoos,
        { ...twenty.tattoos[0], key: "twentyfirst" },
      ],
    },
  }),
);
console.log(
  "Tattoo AM, AF, mixed and 20-entry controller outputs structurally checked",
);
await fs.writeFile(
  out + "/parity-plan.json",
  JSON.stringify({ assets, requests }),
);
execFileSync(
  artifact("NATIVE_RUNTIME"),
  [out + "/parity-plan.json", out + "/native-parity"],
  { stdio: "inherit", timeout: 600000 },
);
for (const r of reports)
  assert.equal(
    hash(await fs.readFile(out + "/native-parity/" + r.filename)),
    r.sha256,
    r.filename,
  );
await fs.writeFile(
  out + "/parity-results.json",
  JSON.stringify(
    {
      engine: m.release,
      checks: {
        imports: 47,
        base_processing: processing,
        packages: reports.length,
        native_wasm_byte_identical: true,
      },
      packages: reports,
    },
    null,
    2,
  ),
);
console.log(
  "PASS",
  reports.length,
  "native and WASM packages are byte-identical",
);
