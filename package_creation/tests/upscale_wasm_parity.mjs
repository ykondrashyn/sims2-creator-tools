// Synthetic native/WASM image parity, using explicit freshly built artifacts.
import fs from "node:fs";
import path from "node:path";
import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";
import { execFileSync } from "node:child_process";
const out = path.resolve(process.argv[2]);
const engineRoot = process.env.TEXTURE_ENGINE_ROOT;
const nativeRoot = process.env.PROJECT_NATIVE_ROOT;
if (!engineRoot || !nativeRoot) throw new Error("Pass explicit fresh engine and native artifact paths.");
fs.mkdirSync(out, { recursive: true });
execFileSync(process.env.PYTHON || ".venv/bin/python", ["-c", `
from PIL import Image, ImageCms
from pathlib import Path
p=Path(${JSON.stringify(out)})
im=Image.new('RGBA',(31,23)); im.putdata([(x*7,y*11,173,(x+y)*13%256) for y in range(23) for x in range(31)])
im.save(p/'alpha.png'); im.save(p/'alpha.webp')
im.convert('RGB').save(p/'source.jpg',quality=100,subsampling=0)
profile=ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
im.save(p/'profile.png',icc_profile=profile)
exif=Image.Exif(); exif[274]=6
im.convert('RGB').save(p/'oriented.jpg',exif=exif,quality=100)
for scale in [2,4]:
 im.resize((31*scale,23*scale)).convert('RGB').save(p/f'output-{scale}.webp',lossless=True)
 im.resize((31*scale,23*scale)).convert('RGB').save(p/f'output-{scale}.jpg',quality=100,subsampling=0)
 im.resize((31*scale,23*scale)).convert('RGB').save(p/f'output-{scale}.png')
im.save(p/'animated.png',save_all=True,append_images=[Image.new('RGBA',im.size,'red')],duration=100,loop=0)
im.save(p/'animated.webp',save_all=True,append_images=[Image.new('RGBA',im.size,'red')],duration=100,loop=0)
im.save(p/'bad-profile.png',icc_profile=b'unsupported profile')
`]);
const module = await import(pathToFileURL(path.join(engineRoot, "engine.js")));
await module.default({ module_or_path: fs.readFileSync(path.join(engineRoot, "engine_bg.wasm")) });
const reports = [];
for (const scale of [2, 4]) for (const name of ["alpha.png", "alpha.webp", "source.jpg", "profile.png", "oriented.jpg"]) {
  for (const format of ["webp", "jpg", "png"]) {
    const folder = path.join(out, name + "-" + scale + "-" + format);
    fs.mkdirSync(folder, { recursive: true });
    const plan = { assets: { source: path.join(out, name), result: path.join(out, "output-" + scale + "." + format) }, requests: [
      { version: 1, op: "upscale_prepare_input", params: { input: "source" }, output: "upload.png" },
      { version: 1, op: "upscale_inspect_output", params: { input: "result", preserve_alpha: true }, output: "restored.png" },
      { version: 1, op: "upscale_png", params: {}, output: "converted.png" },
    ] };
    const planFile = path.join(folder, "plan.json");
    fs.writeFileSync(planFile, JSON.stringify(plan));
    execFileSync(path.join(nativeRoot, "examples/runtime"), [planFile, folder]);
    const engine = new module.BrowserEngine();
    for (const [key, file] of Object.entries(plan.assets)) engine.put_asset(key, fs.readFileSync(file));
    const expected = JSON.parse(fs.readFileSync(path.join(folder, "results.json")));
    for (const [i, request] of plan.requests.entries()) {
      const result = JSON.parse(engine.call(JSON.stringify(request)));
      assert.deepEqual(result, expected[i]);
      assert.deepEqual(Buffer.from(engine.take_asset("output")), fs.readFileSync(path.join(folder, request.output)));
    }
    engine.free();
    reports.push({ source: name, scale, format, native_wasm_bytes_identical: true });
  }
}
const rejected = [];
for (const name of ["animated.png", "animated.webp", "bad-profile.png"]) {
  const engine = new module.BrowserEngine();
  engine.put_asset("source", fs.readFileSync(path.join(out, name)));
  assert.throws(() => engine.call(JSON.stringify({ version: 1, op: "upscale_prepare_input", params: { input: "source" } })));
  engine.free(); rejected.push(name);
}
fs.writeFileSync(path.join(out, "report.json"), JSON.stringify({ passed: true, cases: reports, rejected, live_models: "not tested" }, null, 2));
console.log(`Upscale native/WASM parity passed for ${reports.length} image combinations.`);
