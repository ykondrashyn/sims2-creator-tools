import { build } from "esbuild";
import fs from "node:fs/promises";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
const root = new URL("..", import.meta.url).pathname;
const outputArg = process.argv.indexOf("--out");
const out = path.resolve(
  outputArg >= 0 ? process.argv[outputArg + 1] : path.join(root, "dist/static"),
);
const source = path.join(root, "web/src");
await fs.mkdir(out, { recursive: true });
async function files(folder) {
  const results = [];
  for (const entry of await fs.readdir(folder, { withFileTypes: true })) {
    const file = path.join(folder, entry.name);
    results.push(...(entry.isDirectory() ? await files(file) : [file]));
  }
  return results.sort();
}
const entryPoints = (await files(source)).filter(
  (p) => p.endsWith(".ts") && !p.endsWith(".d.ts"),
);
const workers = new Set([
  "package-runtime/worker.ts",
  "package-runtime/hash-worker.ts",
  "local-upscale/worker.ts",
]);
const graph = await build({
  entryPoints: entryPoints.filter(
    (file) => !workers.has(path.relative(source, file)),
  ),
  outbase: source,
  outdir: out,
  bundle: true,
  splitting: true,
  minify: true,
  // Keep import.meta.url based hash-worker URLs beside the standalone worker.
  chunkNames: "package-runtime/chunk-[hash]",
  external: ["./vendor/*"],
  format: "esm",
  target: ["chrome109", "firefox109"],
  legalComments: "eof",
  sourcemap: false,
  write: false,
});
for (const file of graph.outputFiles) {
  // External preview imports are relative to each emitted entry or shared chunk.
  const vendor = path.relative(
    path.dirname(file.path),
    path.join(out, "vendor"),
  );
  const prefix = vendor.startsWith(".") ? vendor : "./" + vendor;
  await fs.mkdir(path.dirname(file.path), { recursive: true });
  await fs.writeFile(
    file.path,
    file.text.replaceAll('"./vendor/', `"${prefix}/`),
  );
}
// A blob-loaded worker cannot resolve relative imports. Its bundle is standalone.
for (const entry of workers) {
  const relative = entry.replace(/\.ts$/, "");
  const input = path.join(source, entry);
  try {
    await fs.access(input);
  } catch {
    continue;
  }
  await build({
    entryPoints: [input],
    outfile: path.join(out, relative + ".mjs"),
    bundle: true,
    minify: true,
    format: "esm",
    target: ["chrome109", "firefox109"],
  });
  // Preserve direct .js worker clients as well as content-addressed .mjs clients.
  await fs.copyFile(
    path.join(out, relative + ".mjs"),
    path.join(out, relative + ".js"),
  );
}
// Existing test clients and saved integrations use these stable import names.
for (const file of entryPoints) {
  const rel = path.relative(source, file);
  if (
    rel.endsWith("-preview.ts") ||
    rel.startsWith("package-runtime/") ||
    ["scale-scene.ts", "preview-textures.ts"].includes(rel)
  ) {
    if (
      rel === "package-runtime/worker.ts" ||
      rel === "package-runtime/hash-worker.ts"
    )
      continue;
    await fs.writeFile(
      path.join(out, rel.replace(/\.ts$/, ".mjs")),
      `export * from "./${path.basename(rel, ".ts")}.js";\n`,
    );
  }
}
await fs.cp(path.join(source, "generated"), path.join(out, "generated"), {
  recursive: true,
});
for (const name of ["index-wasm.html", "style.css"])
  await fs.copyFile(path.join(root, "web", name), path.join(out, name));
const env = { ...process.env, PROJECT_VENDOR_OUT: path.join(out, "vendor") };
const ortDist = path.join(root, "node_modules/onnxruntime-web/dist");
await fs.mkdir(path.join(out, "vendor/local-upscale"), { recursive: true });
for (const name of [
  "ort.wasm.min.mjs",
  "ort.webgpu.min.mjs",
  "ort-wasm-simd-threaded.jsep.mjs",
  "ort-wasm-simd-threaded.jsep.wasm",
  "ort-wasm-simd-threaded.mjs",
  "ort-wasm-simd-threaded.wasm",
]) {
  await fs.copyFile(
    path.join(ortDist, name),
    path.join(out, "vendor/local-upscale", name),
  );
}
// Bound the inference heap to the application's existing 1 GiB ceiling.
// The imported memory may have a lower maximum than the module declares.
for (const name of [
  "ort-wasm-simd-threaded.mjs",
  "ort-wasm-simd-threaded.jsep.mjs",
]) {
  const ortGluePath = path.join(out, "vendor/local-upscale", name);
  const ortGlue = await fs.readFile(ortGluePath, "utf8");
  if (ortGlue.split("initial:256,maximum:65536").length !== 2)
    throw new Error("The pinned ONNX Runtime memory declaration changed.");
  await fs.writeFile(
    ortGluePath,
    ortGlue.replace("initial:256,maximum:65536", "initial:256,maximum:16384"),
  );
}
await fs.copyFile(
  path.join(root, "tools/local-upscale/vendor/ORT-ThirdPartyNotices.txt"),
  path.join(out, "vendor/local-upscale/ThirdPartyNotices.txt"),
);
await fs.copyFile(
  path.join(root, "tools/local-upscale/vendor/ORT-LICENSE"),
  path.join(out, "vendor/local-upscale/LICENSE"),
);
for (const name of ["body-preview", "object-optimizer"])
  execFileSync(
    process.execPath,
    [path.join(root, "tools", name, "build.mjs")],
    { cwd: root, env, stdio: "inherit" },
  );
const vendorSourcePath = path.join(out, "vendor/vendor-sources.json");
const vendorSources = JSON.parse(await fs.readFile(vendorSourcePath, "utf8"));
for (const name of [
  "onnxruntime-web",
  "onnxruntime-common",
  "fast-png",
  "iobuffer",
  "pako",
])
  vendorSources.push("../../node_modules/" + name);
await fs.writeFile(
  vendorSourcePath,
  JSON.stringify(vendorSources, null, 2) + "\n",
);
const manifest = {};
for (const file of await files(out)) {
  const bytes = await fs.readFile(file),
    name = path.relative(out, file);
  manifest[name] = {
    sha256: createHash("sha256").update(bytes).digest("hex"),
    size: bytes.length,
    mime: name.endsWith(".html")
      ? "text/html"
      : name.endsWith(".css")
        ? "text/css"
        : /\.[mc]?js$/.test(name)
          ? "text/javascript"
          : name.endsWith(".json")
            ? "application/json"
            : name.endsWith(".wasm")
              ? "application/wasm"
              : "text/plain",
  };
}
console.log(
  JSON.stringify({ static_root: out, files: Object.keys(manifest).length }),
);
