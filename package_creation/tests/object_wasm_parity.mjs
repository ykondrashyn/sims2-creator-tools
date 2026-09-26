import {artifact} from "./artifacts.mjs";
import fs from "node:fs/promises";
import path from "node:path";
import assert from "node:assert/strict";
import {execFileSync} from "node:child_process";
import {createHash} from "node:crypto";

const root = process.cwd();
const out = artifact("PACKAGE_RUNTIME_ASSET_ROOT", process.argv[2]);
const m = JSON.parse(await fs.readFile(path.join(out, "manifest.json")));
const asset = n => fs.readFile(path.join(out, m.assets[n].sha256));
const glue = await import("data:text/javascript;base64," + (await asset("glue")).toString("base64"));
await glue.default({module_or_path: await asset("wasm")});
const e = new glue.BrowserEngine();
const call = (op, params) => JSON.parse(e.call(JSON.stringify({version: 1, op, params})));
const fixture = path.join(root, "package_creation/templates/Template_OverlayBox.package");
const native = artifact("NATIVE_OBJECT");
const source = await fs.readFile(fixture);
e.put_asset("fixture.package", source);
const profile = {...call("object_profile", {asset: "fixture.package"}), id: "fixture", label: "Test fixture only", kind: "floor-decor", requirements: "Test fixture only"};
const catalog = {schema_version: 1, items: [profile]};
const game = {schema_version: 1, guids: [], names: {}, keys: []};
const work = path.resolve(process.env.OBJECT_PARITY_OUTPUT || "artifacts/object-creator");
await fs.mkdir(work, {recursive: true});
await fs.writeFile(path.join(work, "fixture-catalog.json"), JSON.stringify(catalog));
await fs.writeFile(path.join(work, "fixture-game.json"), JSON.stringify(game));
e.put_asset("object-catalog", new TextEncoder().encode(JSON.stringify(catalog)));
e.put_asset("object-game", new TextEncoder().encode(JSON.stringify(game)));
e.put_asset("object-template", source);
const hash = b => createHash("sha256").update(b).digest("hex");
const assets = {"object-template": fixture, "object-catalog": path.join(work, "fixture-catalog.json"), "object-game": path.join(work, "fixture-game.json")};
const job = {texture_encoder: process.env.TEXTURE_ENCODER || "directxtex", refpack_compression: process.env.REFPACK_COMPRESSION !== "false", id: "12345678901234567890123456789012", creator: "Parity", object_name: "Object", title: "Parity object", description: "Native and WASM comparison", price: 75, mode: "clone"};
const reports = [];
for (const mode of ["clone", "model"]) {
  const j = {...job, mode};
  if (mode === "model") {
    const file = path.join(work, "model.glb");
    execFileSync(path.join(root, ".venv/bin/python"), ["-c", "from package_creation.tests.test_objects import model_fixture,glb;from pathlib import Path;import sys;Path(sys.argv[1]).write_bytes(glb(*model_fixture()))", file], {cwd: root});
    j.model_file = "model.glb";j.scale = 1;j.rotation = 0;
    assets["model.glb"] = file;
    e.put_asset("model.glb", await fs.readFile(file));
  }
  const started = performance.now();
  const report = call("object_build", {job: j});
  const bytes = e.take_asset("output");
  const output = path.join(work, `native-${mode}.package`);
  const expected = JSON.parse(execFileSync(native, {input: JSON.stringify({assets, op: "object_build", params: {job: j}, output}), encoding: "utf8"}));
  assert.equal(hash(bytes), hash(await fs.readFile(output)));
  assert.deepEqual(report, expected);
  call("object_build", {job: j});assert.equal(hash(e.take_asset("output")), hash(bytes));
  await fs.writeFile(path.join(work, `wasm-${mode}.package`), bytes);
  reports.push({mode, sha256: hash(bytes), bytes: bytes.length, milliseconds: performance.now() - started, resources: report.resource_count});
}
await fs.writeFile(path.join(work, "wasm-parity.json"), JSON.stringify(reports, null, 2));
console.log(JSON.stringify(reports, null, 2));
const gameReports = [];
if (m.objects?.items?.length) {
  e.put_asset("object-catalog", await asset("object-catalog"));
  e.put_asset("object-game", await asset("object-game"));
  for (const item of m.objects.items) {
    e.put_asset("selected-object", await asset(item.asset));
    const inspection = call("object_inspect", {files: ["selected-object"], trusted: true});
    // Compare against this release's pinned templates. Historical game-validation
    // files may describe an earlier scene extraction and are not its oracle.
    const normalized = path.join(work, `parity-template-${item.id}.package`);
    const nativeAssets = {
      "object-catalog": path.join(out, m.assets["object-catalog"].sha256),
      "object-game": path.join(out, m.assets["object-game"].sha256),
      "selected-object": path.join(out, m.assets[item.asset].sha256),
      "model.glb": path.join(work, "model.glb"),
    };
    const nativeInspection = JSON.parse(execFileSync(native, {input: JSON.stringify({assets:nativeAssets, op:"object_inspect", params:{files:["selected-object"], trusted:true}, output:normalized, output_asset:"object-template"}), encoding:"utf8"}));
    assert.deepEqual(inspection, nativeInspection);
    nativeAssets["object-template"] = normalized;
    for (const mode of ["clone", ...(item.kind.endsWith("decor") ? ["model"] : [])]) {
      const job = {texture_encoder: process.env.TEXTURE_ENCODER || "directxtex", refpack_compression: process.env.REFPACK_COMPRESSION !== "false", id: "01234567890123456789012345678912", creator: "Validation", object_name: item.id.replaceAll("-", ""), title: "Validation " + item.id, description: "Object validation", price: 70, mode, model_file: "model.glb", scale: 1, rotation: 0};
      const started = performance.now();
      const report = call("object_build", {job});
      const bytes = e.take_asset("output");
      const nativeOutput = path.join(work, `parity-${item.id}-${mode}.package`);
      const expected = JSON.parse(execFileSync(native, {input:JSON.stringify({assets:nativeAssets,op:"object_build",params:{job},output:nativeOutput}),encoding:"utf8"}));
      assert.deepEqual(report, expected);
      assert.equal(hash(bytes), hash(await fs.readFile(nativeOutput)));
      call("object_build", {job});assert.equal(hash(e.take_asset("output")), hash(bytes));
      gameReports.push({template: item.id, mode, bytes: bytes.length, sha256: hash(bytes), milliseconds: performance.now() - started});
    }
  }
  await fs.writeFile(path.join(work, "game-wasm-parity.json"), JSON.stringify(gameReports, null, 2));
  console.log(JSON.stringify(gameReports, null, 2));
}
